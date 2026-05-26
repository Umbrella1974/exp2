"""Tests for offline replay with MapConfig scene input."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import analyze_session
from offline_replay_autocalibrated import OfflineReplayConfig, run_offline_replay, write_outputs


def test_without_map_config_keeps_auto_scene_outputs(tmp_path: Path) -> None:
    raw_frames = _raw_frames()
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, raw_frames),
        calibration_frames=2,
        out_dir=tmp_path / "out",
    )

    result = run_offline_replay(config)
    write_outputs(result, config.out_dir)

    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    scene = json.loads((config.out_dir / "scene_auto.json").read_text(encoding="utf-8"))
    assert "map_config_used" not in summary
    assert scene["scene_mode"] == "wide-track"
    assert "track_boxes" not in scene


def test_map_config_replay_writes_session_and_analyzer_uses_track_boxes(tmp_path: Path) -> None:
    raw_frames = _raw_frames()
    session_dir = tmp_path / "session"
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, raw_frames),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        map_config=Path("maps/examples/xoy_straight.json"),
        map_id_override="override_straight",
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

    assert summary["map_config_used"] is True
    assert summary["map_id"] == "override_straight"
    assert summary["original_map_id"] == "xoy_straight"
    assert summary["map_id_overridden"] is True
    assert summary["map_config_version"] == 1
    assert summary["map_source_type"] == "manual"
    assert summary["track_box_count"] == 1
    assert summary["target_region_present"] is True
    assert summary["strict_map_validation"] is False
    assert summary["map_validation_errors"] == []

    assert scene["scene_type"] == "map_config"
    assert scene["is_formal_scene"] is False
    assert scene["map_id"] == "override_straight"
    assert scene["metadata"]["original_map_id"] == "xoy_straight"
    assert scene["metadata"]["map_id_overridden"] is True
    assert scene["track_boxes"]
    assert scene["target_region"]["metadata"]["type"] == "target_region"

    assert trial_config["scene_type"] == "map_config"
    assert trial_config["is_formal_scene"] is False
    assert trial_config["track_boxes"]
    assert trial_config["target_region"] is not None
    assert trial_config["map_id"] == "override_straight"
    assert trial_summary["map_config_used"] is True
    assert session_meta["calibration_type"] == "post_hoc_auto"
    assert session_meta["is_formal_calibration"] is False
    assert session_meta["scene_type"] == "map_config"
    assert session_meta["is_formal_scene"] is False
    assert calibration["calibration_type"] == "post_hoc_auto"
    assert calibration["is_formal_calibration"] is False

    raw_lines = (session_dir / "raw_frames.jsonl").read_text(encoding="utf-8").splitlines()
    saved_raw_frames = [json.loads(line) for line in raw_lines]
    assert saved_raw_frames == raw_frames
    assert all("frame_index" not in frame for frame in saved_raw_frames)

    analysis = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )
    assert analysis["trajectory_map_used_track_boxes"] is True
    assert analysis["track_box_count"] > 0
    assert analysis["target_region_present"] is True


def test_map_validation_error_fails_clearly(tmp_path: Path) -> None:
    bad_map_path = tmp_path / "bad_map.json"
    _write_json(
        bad_map_path,
        {
            "map_id": "bad_map",
            "description": "Invalid map with no boxes.",
            "coordinate_space": "task",
            "unit": "m",
            "block_initial_center_task": [0.0, 0.0, 0.0],
            "block_size": [0.2, 0.2, 0.2],
            "track_boxes": [],
            "metadata": {},
        },
    )
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        map_config=bad_map_path,
    )

    with pytest.raises(ValueError, match="map validation failed"):
        run_offline_replay(config)


def test_map_validation_warning_continues_unless_strict(tmp_path: Path) -> None:
    warning_map_path = _warning_map(tmp_path)
    base = {
        "raw_jsonl": _write_raw_jsonl(tmp_path, _raw_frames()),
        "calibration_frames": 2,
        "out_dir": tmp_path / "out",
        "map_config": warning_map_path,
    }

    result = run_offline_replay(OfflineReplayConfig(**base))
    assert result.summary["map_config_used"] is True
    assert result.summary["map_validation_warnings"]

    with pytest.raises(ValueError, match="strict map validation failed due to warnings"):
        run_offline_replay(OfflineReplayConfig(**base, strict_map_validation=True))


def _raw_frames() -> list[dict]:
    return [
        _raw_frame(0.0, 0.0),
        _raw_frame(100.0, 0.1),
        _raw_frame(200.0, 0.2),
    ]


def _raw_frame(timestamp: float, pinch_x: float) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp),
        "combined_monotonic_ms": timestamp + 1.0,
        "skeleton_receive_monotonic_ms": timestamp + 2.0,
        "tracker_receive_monotonic_ms": timestamp + 4.5,
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
                "last_update_time": timestamp + 3.0,
                "valid": True,
            }
        ],
    }


def _write_raw_jsonl(tmp_path: Path, frames: list[dict]) -> Path:
    path = tmp_path / "raw_frames.jsonl"
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return path


def _warning_map(tmp_path: Path) -> Path:
    payload = json.loads(Path("maps/examples/xoy_straight.json").read_text(encoding="utf-8"))
    payload["target_region"] = {
        "id": "target",
        "order": None,
        "label": "Face-touch target",
        "min": [1.2, -0.15, -0.1],
        "max": [1.4, 0.15, 0.1],
        "metadata": {"type": "target_region"},
    }
    path = tmp_path / "warning_map.json"
    _write_json(path, payload)
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
