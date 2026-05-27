"""Tests for offline replay with template-generated maps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import analyze_session
from offline_replay_autocalibrated import OfflineReplayConfig, run_offline_replay, write_outputs


def test_map_template_replay_writes_session_and_summary(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        map_template=_write_template(tmp_path),
        template_anchor_frames=3,
        write_session=True,
        session_dir=session_dir,
    )

    result = run_offline_replay(config)
    write_outputs(result, config.out_dir)

    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    scene = json.loads((config.out_dir / "scene_auto.json").read_text(encoding="utf-8"))
    trial_config = json.loads((session_dir / "trial_config.json").read_text(encoding="utf-8"))
    session_meta = json.loads((session_dir / "session_meta.json").read_text(encoding="utf-8"))

    assert summary["map_template_used"] is True
    assert summary["template_id"] == "template_l"
    assert summary["raw_main_direction"] == pytest.approx([1.0, 0.0, 0.0])
    assert summary["snapped_main_direction"] == "x+"
    assert summary["snap_angle_degrees"] == pytest.approx(0.0)
    assert summary["track_box_count"] == 2

    assert scene["scene_type"] == "map_template_generated"
    assert scene["is_formal_scene"] is False
    assert scene["track_boxes"]
    assert scene["metadata"]["generator_name"] == "template_aligned_to_trajectory"
    assert scene["metadata"]["template_id"] == "template_l"

    assert trial_config["scene_type"] == "map_template_generated"
    assert trial_config["track_boxes"]
    assert trial_config["target_region"] is not None
    assert session_meta["scene_type"] == "map_template_generated"

    analysis = analyze_session.analyze_session(
        session_dir=session_dir,
        no_plots=True,
        overwrite=True,
    )
    assert analysis["trajectory_map_used_track_boxes"] is True
    assert analysis["track_box_count"] == 2
    assert analysis["target_region_present"] is True


def test_map_config_and_map_template_are_mutually_exclusive(tmp_path: Path) -> None:
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        map_config=Path("maps/examples/xoy_straight.json"),
        map_template=_write_template(tmp_path),
    )

    with pytest.raises(ValueError, match="cannot be used together"):
        run_offline_replay(config)


def test_diagnostic_map_and_map_template_are_mutually_exclusive(tmp_path: Path) -> None:
    config = OfflineReplayConfig(
        raw_jsonl=_write_raw_jsonl(tmp_path, _raw_frames()),
        calibration_frames=2,
        out_dir=tmp_path / "out",
        diagnostic_map=True,
        map_template=_write_template(tmp_path),
    )

    with pytest.raises(ValueError, match="cannot be used together"):
        run_offline_replay(config)


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


def _write_template(tmp_path: Path) -> Path:
    path = tmp_path / "template_l.json"
    path.write_text(json.dumps(_template_payload(), indent=2), encoding="utf-8")
    return path


def _template_payload() -> dict:
    return {
        "template_id": "template_l",
        "description": "Simple L template.",
        "coordinate_space": "template",
        "unit": "m",
        "anchor_direction": "x+",
        "block_initial_center_template": [0.0, 0.0, 0.0],
        "block_size": [0.2, 0.2, 0.2],
        "track_boxes": [
            {
                "id": "segment_00",
                "order": 0,
                "label": "main",
                "min": [0.0, -0.2, -0.1],
                "max": [1.0, 0.2, 0.1],
                "metadata": {"direction": "x+"},
            },
            {
                "id": "segment_01",
                "order": 1,
                "label": "turn",
                "min": [0.8, 0.0, -0.1],
                "max": [1.2, 0.8, 0.1],
                "metadata": {"direction": "y+"},
            },
        ],
        "target_region": {
            "id": "target",
            "label": "target",
            "min": [0.8, 0.6, -0.1],
            "max": [1.2, 0.8, 0.1],
            "metadata": {"type": "target_region"},
        },
        "metadata": {"source": "test"},
    }
