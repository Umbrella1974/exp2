"""Tests for live raw preview outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from live_raw_stream import LiveRawFrame
from run_live_raw_preview import LiveRawPreviewConfig, run_live_raw_preview


def test_run_live_raw_preview_writes_raw_metrics_and_summary(tmp_path: Path) -> None:
    source = FakeLiveSource([_live_frame(0), _live_frame(1)], bad_json_line_count=1)
    config = LiveRawPreviewConfig(
        out_dir=tmp_path / "preview",
        max_frames=2,
        print_every=0,
    )
    result = run_live_raw_preview(config, source=source)

    raw_lines = (config.out_dir / "raw_frames.jsonl").read_text(encoding="utf-8").splitlines()
    metrics_rows = _read_csv(config.out_dir / "live_metrics.csv")
    summary = json.loads((config.out_dir / "live_summary.json").read_text(encoding="utf-8"))

    assert len(raw_lines) == 2
    assert len(metrics_rows) == 2
    assert metrics_rows[0]["parse_ok"] == "true"
    assert metrics_rows[0]["adapter_ok"] == "true"
    assert metrics_rows[0]["tracker_valid"] == "true"
    assert summary["mode"] == "live_raw_preview"
    assert summary["total_received_frames"] == 3
    assert summary["total_processed_frames"] == 2
    assert summary["bad_json_line_count"] == 1
    assert summary["parse_error_count"] == 1
    assert summary["queue_drop_policy"] == "drop_oldest_when_full"
    assert summary["stop_reason"] == "max_frames"
    assert result.summary == summary


def test_run_live_raw_preview_no_save_raw_jsonl(tmp_path: Path) -> None:
    source = FakeLiveSource([_live_frame(0)])
    config = LiveRawPreviewConfig(
        out_dir=tmp_path / "preview_no_raw",
        max_frames=1,
        print_every=0,
        save_raw_jsonl=False,
    )
    result = run_live_raw_preview(config, source=source)
    assert not (config.out_dir / "raw_frames.jsonl").exists()
    assert result.summary["save_raw_jsonl"] is False
    assert result.summary["warnings"]


def test_run_live_raw_preview_write_session_does_not_fake_trial_outputs(tmp_path: Path) -> None:
    source = FakeLiveSource([_live_frame(0), _live_frame(1)])
    session_dir = tmp_path / "session"
    config = LiveRawPreviewConfig(
        out_dir=tmp_path / "preview_session",
        max_frames=2,
        print_every=0,
        write_session=True,
        session_dir=session_dir,
    )
    run_live_raw_preview(config, source=source)

    session_meta = json.loads((session_dir / "session_meta.json").read_text(encoding="utf-8"))
    trial_summary = json.loads((session_dir / "trial_summary.json").read_text(encoding="utf-8"))
    session_live_summary = json.loads((session_dir / "live_summary.json").read_text(encoding="utf-8"))
    raw_lines = (session_dir / "raw_frames.jsonl").read_text(encoding="utf-8").splitlines()
    device_lines = (session_dir / "device_frames.jsonl").read_text(encoding="utf-8").splitlines()
    processed_lines = (session_dir / "processed_frames.csv").read_text(encoding="utf-8").splitlines()

    assert session_meta["mode"] == "live_raw_preview"
    assert session_meta["is_live_trial"] is False
    assert session_meta["trial_controller_started"] is False
    assert session_meta["processed_frames_are_trial_outputs"] is False
    assert trial_summary["trial_controller_started"] is False
    assert session_live_summary["mode"] == "live_raw_preview"
    assert len(raw_lines) == 2
    assert len(device_lines) == 2
    assert len(processed_lines) == 1


class FakeLiveSource:
    def __init__(
        self,
        frames: list[LiveRawFrame],
        *,
        bad_json_line_count: int = 0,
        dropped_frame_count: int = 0,
    ) -> None:
        self.frames = list(frames)
        self.total_valid_frames = len(frames)
        self.bad_json_line_count = bad_json_line_count
        self.parse_error_count = bad_json_line_count
        self.dropped_frame_count = dropped_frame_count
        self.stop_reason = None
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

    def stop(self, reason: str) -> None:
        self.stopped = True
        self.stop_reason = self.stop_reason or reason

    def stats_snapshot(self):
        return {
            "total_received_frames": self.bad_json_line_count + self.total_valid_frames,
            "parse_error_count": self.parse_error_count,
            "bad_json_line_count": self.bad_json_line_count,
            "dropped_frame_count": self.dropped_frame_count,
            "last_parse_error_message": "fake bad json" if self.bad_json_line_count else "",
            "last_bad_json_preview": "{bad json}" if self.bad_json_line_count else "",
            "stop_reason": self.stop_reason,
        }

def _live_frame(index: int) -> LiveRawFrame:
    receive_time = 100.0 + index * 0.01
    raw = _raw_frame(index)
    return LiveRawFrame(
        frame_index=index,
        raw_frame=raw,
        receive_time_monotonic=receive_time,
        receive_wall_time=1_700_000_000.0 + index * 0.01,
        byte_length=len(json.dumps(raw)),
    )


def _raw_frame(index: int) -> dict:
    timestamp = index * 10.0
    return {
        "timestamp": timestamp,
        "frame": index,
        "combined_monotonic_ms": timestamp + 1.0,
        "skeleton_receive_monotonic_ms": timestamp + 2.0,
        "tracker_receive_monotonic_ms": timestamp + 4.0,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "nodes": [
                    {"id": 4, "position": [-0.005, 0.0, 0.0]},
                    {"id": 9, "position": [0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": [float(index) * 0.01, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
