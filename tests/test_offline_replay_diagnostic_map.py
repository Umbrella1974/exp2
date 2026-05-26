"""Tests for trajectory-aligned diagnostic map replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import analyze_session
from map_config import validate_map_config
from offline_replay_autocalibrated import (
    OfflineReplayConfig,
    build_diagnostic_map_scene,
    generate_trajectory_aligned_diagnostic_map,
    run_offline_replay,
    write_outputs,
)


def test_diagnostic_map_replay_writes_session_and_summary(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        diagnostic_map=True,
        diagnostic_map_frames=3,
        diagnostic_map_shape="l_shape",
        diagnostic_map_turn="left",
        write_session=True,
        session_dir=session_dir,
    )

    result = run_offline_replay(config)
    write_outputs(result, config.out_dir)

    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    scene = json.loads((config.out_dir / "scene_auto.json").read_text(encoding="utf-8"))
    trial_config = json.loads((session_dir / "trial_config.json").read_text(encoding="utf-8"))
    trial_summary = json.loads((session_dir / "trial_summary.json").read_text(encoding="utf-8"))
    session_meta = json.loads((session_dir / "session_meta.json").read_text(encoding="utf-8"))
    calibration = json.loads((session_dir / "calibration.json").read_text(encoding="utf-8"))

    assert summary["diagnostic_map_used"] is True
    assert summary["diagnostic_map_id"] == "trajectory_aligned_diagnostic_map"
    assert summary["diagnostic_map_shape"] == "l_shape"
    assert summary["diagnostic_map_turn"] == "left"
    assert summary["raw_main_direction"] == pytest.approx([1.0, 0.0, 0.0])
    assert summary["snapped_main_direction"] == "x+"
    assert summary["snapped_perp_direction"] == "y+"
    assert summary["snap_angle_degrees"] == pytest.approx(0.0)

    assert scene["scene_type"] == "diagnostic_map"
    assert scene["is_formal_scene"] is False
    assert scene["track_boxes"]
    assert len(scene["track_boxes"]) == 2
    assert scene["target_region"]["metadata"]["based_on_segment_id"] == "segment_01"
    assert scene["metadata"]["diagnostic"] is True
    assert scene["metadata"]["post_hoc"] is True

    assert trial_config["scene_type"] == "diagnostic_map"
    assert trial_config["is_formal_scene"] is False
    assert trial_config["track_boxes"]
    assert trial_config["target_region"] is not None
    assert trial_summary["diagnostic_map_used"] is True
    assert session_meta["calibration_type"] == "post_hoc_auto"
    assert session_meta["is_formal_calibration"] is False
    assert session_meta["scene_type"] == "diagnostic_map"
    assert calibration["calibration_type"] == "post_hoc_auto"
    assert calibration["is_formal_calibration"] is False

    analysis = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )
    assert analysis["trajectory_map_used_track_boxes"] is True
    assert analysis["track_box_count"] == 2
    assert analysis["target_region_present"] is True


def test_diagnostic_map_and_map_config_are_mutually_exclusive(tmp_path: Path) -> None:
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        map_config=Path("maps/examples/xoy_straight.json"),
        diagnostic_map=True,
    )

    with pytest.raises(ValueError, match="cannot be used together"):
        run_offline_replay(config)


def test_diagnostic_shapes_generate_valid_map_configs() -> None:
    task_points = _task_points()
    for shape in ("cross", "l_shape", "t_shape"):
        config = OfflineReplayConfig(
            raw_jsonl=Path("unused.jsonl"),
            diagnostic_map=True,
            diagnostic_map_shape=shape,
            diagnostic_map_turn="right",
        )

        generated = generate_trajectory_aligned_diagnostic_map(task_points, config)

        assert validate_map_config(generated).is_valid
        assert generated.metadata["snapped_main_direction"] == "x+"
        assert generated.metadata["shape"] == shape
        assert generated.target_region is not None
        assert generated.target_region.metadata["type"] == "target_region"


def test_diagnostic_main_direction_snaps_to_task_axis() -> None:
    task_points = [
        [0.0, 0.0, 0.0],
        [0.1, 0.3, 0.0],
        [0.2, 0.6, 0.0],
    ]
    config = OfflineReplayConfig(
        raw_jsonl=Path("unused.jsonl"),
        diagnostic_map=True,
    )

    generated = generate_trajectory_aligned_diagnostic_map(task_points, config)

    assert generated.metadata["raw_main_direction"] == pytest.approx(
        [0.316227766, 0.948683298, 0.0]
    )
    assert generated.metadata["snapped_main_direction"] == "y+"
    assert generated.metadata["snap_angle_degrees"] > 0.0


def test_diagnostic_map_fails_when_points_are_too_concentrated() -> None:
    config = OfflineReplayConfig(
        raw_jsonl=Path("unused.jsonl"),
        diagnostic_map=True,
    )

    with pytest.raises(ValueError, match="Unable to estimate diagnostic map direction"):
        build_diagnostic_map_scene(
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            config,
        )


def _task_points() -> list[list[float]]:
    return [
        [0.0, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.2, 0.0, 0.0],
    ]


def _raw_frames() -> list[dict]:
    return [
        _raw_frame(0.0, 0.0),
        _raw_frame(100.0, 0.1),
        _raw_frame(200.0, 0.2),
        _raw_frame(300.0, 0.3),
    ]


def _raw_frame(timestamp: float, pinch_x: float) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp),
        "subject_end": False,
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


def _write_raw_jsonl(tmp_path: Path, frames: list[dict]) -> Path:
    path = tmp_path / "raw_frames.jsonl"
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return path
