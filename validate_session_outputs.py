"""Read-only validation for saved session artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from cue_feedback import CUE_CSV_FIELDS


FINAL_DIAGNOSTIC_FIELDS = (
    "block_center_task_position_at_end",
    "pinch_task_position_at_end",
    "block_center_in_target_at_end",
    "distance_to_target_at_end",
)

LIVE_REQUIRED_SESSION_FILES = (
    "session_meta.json",
    "calibration.json",
    "trial_config.json",
    "raw_frames.jsonl",
    "termination_config.json",
    "trial_summary.json",
)

GENERIC_REQUIRED_SESSION_FILES = (
    "session_meta.json",
    "calibration.json",
    "trial_config.json",
    "raw_frames.jsonl",
    "trial_summary.json",
)


def validate_session_outputs(
    session_dir: str | Path,
    *,
    summary_json: str | Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate one saved session without modifying any artifact."""

    session_path = Path(session_dir)
    summary_path = (
        Path(summary_json)
        if summary_json is not None
        else session_path.parent / "summary.json"
    )
    errors: list[str] = []
    warnings: list[str] = []
    checked_files: dict[str, str] = {}

    if not session_path.exists():
        errors.append(f"session directory does not exist: {session_path}")
    elif not session_path.is_dir():
        errors.append(f"session path is not a directory: {session_path}")

    meta = _read_json_object(
        session_path / "session_meta.json",
        "session_meta.json",
        errors,
        checked_files,
    )
    summary = _read_json_object(summary_path, "summary.json", errors, checked_files)
    trial_summary = _read_json_object(
        session_path / "trial_summary.json",
        "trial_summary.json",
        errors,
        checked_files,
    )
    mode = _first_nonempty(
        _dict_value(meta, "mode"),
        _dict_value(summary, "mode"),
        _dict_value(trial_summary, "mode"),
    )
    is_live_integrated = mode == "live_integrated_session"

    required_files = LIVE_REQUIRED_SESSION_FILES if is_live_integrated else GENERIC_REQUIRED_SESSION_FILES
    for filename in required_files:
        path = session_path / filename
        checked_files.setdefault(filename, str(path))
        if not path.exists():
            errors.append(f"required session artifact is missing: {path}")

    if not summary_path.exists():
        errors.append(f"required summary artifact is missing: {summary_path}")

    calibration = _read_json_object_if_present(
        session_path / "calibration.json",
        "calibration.json",
        errors,
        checked_files,
    )
    trial_config = _read_json_object_if_present(
        session_path / "trial_config.json",
        "trial_config.json",
        errors,
        checked_files,
    )
    termination = _read_json_object_if_present(
        session_path / "termination_config.json",
        "termination_config.json",
        errors,
        checked_files,
    )

    _validate_required_summary_fields(summary, "summary.json", errors, warnings)
    _validate_required_summary_fields(trial_summary, "trial_summary.json", errors, warnings)
    _validate_summary_consistency(summary, trial_summary, errors)
    _validate_termination_consistency(termination, summary, trial_summary, is_live_integrated, errors, warnings)
    _validate_identifier_consistency(
        calibration=calibration,
        meta=meta,
        trial_config=trial_config,
        summary=summary,
        trial_summary=trial_summary,
        errors=errors,
        warnings=warnings,
    )
    _validate_raw_frames(session_path / "raw_frames.jsonl", warnings)
    _validate_target_diagnostics(trial_config, summary, trial_summary, warnings)
    _validate_gui_artifact(session_path, summary, trial_summary, errors, warnings, checked_files)
    _validate_timing_artifact(
        session_path,
        mode,
        summary,
        trial_summary,
        errors,
        warnings,
        checked_files,
    )
    _validate_cue_artifacts(
        session_path,
        mode,
        meta,
        trial_config,
        summary,
        trial_summary,
        errors,
        warnings,
        checked_files,
    )

    failed = bool(errors) or (strict and bool(warnings))
    return {
        "status": "FAIL" if failed else "PASS",
        "strict": bool(strict),
        "session_dir": str(session_path),
        "summary_json": str(summary_path),
        "mode": mode,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "checked_files": checked_files,
    }


def _read_json_object(
    path: Path,
    label: str,
    errors: list[str],
    checked_files: dict[str, str],
) -> dict[str, Any] | None:
    checked_files[label] = str(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} could not be parsed: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object.")
        return None
    return payload


