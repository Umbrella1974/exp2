"""Tests for formal-calibrated offline replay."""

from __future__ import annotations

import json
from pathlib import Path

from calibrate_from_raw_jsonl_table import main as calibration_main
from offline_replay_formal_calibrated import (
    FormalReplayConfig,
    run_formal_replay,
    write_outputs,
)


def test_formal_replay_writes_outputs_without_post_hoc_semantics(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path, _raw_frames())
    calibration_path = _create_calibration(tmp_path, raw_path)
    config = FormalReplayConfig(
        raw_jsonl=raw_path,
        calibration_json=calibration_path,
        map_config=Path("maps/examples/xoy_straight.json"),
        out_dir=tmp_path / "out",
    )
    result = run_formal_replay(config)
    write_outputs(result, config.out_dir)

    summary = json.loads((config.out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "offline_formal_calibrated_replay"
    assert summary["calibration_type"] == "formal_table_lines"
    assert summary["is_formal_calibration"] is True
    assert summary["is_live_trial"] is False
    assert summary["scene_type"] == "map_config"
    assert "post_hoc_auto" not in json.dumps(summary)
    assert (config.out_dir / "frames.csv").exists()
    assert (config.out_dir / "events.csv").exists()


def test_formal_replay_write_session_marks_formal_calibration(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path, _raw_frames())
    calibration_path = _create_calibration(tmp_path, raw_path)
    session_dir = tmp_path / "session"
    config = FormalReplayConfig(
        raw_jsonl=raw_path,
        calibration_json=calibration_path,
        map_config=Path("maps/examples/xoy_straight.json"),
        out_dir=tmp_path / "out_session",
        write_session=True,
        session_dir=session_dir,
    )
    result = run_formal_replay(config)
    write_outputs(result, config.out_dir)

    session_meta = json.loads((session_dir / "session_meta.json").read_text(encoding="utf-8"))
    session_calibration = json.loads((session_dir / "calibration.json").read_text(encoding="utf-8"))
    trial_summary = json.loads((session_dir / "trial_summary.json").read_text(encoding="utf-8"))
    assert session_meta["mode"] == "offline_formal_calibrated_replay"
    assert session_meta["is_formal_calibration"] is True
    assert session_meta["is_live_trial"] is False
    assert session_calibration["calibration_type"] == "formal_table_lines"
    assert trial_summary["is_formal_calibration"] is True


def _create_calibration(tmp_path: Path, raw_path: Path) -> Path:
    calibration_path = tmp_path / "formal_calibration.json"
    exit_code = calibration_main(
        [
            "--raw-jsonl",
            str(raw_path),
            "--origin-start-frame",
            "0",
            "--long-line-start-frame",
            "10",
            "--width-line-start-frame",
            "20",
            "--diagonal-line-start-frame",
            "30",
            "--sample-window-frames",
            "10",
            "--out",
            str(calibration_path),
        ]
    )
    assert exit_code == 0
    return calibration_path


def _raw_frames() -> list[dict]:
    frames: list[dict] = []
    for index in range(10):
        frames.append(_raw_frame(index, [0.0, 0.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(10 + index, [index / 9.0, 0.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(20 + index, [0.0, index / 9.0, 0.0]))
    for index in range(10):
        frames.append(_raw_frame(30 + index, [index / 9.0, index / 9.0, 0.0]))
    return frames


def _raw_frame(index: int, tracker_position: list[float]) -> dict:
    timestamp = index * 1000.0
    return {
        "timestamp": timestamp,
        "frame": index,
        "subject_end": False,
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
                "position": tracker_position,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def _write_raw_jsonl(tmp_path: Path, frames: list[dict]) -> Path:
    path = tmp_path / "raw_frames.jsonl"
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return path
