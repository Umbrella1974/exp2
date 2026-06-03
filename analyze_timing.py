"""Summarize timing_diagnostics.csv without replaying a session."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Any, Iterable


METRIC_FIELDS = (
    "skeleton_tracker_sync_delta_ms",
    "raw_to_frame_publish_latency_ms",
    "frame_wait_age_ms",
    "raw_to_trial_update_latency_ms",
    "frame_to_trial_update_latency_ms",
    "parse_duration_ms",
    "adapter_duration_ms",
    "trial_update_duration_ms",
    "trial_update_to_snapshot_latency_ms",
    "snapshot_publish_to_gui_render_latency_ms",
    "operator_command_to_trial_stop_latency_ms",
)

TRANSPORT_METRIC_FIELDS = (
    "raw_to_frame_publish_latency_ms",
    "frame_wait_age_ms",
)


def analyze_timing(
    session_dir: str | Path,
    *,
    out: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Read one timing log, write a JSON summary, and return it."""

    session_path = Path(session_dir)
    timing_path = session_path / "timing_diagnostics.csv"
    if not timing_path.exists():
        raise FileNotFoundError(f"timing diagnostics not found: {timing_path}")

    out_path = Path(out) if out is not None else session_path / "timing_analysis_summary.json"
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {out_path}. Pass --overwrite to replace it.")

    rows = _read_rows(timing_path)
    summary = _build_summary(session_path, timing_path, rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"timing diagnostics has no CSV header: {path}")
        return [dict(row) for row in reader]


def _build_summary(
    session_dir: Path,
    timing_path: Path,
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    frame_rows = [row for row in rows if _optional_int(row.get("frame_index")) is not None]
    modes = sorted({_nonempty(row.get("mode")) for row in rows if _nonempty(row.get("mode"))})
    live_flags = {_optional_bool(row.get("is_live_latency")) for row in rows}
    live_flags.discard(None)
    warnings: list[str] = []
    if not rows:
        warnings.append("timing diagnostics contains no data rows.")
    if not any(_optional_float(row.get("trial_update_duration_ms")) is not None for row in rows):
        warnings.append("no trial update timing values were available.")
    if not any(
        _optional_float(row.get("snapshot_publish_to_gui_render_latency_ms")) is not None
        for row in rows
    ):
        warnings.append("no GUI render latency values were available.")
    if live_flags == {False}:
        warnings.append("replay timing is not a measurement of real live transport or GUI latency.")

    summary: dict[str, Any] = {
        "status": "OK",
        "session_dir": str(session_dir),
        "timing_diagnostics_path": str(timing_path),
        "mode": modes[0] if len(modes) == 1 else ("mixed" if modes else None),
        "is_live_latency": next(iter(live_flags)) if len(live_flags) == 1 else None,
        "timing_record_count": len(rows),
        "frame_count": len(frame_rows),
        "published_frame_count": _count_true(frame_rows, "frame_published"),
        "consumed_frame_count": _count_true(frame_rows, "frame_consumed"),
        "processed_frame_count": _count_true(frame_rows, "frame_processed"),
        "overwritten_before_consume_count": _count_true(frame_rows, "overwritten_before_consume"),
        "max_no_frame_gap_ms": _max_no_frame_gap_ms(session_dir, frame_rows),
        "phase_summaries": _phase_summaries(frame_rows),
        "warnings": warnings,
    }
    for field in METRIC_FIELDS:
        summary.update(_metric_summary(field, rows))

    # These aliases match the shorter names used in the Stage 4 requirement.
    summary["median_raw_to_update_latency_ms"] = summary.get(
        "median_raw_to_trial_update_latency_ms"
    )
    summary["p95_raw_to_update_latency_ms"] = summary.get(
        "p95_raw_to_trial_update_latency_ms"
    )
    summary["median_snapshot_to_gui_latency_ms"] = summary.get(
        "median_snapshot_publish_to_gui_render_latency_ms"
    )
    summary["p95_snapshot_to_gui_latency_ms"] = summary.get(
        "p95_snapshot_publish_to_gui_render_latency_ms"
    )
    operator_values = _numeric_values(rows, "operator_command_to_trial_stop_latency_ms")
    summary["operator_command_to_stop_latency_ms"] = (
        operator_values[-1] if operator_values else None
    )
    return summary


def _phase_summaries(rows: list[dict[str, str]]) -> dict[str, Any]:
    phases = sorted({_nonempty(row.get("phase")) for row in rows if _nonempty(row.get("phase"))})
    result: dict[str, Any] = {}
    for phase in phases:
        phase_rows = [row for row in rows if _nonempty(row.get("phase")) == phase]
        payload: dict[str, Any] = {
            "frame_count": len(phase_rows),
            "published_frame_count": _count_true(phase_rows, "frame_published"),
            "consumed_frame_count": _count_true(phase_rows, "frame_consumed"),
            "processed_frame_count": _count_true(phase_rows, "frame_processed"),
            "overwritten_before_consume_count": _count_true(
                phase_rows,
                "overwritten_before_consume",
            ),
        }
        for field in TRANSPORT_METRIC_FIELDS:
            payload.update(_metric_summary(field, phase_rows))
        result[phase] = payload
    return result


def _metric_summary(field: str, rows: Iterable[dict[str, str]]) -> dict[str, Any]:
    values = _numeric_values(rows, field)
    return {
        f"{field}_sample_count": len(values),
        f"median_{field}": median(values) if values else None,
        f"p95_{field}": _percentile(values, 0.95) if values else None,
        f"max_{field}": max(values) if values else None,
    }


def _numeric_values(rows: Iterable[dict[str, str]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _optional_float(row.get(field))
        if value is not None:
            values.append(value)
    return values


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _max_no_frame_gap_ms(session_dir: Path, rows: list[dict[str, str]]) -> float | None:
    trial_summary_path = session_dir / "trial_summary.json"
    if trial_summary_path.exists():
        try:
            payload = json.loads(trial_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            seconds = _optional_float(payload.get("max_no_new_frame_gap_seconds"))
            if seconds is not None:
                return seconds * 1000.0

    publish_times = sorted(
        value
        for value in (
            _optional_float(row.get("frame_published_monotonic_ms"))
            for row in rows
        )
        if value is not None
    )
    if len(publish_times) < 2:
        return None
    return max(end - start for start, end in zip(publish_times, publish_times[1:]))


def _count_true(rows: Iterable[dict[str, str]], field: str) -> int:
    return sum(_optional_bool(row.get(field)) is True for row in rows)


def _nonempty(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze session timing diagnostics.")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = analyze_timing(
            args.session_dir,
            out=args.out,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(f"Timing analysis failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