def _read_json_object_if_present(
    path: Path,
    label: str,
    errors: list[str],
    checked_files: dict[str, str],
) -> dict[str, Any] | None:
    checked_files.setdefault(label, str(path))
    if not path.exists():
        return None
    return _read_json_object(path, label, errors, checked_files)


def _validate_required_summary_fields(
    payload: dict[str, Any] | None,
    label: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    if payload is None:
        return
    for field in ("trial_outcome", "end_reason", "termination_config"):
        if field not in payload or payload.get(field) in (None, ""):
            errors.append(f"{label} is missing required field: {field}")
    if "termination_config" in payload and not isinstance(payload.get("termination_config"), dict):
        errors.append(f"{label}.termination_config must be an object.")
    for field in FINAL_DIAGNOSTIC_FIELDS:
        if field not in payload:
            errors.append(f"{label} is missing required field: {field}")
        elif payload.get(field) is None:
            warnings.append(f"{label}.{field} is null.")


def _validate_summary_consistency(
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    errors: list[str],
) -> None:
    if summary is None or trial_summary is None:
        return
    for field in ("trial_outcome", "end_reason"):
        left = summary.get(field)
        right = trial_summary.get(field)
        if left not in (None, "") and right not in (None, "") and left != right:
            errors.append(
                f"summary.json and trial_summary.json disagree on {field}: {left!r} != {right!r}"
            )
    left_config = summary.get("termination_config")
    right_config = trial_summary.get("termination_config")
    if isinstance(left_config, dict) and isinstance(right_config, dict) and left_config != right_config:
        errors.append("summary.json and trial_summary.json disagree on termination_config.")


def _validate_termination_consistency(
    termination: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    is_live_integrated: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    if termination is None:
        if is_live_integrated:
            errors.append("effective termination config file is missing: termination_config.json")
        else:
            warnings.append("termination_config.json is not available for this legacy/non-live session.")
        return
    for label, payload in (("summary.json", summary), ("trial_summary.json", trial_summary)):
        if payload is None:
            continue
        effective = payload.get("termination_config")
        if isinstance(effective, dict) and effective != termination:
            errors.append(f"termination_config.json disagrees with {label}.termination_config.")


def _validate_identifier_consistency(
    *,
    calibration: dict[str, Any] | None,
    meta: dict[str, Any] | None,
    trial_config: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    errors: list[str],
    warnings: list[str],
) -> None:
    sources = {
        "calibration_id": (
            ("calibration.json", calibration),
            ("session_meta.json", meta),
            ("trial_config.json", trial_config),
            ("summary.json", summary),
            ("trial_summary.json", trial_summary),
        ),
        "map_id": (
            ("session_meta.json", meta),
            ("trial_config.json", trial_config),
            ("summary.json", summary),
            ("trial_summary.json", trial_summary),
        ),
        "trial_id": (
            ("session_meta.json", meta),
            ("trial_config.json", trial_config),
            ("summary.json", summary),
            ("trial_summary.json", trial_summary),
        ),
    }
    for field, payload_sources in sources.items():
        present: list[tuple[str, str]] = []
        missing: list[str] = []
        for label, payload in payload_sources:
            value = _dict_value(payload, field)
            if value in (None, ""):
                missing.append(label)
            else:
                present.append((label, str(value)))
        values = sorted({value for _, value in present})
        if len(values) > 1:
            details = ", ".join(f"{label}={value!r}" for label, value in present)
            errors.append(f"{field} is inconsistent across artifacts: {details}")
        if missing:
            warnings.append(f"{field} is missing in: {', '.join(missing)}")


def _validate_raw_frames(path: Path, warnings: list[str]) -> None:
    if path.exists() and path.stat().st_size == 0:
        warnings.append("raw_frames.jsonl exists but is empty.")


def _validate_target_diagnostics(
    trial_config: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    warnings: list[str],
) -> None:
    target_region = _dict_value(trial_config, "target_region")
    if target_region in (None, ""):
        warnings.append("trial_config.json has no target_region; target diagnostics may be null.")
    for label, payload in (("summary.json", summary), ("trial_summary.json", trial_summary)):
        if payload is None:
            continue
        if (
            payload.get("block_center_in_target_at_end") is None
            or payload.get("distance_to_target_at_end") is None
        ):
            warnings.append(f"{label} has incomplete target diagnostics.")


def _validate_gui_artifact(
    session_dir: Path,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    errors: list[str],
    warnings: list[str],
    checked_files: dict[str, str],
) -> None:
    gui_enabled = _any_true(
        _dict_value(summary, "gui_enabled"),
        _dict_value(trial_summary, "gui_enabled"),
    )
    if not gui_enabled:
        return
    path = session_dir / "gui_diagnostics.csv"
    checked_files["gui_diagnostics.csv"] = str(path)
    if not path.exists():
        errors.append(f"GUI was enabled but gui_diagnostics.csv is missing: {path}")
        return
    row_count = _csv_data_row_count(path, "gui_diagnostics.csv", errors)
    if row_count is not None and row_count < 3:
        warnings.append(f"gui_diagnostics.csv contains only {row_count} data row(s).")


def _validate_timing_artifact(
    session_dir: Path,
    mode: str | None,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    errors: list[str],
    warnings: list[str],
    checked_files: dict[str, str],
) -> None:
    timing_enabled = _any_true(
        _dict_value(summary, "timing_enabled"),
        _dict_value(trial_summary, "timing_enabled"),
    )
    if mode != "live_integrated_session" or not timing_enabled:
        return
    path = session_dir / "timing_diagnostics.csv"
    checked_files["timing_diagnostics.csv"] = str(path)
    if not path.exists():
        errors.append(f"timing is enabled but timing_diagnostics.csv is missing: {path}")
        return
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        errors.append(f"timing_diagnostics.csv could not be parsed: {exc}")
        return
    if not fieldnames:
        errors.append("timing_diagnostics.csv has no CSV header.")
        return
    if not rows:
        warnings.append("timing_diagnostics.csv contains no data rows.")
        return
    optional_fields = (
        "trial_update_duration_ms",
        "snapshot_publish_to_gui_render_latency_ms",
        "operator_command_to_trial_stop_latency_ms",
    )
    partially_empty = [
        field
        for field in optional_fields
        if field in fieldnames
        and any(row.get(field) in (None, "") for row in rows)
    ]
    if partially_empty:
        warnings.append(
            "timing_diagnostics.csv has empty optional timing fields: "
            + ", ".join(partially_empty)
        )


def _validate_cue_artifacts(
    session_dir: Path,
    mode: str | None,
    meta: dict[str, Any] | None,
    trial_config: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    trial_summary: dict[str, Any] | None,
    errors: list[str],
    warnings: list[str],
    checked_files: dict[str, str],
) -> None:
    if mode != "live_integrated_session":
        return
    payloads = (
        ("session_meta.json", meta),
        ("trial_config.json", trial_config),
        ("summary.json", summary),
        ("trial_summary.json", trial_summary),
    )
    cue_aware = any(
        isinstance(payload, dict)
        and (
            "effective_cue_config" in payload
            or "cue_sink" in payload
            or "cue_enabled" in payload
        )
        for _, payload in payloads
    )
    if not cue_aware:
        return

    cue_config_path = session_dir / "cue_config.json"
    checked_files["cue_config.json"] = str(cue_config_path)
    cue_config = _read_json_object(
        cue_config_path,
        "cue_config.json",
        errors,
        checked_files,
    )
    if cue_config is None and not cue_config_path.exists():
        errors.append(f"cue-aware live session is missing cue_config.json: {cue_config_path}")

    for label, payload in payloads:
        if payload is None:
            continue
        effective = payload.get("effective_cue_config")
        if not isinstance(effective, dict):
            errors.append(f"{label}.effective_cue_config must be an object for cue-aware sessions.")
        elif cue_config is not None and effective != cue_config:
            errors.append(f"cue_config.json disagrees with {label}.effective_cue_config.")
    if summary is not None and trial_summary is not None:
        for field in ("cue_sink", "cue_enabled", "cue_mode", "cue_count", "cue_type_counts"):
            left = summary.get(field)
            right = trial_summary.get(field)
            if left not in (None, "") and right not in (None, "") and left != right:
                errors.append(f"summary.json and trial_summary.json disagree on {field}.")

    cue_sink = _first_nonempty(
        _dict_value(summary, "cue_sink"),
        _dict_value(trial_summary, "cue_sink"),
        _dict_value(meta, "cue_sink"),
        _dict_value(trial_config, "cue_sink"),
    )
    cue_enabled = _any_true(
        _dict_value(summary, "cue_enabled"),
        _dict_value(trial_summary, "cue_enabled"),
        _dict_value(meta, "cue_enabled"),
        _dict_value(trial_config, "cue_enabled"),
    )
    if cue_sink == "none" and cue_enabled:
        errors.append("cue_sink=none is inconsistent with cue_enabled=true.")
    if cue_sink not in (None, "", "none") and not cue_enabled:
        errors.append(f"cue_sink={cue_sink!r} is inconsistent with cue_enabled=false.")

    if not cue_enabled and cue_sink in (None, "", "none"):
        return

    cue_log_path = session_dir / "cue_log.csv"
    checked_files["cue_log.csv"] = str(cue_log_path)
    if not cue_log_path.exists():
        errors.append(f"cue is enabled but cue_log.csv is missing: {cue_log_path}")
        return
    try:
        with cue_log_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        errors.append(f"cue_log.csv could not be parsed: {exc}")
        return

    missing_fields = [field for field in CUE_CSV_FIELDS if field not in fieldnames]
    if missing_fields:
        errors.append("cue_log.csv is missing required fields: " + ", ".join(missing_fields))

    expected_count = _first_nonempty(
        _dict_value(summary, "cue_count"),
        _dict_value(trial_summary, "cue_count"),
    )
    if expected_count not in (None, ""):
        try:
            count_value = int(expected_count)
        except (TypeError, ValueError):
            errors.append("cue_count must be an integer.")
        else:
            if count_value != len(rows):
                errors.append(f"cue_count does not match cue_log.csv rows: {count_value} != {len(rows)}")

    actual_type_counts: dict[str, int] = {}
    cue_ids: list[str] = []
    expected_trial_id = _first_nonempty(
        _dict_value(summary, "trial_id"),
        _dict_value(trial_summary, "trial_id"),
        _dict_value(meta, "trial_id"),
    )
    for row in rows:
        cue_type = str(row.get("cue_type", ""))
        actual_type_counts[cue_type] = actual_type_counts.get(cue_type, 0) + 1
        cue_id = str(row.get("cue_id", ""))
        cue_ids.append(cue_id)
        if not cue_id:
            errors.append("cue_log.csv contains an empty cue_id.")
        if row.get("mode") not in ("live", None, ""):
            errors.append(f"cue_log.csv contains non-live cue mode: {row.get('mode')!r}")
        if row.get("is_live_cue_timing") not in ("true", "True", True):
            errors.append("cue_log.csv live cue rows must set is_live_cue_timing=true.")
        if cue_sink not in (None, "") and row.get("requested_cue_sink") != str(cue_sink):
            errors.append("cue_log.csv requested_cue_sink is inconsistent with summary cue_sink.")
        if expected_trial_id not in (None, "") and str(row.get("trial_id", "")) != str(expected_trial_id):
            errors.append("cue_log.csv trial_id is inconsistent with session trial_id.")
            break
    if len(cue_ids) != len(set(cue_ids)):
        errors.append("cue_log.csv cue_id values must be unique.")

    expected_type_counts = _first_nonempty(
        _dict_value(summary, "cue_type_counts"),
        _dict_value(trial_summary, "cue_type_counts"),
    )
    if isinstance(expected_type_counts, dict):
        try:
            normalized = {str(key): int(value) for key, value in expected_type_counts.items()}
        except (TypeError, ValueError):
            errors.append("cue_type_counts values must be integers.")
        else:
            if normalized != actual_type_counts:
                errors.append(
                    "cue_type_counts does not match cue_log.csv: "
                    f"{normalized!r} != {actual_type_counts!r}"
                )


def _csv_data_row_count(path: Path, label: str, errors: list[str]) -> int | None:
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        errors.append(f"{label} could not be parsed: {exc}")
        return None
    return max(0, len(rows) - 1)


def _dict_value(payload: dict[str, Any] | None, field: str) -> Any:
    return payload.get(field) if isinstance(payload, dict) else None


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _any_true(*values: Any) -> bool:
    return any(value is True for value in values)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate saved session artifacts without modifying them.")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--summary-json", default=None)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate_session_outputs(
        args.session_dir,
        summary_json=args.summary_json,
        strict=args.strict,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
