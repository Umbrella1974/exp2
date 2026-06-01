"""Tests for replay debug runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from latest_snapshot_store import LatestSnapshotStore
from replay_debug_runner import ReplayDebugConfig, load_replay_debug_inputs, run_replay_debug
from run_replay_debug_gui import main as replay_gui_main


def test_replay_debug_runner_from_explicit_files_produces_snapshots(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw_frames.jsonl")
    calibration_path = _write_task_calibration(tmp_path / "calibration.json")
    trial_config_path = _write_trial_config(tmp_path / "trial_config.json")
    store = LatestSnapshotStore()

    result = run_replay_debug(
        ReplayDebugConfig(
            raw_jsonl=raw_path,
            calibration_json=calibration_path,
            trial_config_json=trial_config_path,
            replay_timing="fast",
            out_dir=tmp_path / "out",
        ),
        snapshot_store=store,
    )

    assert result.snapshot_count == 2
    assert result.last_snapshot is not None
    assert result.summary["mode"] == "replay_debug_gui"
    assert result.summary["run_stop_reason"] == "eof"
    assert store.get_latest().frame_index == 1
    assert (tmp_path / "out" / "replay_debug_summary.json").exists()


def test_replay_debug_runner_from_session_dir_discovers_files(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_raw_jsonl(session_dir / "raw_frames.jsonl")
    _write_posthoc_calibration(session_dir / "calibration.json")
    _write_trial_config(session_dir / "trial_config.json")
    (session_dir / "session_meta.json").write_text(
        json.dumps({"mode": "offline_autocalibrated", "trial_id": "trial_from_meta"}),
        encoding="utf-8",
    )

    inputs = load_replay_debug_inputs(ReplayDebugConfig(session_dir=session_dir))
    result = run_replay_debug(
        ReplayDebugConfig(session_dir=session_dir, replay_timing="fast", max_frames=1)
    )

    assert inputs.scene.map_id == "debug_map"
    assert result.snapshot_count == 1
    assert result.summary["session_dir"] == str(session_dir)


def test_replay_debug_runner_fails_clearly_when_inputs_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="calibration_json"):
        run_replay_debug(
            ReplayDebugConfig(
                raw_jsonl=_write_raw_jsonl(tmp_path / "raw_frames.jsonl"),
                calibration_json=tmp_path / "missing_calibration.json",
                trial_config_json=_write_trial_config(tmp_path / "trial_config.json"),
                replay_timing="fast",
            )
        )


def test_replay_debug_runner_requires_track_geometry(tmp_path: Path) -> None:
    raw_path = _write_raw_jsonl(tmp_path / "raw_frames.jsonl")
    calibration_path = _write_task_calibration(tmp_path / "calibration.json")
    trial_config_path = tmp_path / "trial_config.json"
    trial_config_path.write_text(
        json.dumps({"block_initial_center_task": [0, 0, 0], "block_size": [0.2, 0.2, 0.2]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="track"):
        run_replay_debug(
            ReplayDebugConfig(
                raw_jsonl=raw_path,
                calibration_json=calibration_path,
                trial_config_json=trial_config_path,
                replay_timing="fast",
            )
        )


def test_replay_debug_gui_headless_entrypoint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = replay_gui_main(
        [
            "--raw-jsonl",
            str(_write_raw_jsonl(tmp_path / "raw_frames.jsonl")),
            "--calibration-json",
            str(_write_task_calibration(tmp_path / "calibration.json")),
            "--trial-config-json",
            str(_write_trial_config(tmp_path / "trial_config.json")),
            "--replay-timing",
            "fast",
            "--headless",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"mode": "replay_debug_gui"' in captured.out


def _write_raw_jsonl(path: Path) -> Path:
    frames = [_raw_frame(0, [0.0, 0.0, 0.0]), _raw_frame(1, [0.02, 0.0, 0.0])]
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")
    return path


def _write_task_calibration(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "calibration_type": "debug_task_axes",
                "task_coordinate_system": {
                    "origin_world": [0.0, 0.0, 0.0],
                    "x_axis_world": [1.0, 0.0, 0.0],
                    "y_axis_world": [0.0, 1.0, 0.0],
                    "z_axis_world": [0.0, 0.0, 1.0],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_posthoc_calibration(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "calibration_type": "post_hoc_auto",
                "is_formal_calibration": False,
                "calibration_auto": {
                    "origin_world": [0.0, 0.0, 0.0],
                    "x_axis_world": [1.0, 0.0, 0.0],
                    "y_axis_world": [0.0, 1.0, 0.0],
                    "z_axis_world": [0.0, 0.0, 1.0],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_trial_config(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "trial_id": "debug_trial",
                "map_id": "debug_map",
                "block_initial_center_task": [0.0, 0.0, 0.0],
                "block_size": [0.2, 0.2, 0.2],
                "track_boxes": [
                    {
                        "id": "track",
                        "min": [-0.5, -0.5, -0.5],
                        "max": [1.0, 0.5, 0.5],
                        "order": 0,
                    }
                ],
                "pinch_threshold": {"grab": 0.025, "release": 0.035},
            }
        ),
        encoding="utf-8",
    )
    return path


def _raw_frame(frame: int, position: list[float]) -> dict[str, Any]:
    return {
        "timestamp": frame * 1000.0,
        "frame": frame,
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
