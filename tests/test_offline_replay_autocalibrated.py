"""Tests for offline autocalibrated replay diagnostics."""

from __future__ import annotations

import json

import numpy as np

from offline_replay_autocalibrated import (
    OfflineReplayConfig,
    build_auto_scene,
    build_autocalibrated_task_coordinate_system,
    collect_valid_trajectory_points,
    load_samples_from_raw_jsonl,
    run_offline_replay,
    write_outputs,
)


def raw_frame(timestamp: float, pinch_x: float, *, subject_end: bool = False) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp * 1000),
        "subject_end": subject_end,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "nodes": [
                    {"id": 4, "position": [pinch_x - 0.005, 0.0, 0.0]},
                    {"id": 9, "position": [pinch_x + 0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def write_raw_jsonl(tmp_path, frames: list[dict]) -> str:
    path = tmp_path / "frames.jsonl"
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return str(path)


def make_config(tmp_path, **overrides) -> OfflineReplayConfig:
    frames = overrides.pop(
        "frames",
        [
            raw_frame(1000.0, 0.0),
            raw_frame(1100.0, 0.1),
            raw_frame(1200.0, 0.2),
            raw_frame(1300.0, 0.3, subject_end=True),
        ],
    )
    defaults = {
        "raw_jsonl": write_raw_jsonl(tmp_path, frames),
        "calibration_frames": 2,
        "out_dir": tmp_path / "out",
    }
    defaults.update(overrides)
    return OfflineReplayConfig(**defaults)


def test_initial_window_calibration_generates_task_coordinate_system(tmp_path) -> None:
    config = make_config(tmp_path, calibration_frames=2)
    records = load_samples_from_raw_jsonl(config)
    points, _, _ = collect_valid_trajectory_points(records)
    calibration = build_autocalibrated_task_coordinate_system(points, config)
    assert calibration.payload["calibration_points_count"] == 2
    assert calibration.payload["origin_world"] == [0.0, 0.0, 0.0]
    assert calibration.payload["x_point_world"] == [0.1, 0.0, 0.0]


def test_calibration_frames_affects_x_axis_estimation_window(tmp_path) -> None:
    frames = [
        raw_frame(1000.0, 0.0),
        raw_frame(1100.0, 0.1),
        raw_frame(1200.0, 0.2),
        raw_frame(1300.0, 5.0),
    ]
    config = make_config(tmp_path, frames=frames, calibration_frames=2)
    records = load_samples_from_raw_jsonl(config)
    points, _, _ = collect_valid_trajectory_points(records)
    calibration = build_autocalibrated_task_coordinate_system(points, config)
    assert calibration.payload["x_point_world"] == [0.1, 0.0, 0.0]
    assert calibration.payload["x_point_world"] != [5.0, 0.0, 0.0]


def test_pca_mode_uses_calibration_window_not_full_trajectory(tmp_path) -> None:
    frames = [
        raw_frame(1000.0, 0.0),
        raw_frame(1100.0, 0.1),
        raw_frame(1200.0, 0.2),
        raw_frame(1300.0, 0.2),
    ]
    frames[-1]["skeletons"][0]["nodes"][0]["position"] = [0.195, 10.0, 0.0]
    frames[-1]["skeletons"][0]["nodes"][1]["position"] = [0.205, 10.0, 0.0]
    config = make_config(
        tmp_path,
        frames=frames,
        calibration_mode="pca",
        calibration_frames=3,
    )
    records = load_samples_from_raw_jsonl(config)
    points, _, _ = collect_valid_trajectory_points(records)
    calibration = build_autocalibrated_task_coordinate_system(points, config)
    assert calibration.payload["x_axis_estimation_method"] == "pca_first_component"
    assert abs(calibration.task_coordinate_system.x_axis_world[1]) < 1e-6


def test_wide_track_mode_runs_complete_replay_and_outputs_summary_fields(tmp_path) -> None:
    config = make_config(tmp_path, scene_mode="wide-track")
    result = run_offline_replay(config)
    assert result.summary["replayed_raw_frames"] == 4
    assert result.summary["raw_subject_end_frame_count"] == 1
    assert result.summary["forced_subject_end_false"] is True
    assert result.summary["timeout_effectively_disabled_for_offline_replay"] is True
    assert result.summary["too_many_detaches_effectively_disabled_for_offline_replay"] is True
    assert result.summary["offline_max_detach_count"] == 1_000_000_000
    assert "generated_contact_enter_count" in result.summary
    assert "slip_active_frame_count" in result.summary
    assert "blocked_frame_count" in result.summary
    assert "large_delta_frame_count" in result.summary
    assert "tracker_invalid_frame_count" in result.summary


def test_scene_modes_generate_expected_track_shapes(tmp_path) -> None:
    config = make_config(tmp_path)
    records = load_samples_from_raw_jsonl(config)
    points, _, _ = collect_valid_trajectory_points(records)
    calibration = build_autocalibrated_task_coordinate_system(points, config)
    task_points = np.vstack(
        [calibration.task_coordinate_system.world_to_task(point) for point in points]
    )

    fitted = build_auto_scene(
        task_points,
        make_config(tmp_path, scene_mode="fitted-corridor", track_width=0.3),
    )
    narrow = build_auto_scene(
        task_points,
        make_config(tmp_path, scene_mode="narrow-corridor", narrow_track_width=0.07),
    )
    assert fitted.payload["track_bounds"]["size"][1] == 0.3
    assert narrow.payload["track_bounds"]["size"][1] == 0.07
    assert narrow.payload["corridor_y_center"] == 0.0


def test_outputs_include_calibration_scene_summary_and_csvs(tmp_path) -> None:
    config = make_config(tmp_path)
    result = run_offline_replay(config)
    write_outputs(result, config.out_dir)
    assert (config.out_dir / "frames.csv").exists()
    assert (config.out_dir / "events.csv").exists()
    calibration = json.loads((config.out_dir / "calibration_auto.json").read_text(encoding="utf-8"))
    scene = json.loads((config.out_dir / "scene_auto.json").read_text(encoding="utf-8"))
    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    assert "calibration_points_count" in calibration
    assert "origin_world" in calibration
    assert "x_point_world" in calibration
    assert "block_center_task" in scene
    assert "track_bounds" in scene
    assert summary["valid_pinch_frames"] >= 1
