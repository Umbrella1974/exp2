"""Tests for replay debug runner."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

import run_replay_debug_gui
from debug_gui import GuiDependencyError
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
    timing_path = tmp_path / "out" / "timing_diagnostics.csv"
    assert timing_path.exists()
    assert result.summary["timing_mode"] == "replay"
    assert result.summary["timing_is_live_latency"] is False
    assert result.summary["cue_mode"] == "replay"
    assert result.summary["cue_sink"] == "logging"
    assert result.summary["cue_count"] == len(result.cue_records)
    assert result.summary["is_live_cue_timing"] is False
    assert (tmp_path / "out" / "cue_config.json").exists()
    assert (tmp_path / "out" / "cue_log.csv").exists()
    with timing_path.open("r", newline="", encoding="utf-8") as handle:
        timing_rows = list(csv.DictReader(handle))
    assert timing_rows
    assert all(row["mode"] == "replay" for row in timing_rows)
    assert all(row["is_live_latency"] == "false" for row in timing_rows)


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
    assert not (session_dir / "timing_diagnostics.csv").exists()
    assert not (session_dir / "cue_log.csv").exists()


def test_replay_debug_infers_nodes_world_for_legacy_live_integrated_session(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_raw_jsonl(session_dir / "raw_frames.jsonl")
    _write_task_calibration(session_dir / "calibration.json")
    _write_trial_config(session_dir / "trial_config.json")
    (session_dir / "session_meta.json").write_text(
        json.dumps({"mode": "live_integrated_session", "trial_id": "live_trial"}),
        encoding="utf-8",
    )

    result = run_replay_debug(ReplayDebugConfig(session_dir=session_dir, replay_timing="fast"))

    assert result.summary["pinch_position_mode"] == "nodes_world"
    assert result.last_snapshot is not None
    assert result.last_snapshot.pinch_center_task[0] == pytest.approx(0.0)


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


def test_replay_uses_session_cue_config_but_writes_only_to_out_dir(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_raw_jsonl(session_dir / "raw_frames.jsonl")
    _write_task_calibration(session_dir / "calibration.json")
    _write_trial_config(session_dir / "trial_config.json")
    (session_dir / "cue_config.json").write_text(
        json.dumps({"enable_contact_cue": False}),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    result = run_replay_debug(
        ReplayDebugConfig(session_dir=session_dir, replay_timing="fast", out_dir=out_dir)
    )

    effective = json.loads((out_dir / "cue_config.json").read_text(encoding="utf-8"))
    assert effective["enable_contact_cue"] is False
    assert result.summary["effective_cue_config"] == effective
    assert result.summary["cue_count"] == 0
    assert (out_dir / "cue_log.csv").exists()
    assert not (session_dir / "cue_log.csv").exists()


def test_replay_headless_rejects_gui_text_sink(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
            "--cue-sink",
            "gui_text",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "gui_text" in captured.err


def test_replay_explicit_invalid_cue_config_exits_before_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cue_path = tmp_path / "bad_cue.json"
    cue_path.write_text(json.dumps({"unknown_cue_key": True}), encoding="utf-8")

    exit_code = replay_gui_main(
        [
            "--raw-jsonl",
            str(_write_raw_jsonl(tmp_path / "raw_frames.jsonl")),
            "--calibration-json",
            str(_write_task_calibration(tmp_path / "calibration.json")),
            "--trial-config-json",
            str(_write_trial_config(tmp_path / "trial_config.json")),
            "--cue-config",
            str(cue_path),
            "--headless",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Config error" in captured.err


def test_replay_corrupt_auto_loaded_session_cue_config_fails(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    _write_raw_jsonl(session_dir / "raw_frames.jsonl")
    _write_task_calibration(session_dir / "calibration.json")
    _write_trial_config(session_dir / "trial_config.json")
    (session_dir / "cue_config.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        run_replay_debug(ReplayDebugConfig(session_dir=session_dir, replay_timing="fast"))


def test_replay_debug_gui_dependency_preflight_happens_before_worker_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    worker_started = {"value": False}

    def failing_preflight() -> None:
        raise GuiDependencyError("missing gui deps")

    def unexpected_replay(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        worker_started["value"] = True
        raise AssertionError("replay worker should not start before GUI preflight")

    monkeypatch.setattr(run_replay_debug_gui, "preflight_gui_dependencies", failing_preflight)
    monkeypatch.setattr(run_replay_debug_gui, "run_replay_debug", unexpected_replay)

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
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert worker_started["value"] is False
    assert "pip install PySide6 pyqtgraph" in captured.err


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
