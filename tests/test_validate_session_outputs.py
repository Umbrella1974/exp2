"""Tests for read-only session artifact validation."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from cue_feedback import CUE_CSV_FIELDS
from timing_diagnostics import TIMING_CSV_FIELDS
from validate_session_outputs import validate_session_outputs


def test_valid_live_session_passes_with_default_summary_path(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)

    report = validate_session_outputs(session_dir)

    assert report["status"] == "PASS"
    assert report["error_count"] == 0
    assert report["summary_json"] == str(tmp_path / "summary.json")


def test_validator_finds_missing_summary_trial_summary_and_termination_config(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)
    (tmp_path / "summary.json").unlink()
    (session_dir / "trial_summary.json").unlink()
    (session_dir / "termination_config.json").unlink()

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    joined = "\n".join(report["errors"])
    assert "summary artifact is missing" in joined
    assert "trial_summary.json" in joined
    assert "termination_config.json" in joined


def test_validator_reports_malformed_summary_files(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)
    (tmp_path / "summary.json").write_text("{bad", encoding="utf-8")
    (session_dir / "trial_summary.json").write_text("[]", encoding="utf-8")

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    joined = "\n".join(report["errors"])
    assert "summary.json could not be parsed" in joined
    assert "trial_summary.json must contain a JSON object" in joined


def test_validator_detects_outcome_and_termination_config_mismatch(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)
    trial_summary = _read_json(session_dir / "trial_summary.json")
    trial_summary["trial_outcome"] = "FAILED_TIMEOUT"
    trial_summary["end_reason"] = "trial_timeout"
    _write_json(session_dir / "trial_summary.json", trial_summary)
    termination = _read_json(session_dir / "termination_config.json")
    termination["max_detach_count"] = 99
    _write_json(session_dir / "termination_config.json", termination)

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    joined = "\n".join(report["errors"])
    assert "disagree on trial_outcome" in joined
    assert "disagree on end_reason" in joined
    assert "termination_config.json disagrees" in joined


def test_validator_requires_gui_and_timing_artifacts_when_enabled(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path, gui_enabled=True)
    (session_dir / "timing_diagnostics.csv").unlink()

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    joined = "\n".join(report["errors"])
    assert "gui_diagnostics.csv is missing" in joined
    assert "timing_diagnostics.csv is missing" in joined


def test_old_non_live_session_does_not_require_timing_file(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)
    meta = _read_json(session_dir / "session_meta.json")
    meta["mode"] = "offline_autocalibrated"
    _write_json(session_dir / "session_meta.json", meta)
    summary = _read_json(tmp_path / "summary.json")
    summary["mode"] = "offline_autocalibrated"
    _write_json(tmp_path / "summary.json", summary)
    trial_summary = _read_json(session_dir / "trial_summary.json")
    trial_summary["mode"] = "offline_autocalibrated"
    _write_json(session_dir / "trial_summary.json", trial_summary)
    (session_dir / "timing_diagnostics.csv").unlink()

    report = validate_session_outputs(session_dir)

    assert not any("timing_diagnostics.csv is missing" in error for error in report["errors"])


def test_validator_detects_obvious_identifier_mismatch(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path)
    trial_config = _read_json(session_dir / "trial_config.json")
    trial_config["map_id"] = "different_map"
    _write_json(session_dir / "trial_config.json", trial_config)

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    assert any("map_id is inconsistent" in error for error in report["errors"])


def test_validator_checks_cue_config_and_log_consistency(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path, cue_aware=True)
    (session_dir / "cue_log.csv").unlink()

    report = validate_session_outputs(session_dir)

    assert report["status"] == "FAIL"
    assert any("cue_log.csv is missing" in error for error in report["errors"])


def test_validator_accepts_header_only_cue_log_when_count_is_zero(tmp_path: Path) -> None:
    session_dir = _write_valid_live_session(tmp_path, cue_aware=True, cue_count=0)

    report = validate_session_outputs(session_dir)

    assert report["status"] == "PASS"
    assert not any("cue_log" in warning for warning in report["warnings"])


def _write_valid_live_session(
    tmp_path: Path,
    *,
    gui_enabled: bool = False,
    cue_aware: bool = False,
    cue_count: int = 1,
) -> Path:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    termination = {
        "max_trial_duration_seconds": 600.0,
        "max_detach_count": 20,
        "manual_completion_enabled": True,
        "timeout_enabled": True,
        "detach_limit_enabled": True,
    }
    summary: dict[str, Any] = {
        "mode": "live_integrated_session",
        "trial_id": "trial_001",
        "map_id": "map_001",
        "calibration_id": "cal_001",
        "trial_outcome": "MAX_FRAMES_REACHED",
        "end_reason": "max_frames",
        "termination_config": termination,
        "block_center_task_position_at_end": [0.0, 0.0, 0.0],
        "pinch_task_position_at_end": [0.0, 0.0, 0.0],
        "block_center_in_target_at_end": True,
        "distance_to_target_at_end": 0.0,
        "gui_enabled": gui_enabled,
        "timing_enabled": True,
    }
    cue_config = {
        "enable_contact_cue": True,
        "enable_contact_exit_cue": True,
        "enable_slip_cue": True,
        "enable_blocked_directional_cue": True,
        "min_cue_interval_ms": 0.0,
        "repeat_policy": "edge_only",
        "message_language": "en",
    }
    if cue_aware:
        summary.update(
            {
                "cue_enabled": True,
                "cue_sink": "logging",
                "cue_mode": "live",
                "cue_count": cue_count,
                "cue_type_counts": {"contact_enter": cue_count} if cue_count else {},
                "effective_cue_config": cue_config,
            }
        )
    _write_json(tmp_path / "summary.json", summary)
    _write_json(session_dir / "trial_summary.json", summary)
    meta = {
        "mode": "live_integrated_session",
        "trial_id": "trial_001",
        "map_id": "map_001",
        "calibration_id": "cal_001",
    }
    if cue_aware:
        meta.update(
            {
                "cue_enabled": True,
                "cue_sink": "logging",
                "effective_cue_config": cue_config,
            }
        )
    _write_json(session_dir / "session_meta.json", meta)
    _write_json(session_dir / "calibration.json", {"calibration_id": "cal_001"})
    trial_config = {
        "trial_id": "trial_001",
        "map_id": "map_001",
        "calibration_id": "cal_001",
        "target_region": {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]},
    }
    if cue_aware:
        trial_config.update(
            {
                "cue_enabled": True,
                "cue_sink": "logging",
                "effective_cue_config": cue_config,
            }
        )
    _write_json(session_dir / "trial_config.json", trial_config)
    _write_json(session_dir / "termination_config.json", termination)
    (session_dir / "raw_frames.jsonl").write_text('{"frame": 0}\n', encoding="utf-8")
    _write_timing_csv(session_dir / "timing_diagnostics.csv")
    if cue_aware:
        _write_json(session_dir / "cue_config.json", cue_config)
        _write_cue_csv(session_dir / "cue_log.csv", cue_count)
    return session_dir


def _write_timing_csv(path: Path) -> None:
    row = {field: "" for field in TIMING_CSV_FIELDS}
    row.update(
        {
            "sequence_index": 0,
            "event_type": "frame",
            "mode": "live",
            "is_live_latency": "true",
            "frame_index": 0,
            "phase": "TRIAL_RUNNING",
            "frame_published": "true",
            "frame_consumed": "true",
            "frame_processed": "true",
            "overwritten_before_consume": "false",
            "trial_update_duration_ms": 1.0,
        }
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMING_CSV_FIELDS)
        writer.writeheader()
        writer.writerow(row)


def _write_cue_csv(path: Path, count: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CUE_CSV_FIELDS)
        writer.writeheader()
        for index in range(count):
            row = {field: "" for field in CUE_CSV_FIELDS}
            row.update(
                {
                    "cue_sequence_index": index,
                    "cue_id": f"trial_001:cue:{index:06d}",
                    "trial_id": "trial_001",
                    "cue_type": "contact_enter",
                    "cue_modality": "logging",
                    "requested_cue_sink": "logging",
                    "mode": "live",
                    "is_live_cue_timing": "true",
                    "source_state": "INSIDE_BLOCK",
                    "success": "true",
                    "display_status": "not_applicable",
                    "is_hardware_haptic": "false",
                    "details_json": "{}",
                }
            )
            writer.writerow(row)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
