"""Tests for MVP live visual preview runner."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from analyze_session import analyze_session
from calibration_geometry import build_axes_from_table_lines
from calibration_io import FormalCalibration, PlaneFitRecord, save_calibration
from calibration_sampling import build_calibration_line_record, build_calibration_point_record
from live_raw_stream import LiveRawFrame
from run_live_trial_visual_preview import (
    LiveTrialVisualPreviewConfig,
    run_live_trial_visual_preview,
)


def test_run_live_trial_visual_preview_writes_outputs_and_session(tmp_path: Path) -> None:
    calibration_path = _write_calibration(tmp_path / "calibration.json")
    map_path = _write_map_config(tmp_path / "map.json")
    source = FakeLiveSource([_live_frame(index) for index in range(5)])
    config = LiveTrialVisualPreviewConfig(
        calibration_json=calibration_path,
        map_config=map_path,
        out_dir=tmp_path / "preview",
        max_frames=5,
        print_every=0,
        show_visual=False,
        write_session=True,
        pinch_grab_threshold=0.03,
        pinch_release_threshold=0.04,
        slip_motion_threshold=0.0001,
    )

    result = run_live_trial_visual_preview(config, source=source)

    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    metrics_rows = _read_csv(config.out_dir / "live_metrics.csv")
    session_dir = config.out_dir / "session"

    assert result.summary == summary
    assert summary["mode"] == "live_visual_preview"
    assert summary["run_stop_reason"] == "max_frames"
    assert summary["is_live_trial"] is True
    assert summary["haptic_hardware_enabled"] is False
    assert "logical_haptic_label_counts" in summary
    assert summary["engine_config"]["pinch_grab_threshold"] == 0.03
    assert len(metrics_rows) == 5
    assert (session_dir / "processed_frames.csv").exists()
    assert (session_dir / "haptic.csv").exists()

    analysis = analyze_session(session_dir=session_dir, no_plots=True, overwrite=True)
    assert analysis["session_dir"] == str(session_dir)

    haptic_rows = _read_csv(session_dir / "haptic.csv")
    assert "logical_haptic_label" in haptic_rows[0]["details_json"]


def test_run_live_trial_visual_preview_can_skip_session(tmp_path: Path) -> None:
    calibration_path = _write_calibration(tmp_path / "calibration.json")
    map_path = _write_map_config(tmp_path / "map.json")
    source = FakeLiveSource([_live_frame(0), _live_frame(1)])
    config = LiveTrialVisualPreviewConfig(
        calibration_json=calibration_path,
        map_config=map_path,
        out_dir=tmp_path / "preview_no_session",
        max_frames=2,
        print_every=0,
        show_visual=False,
        write_session=False,
    )

    run_live_trial_visual_preview(config, source=source)

    assert (config.out_dir / "summary.json").exists()
    assert (config.out_dir / "live_metrics.csv").exists()
    assert not (config.out_dir / "session").exists()


class FakeLiveSource:
    def __init__(self, frames: list[LiveRawFrame]) -> None:
        self.frames = list(frames)
        self.total = len(frames)
        self.stop_reason = None
        self.stopped = False
        self.dropped_frame_count = 0
        self.parse_error_count = 0
        self.bad_json_line_count = 0

    def start(self) -> None:
        self.stopped = False

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame | None:
        del timeout
        if self.frames:
            return self.frames.pop(0)
        self.stopped = True
        self.stop_reason = self.stop_reason or "client_disconnected"
        return None

    def queue_size(self) -> int:
        return len(self.frames)

    def stats_snapshot(self) -> dict:
        return {
            "total_received_frames": self.total,
            "parse_error_count": self.parse_error_count,
            "bad_json_line_count": self.bad_json_line_count,
            "dropped_frame_count": self.dropped_frame_count,
            "stop_reason": self.stop_reason,
        }


def _live_frame(index: int) -> LiveRawFrame:
    raw = _raw_frame(index)
    return LiveRawFrame(
        frame_index=index,
        raw_frame=raw,
        receive_time_monotonic=100.0 + index * 0.01,
        receive_wall_time=1_700_000_000.0 + index * 0.01,
        byte_length=len(json.dumps(raw)),
    )


def _raw_frame(index: int) -> dict:
    position = [index * 0.03, 0.0, 0.0]
    return {
        "timestamp": index * 10.0,
        "frame": index,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "side": "left",
                "nodes": [
                    {"id": 4, "position": [-0.005, 0.0, 0.0]},
                    {"id": 9, "position": [0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": position,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def _write_map_config(path: Path) -> Path:
    payload = {
        "map_id": "test_map",
        "description": "unit test map",
        "coordinate_space": "task",
        "unit": "m",
        "block_initial_center_task": [0.0, 0.0, 0.0],
        "block_size": [0.3, 0.3, 0.3],
        "track_boxes": [
            {
                "id": "main",
                "order": 0,
                "min": [-1.0, -1.0, -1.0],
                "max": [1.0, 1.0, 1.0],
            }
        ],
        "target_region": {
            "id": "target",
            "min": [0.6, -0.2, -0.2],
            "max": [0.9, 0.2, 0.2],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_calibration(path: Path) -> Path:
    calibration = _valid_calibration()
    save_calibration(calibration, path)
    return path


def _valid_calibration() -> FormalCalibration:
    origin_points = [[0.0, 0.0, 0.0] for _ in range(10)]
    long_points = [[index / 9.0, 0.0, 0.0] for index in range(10)]
    width_points = [[0.0, index / 9.0, 0.0] for index in range(10)]
    diagonal_points = [[index / 9.0, index / 9.0, 0.0] for index in range(10)]
    origin = build_calibration_point_record(
        "origin",
        origin_points,
        source="tracker_position_world",
        time_start=0.0,
        time_end=9.0,
        save_points=True,
    )
    long_line = build_calibration_line_record(
        "long_axis_line",
        long_points,
        source="tracker_position_world",
        time_start=10.0,
        time_end=19.0,
    )
    width_line = build_calibration_line_record(
        "width_axis_line",
        width_points,
        source="tracker_position_world",
        time_start=20.0,
        time_end=29.0,
    )
    diagonal_line = build_calibration_line_record(
        "diagonal_line",
        diagonal_points,
        source="tracker_position_world",
        time_start=30.0,
        time_end=39.0,
    )
    axes = build_axes_from_table_lines(
        origin.mean_world,
        long_line,
        width_line,
        diagonal_line,
        up_hint=[0.0, 0.0, 1.0],
    )
    plane_fit = PlaneFitRecord(
        centroid_world=axes["plane_fit"]["centroid_world"],
        normal_world=axes["plane_fit"]["normal_world"],
        rmse_m=axes["plane_fit"]["rmse_m"],
        plane_fit_rmse_m=axes["plane_fit"]["rmse_m"],
        max_abs_distance_m=axes["plane_fit"]["max_abs_distance_m"],
        sample_count=axes["plane_fit"]["sample_count"],
        source_labels=["long_axis_line", "width_axis_line", "diagonal_line"],
        singular_values=list(axes["plane_fit"].get("singular_values", [])),
    )
    return FormalCalibration(
        calibration_id="test_calibration",
        created_at="2026-01-01T00:00:00+00:00",
        point_source="tracker_position_world",
        origin_world=origin.mean_world,
        x_axis_world=axes["x_axis_world"],
        y_axis_world=axes["y_axis_world"],
        z_axis_world=axes["z_axis_world"],
        up_axis_world=axes["up_axis_world"],
        origin_record=origin,
        long_line=long_line,
        width_line=width_line,
        diagonal_line=diagonal_line,
        plane_fit=plane_fit,
        quality={**axes["quality"], "calibration_quality_status": "ok"},
        metadata={"source": "unit_test"},
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
