"""Post-hoc session analysis and visualization.

This tool reads a recorded session directory and generates an
``analysis_summary.json`` plus optional PNG plots. It does not rerun
TrialController or BlockController, does not recompute experiment state, and
does not replace formal experiment statistics. Its purpose is debug, quality
checks, and visual review of saved session data.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


POST_HOC_WARNING = (
    "This session was generated from post-hoc auto calibration and must not be treated "
    "as a formal experimental trial."
)

GENERATED_PLOT_NAMES = [
    "timeseries_xyz_with_events.png",
    "pinch_distance_with_events.png",
    "trajectory_track_map.png",
    "trajectory_track_map_with_block_footprint.png",
    "state_timeline.png",
    "haptic_timeline.png",
]

KEY_EVENT_TYPES = {
    "contact_enter",
    "contact_exit",
    "slip_start",
    "slip_end",
    "blocked_start",
    "blocked_end",
    "blocked_force_start",
    "blocked_force_end",
    "haptic_on",
    "haptic_off",
    "subject_end",
    "trial_end",
}

INACTIVE_HAPTIC_VALUES = {"", "none", "NONE", "off", "OFF", "false", "False", "0"}


@dataclass(frozen=True)
class TimeSeries:
    """Selected time column values and metadata."""

    column: str
    values: list[float | None]
    axis_values: list[float | None]
    axis_mode: str
    zero: float | None
    axis_label: str


@dataclass(frozen=True)
class TrackBoxStats:
    """Parse status for MapConfig-style track boxes."""

    total: int
    valid: int
    skipped: int
    skipped_indices: list[int]


@dataclass(frozen=True)
class BlockFootprintOverlay:
    """One x-y block footprint reconstructed from a block center and size."""

    kind: str
    label: str
    center: list[float]
    size: list[float]


def analyze_session(
    *,
    session_dir: Path,
    out_dir: Path | None = None,
    no_plots: bool = False,
    event_label_limit: int = 40,
    overwrite: bool = False,
    time_column: str = "sample_time",
    relative_time: bool = True,
    max_footprint_overlays: int = 20,
) -> dict[str, Any]:
    """Analyze a session directory and write analysis_summary.json."""

    warnings: list[str] = []
    session_dir = Path(session_dir)
    summary_path = session_dir / "analysis_summary.json"
    if summary_path.exists() and not overwrite:
        raise FileExistsError(
            f"analysis_summary.json already exists: {summary_path}. Use --overwrite to replace it."
        )

    processed_path = session_dir / "processed_frames.csv"
    if not processed_path.exists():
        summary = _error_summary(session_dir, "processed_frames.csv is required.", warnings)
        _write_json(summary_path, summary)
        return summary

    session_meta = _read_json_optional(session_dir / "session_meta.json", warnings)
    calibration = _read_json_optional(session_dir / "calibration.json", warnings)
    trial_config = _read_json_optional(session_dir / "trial_config.json", warnings)
    trial_summary = _read_json_optional(session_dir / "trial_summary.json", warnings)
    processed_rows = _read_csv(processed_path)
    event_rows = _read_csv_optional(session_dir / "events.csv", warnings)
    haptic_rows = _read_csv_optional(session_dir / "haptic.csv", warnings)

    _extend_warning_list(warnings, session_meta.get("warnings"))
    _extend_warning_list(warnings, trial_summary.get("warnings"))
    if session_meta.get("mode") == "offline_autocalibrated" and POST_HOC_WARNING not in warnings:
        warnings.append(POST_HOC_WARNING)

    times = _select_time_series(processed_rows, time_column, warnings, relative_time=relative_time)
    recorded_events = _recorded_events(event_rows)
    derived_events = _derive_events(processed_rows, times, recorded_events)
    all_events = _sort_events(recorded_events + derived_events)
    derived_event_counts = _count_events(derived_events)
    key_events = [event for event in all_events if event["event_type"] in KEY_EVENT_TYPES]
    skipped_event_label_count = max(0, len(key_events) - max(0, event_label_limit))

    haptic_active_flags, haptic_warning = _haptic_active_flags(haptic_rows, processed_rows)
    if haptic_warning:
        warnings.append(haptic_warning)
    blocked_flags, blocked_warning = _blocked_flags(processed_rows)
    if blocked_warning:
        warnings.append(blocked_warning)
    track_box_stats = _track_box_stats(trial_config)
    _extend_track_box_warnings(track_box_stats, warnings)
    slip_active_frame_count = sum(_bool(row.get("slip_active")) for row in processed_rows)
    logical_blocked_feedback_frame_count = sum(
        _bool(row.get("blocked_force_active")) for row in processed_rows
    )
    footprint_analysis = _block_footprint_analysis(
        processed_rows,
        trial_config,
        max_footprint_overlays=max_footprint_overlays,
        warnings=warnings,
    )
    slip_audit = _slip_reason_audit(processed_rows)

    summary = {
        "status": "OK",
        "session_dir": str(session_dir),
        "mode": session_meta.get("mode", ""),
        "map_id": trial_config.get("map_id", ""),
        "map_config_version": trial_config.get("map_config_version", ""),
        "map_source_type": trial_config.get("map_source_type", ""),
        "calibration_type": calibration.get(
            "calibration_type",
            session_meta.get("calibration_type", ""),
        ),
        "is_formal_calibration": calibration.get(
            "is_formal_calibration",
            session_meta.get("is_formal_calibration", ""),
        ),
        "scene_type": trial_config.get("scene_type", session_meta.get("scene_type", "")),
        "is_formal_scene": trial_config.get(
            "is_formal_scene",
            session_meta.get("is_formal_scene", ""),
        ),
        "time_column_used": times.column,
        "time_axis_mode": times.axis_mode,
        "time_zero": times.zero,
        "time_axis_label": times.axis_label,
        "total_processed_frames": len(processed_rows),
        "total_events": len(recorded_events),
        "total_haptic_records": len(haptic_rows),
        "haptic_active_frame_count": sum(haptic_active_flags),
        "haptic_event_count": _edge_count(haptic_active_flags),
        "hardware_haptic_active_frame_count": sum(haptic_active_flags),
        "hardware_haptic_event_count": _edge_count(haptic_active_flags),
        "contact_enter_count": _count_event_type(recorded_events, "contact_enter"),
        "contact_exit_count": _count_event_type(recorded_events, "contact_exit"),
        "slip_active_frame_count": slip_active_frame_count,
        "slip_frame_count": slip_active_frame_count,
        "logical_slip_feedback_frame_count": slip_active_frame_count,
        "slip_reason_counts": slip_audit["slip_reason_counts"],
        "logical_slip_due_to_pinch_insufficient_count": slip_audit[
            "logical_slip_due_to_pinch_insufficient_count"
        ],
        "logical_slip_due_to_track_blocked_count": slip_audit[
            "logical_slip_due_to_track_blocked_count"
        ],
        "logical_blocked_feedback_frame_count": logical_blocked_feedback_frame_count,
        "blocked_force_active_count": logical_blocked_feedback_frame_count,
        "slip_start_count": _count_event_type(all_events, "slip_start"),
        "slip_end_count": _count_event_type(all_events, "slip_end"),
        "blocked_frame_count": sum(blocked_flags),
        "blocked_start_count": _count_event_type(all_events, "blocked_start")
        + _count_event_type(all_events, "blocked_force_start"),
        "blocked_end_count": _count_event_type(all_events, "blocked_end")
        + _count_event_type(all_events, "blocked_force_end"),
        "large_delta_frame_count": _large_delta_count(processed_rows),
        "tracker_invalid_frame_count": sum(not _bool(row.get("tracker_valid")) for row in processed_rows),
        "pinch_distance_min": _min_float(processed_rows, "pinch_distance"),
        "pinch_distance_mean": _mean_float(processed_rows, "pinch_distance"),
        "pinch_distance_max": _max_float(processed_rows, "pinch_distance"),
        "block_displacement_task": _block_displacement(processed_rows),
        "pinch_trajectory_range_task": _trajectory_range(
            processed_rows,
            "pinch_center_task_x",
            "pinch_center_task_y",
            "pinch_center_task_z",
        ),
        "block_trajectory_range_task": _trajectory_range(
            processed_rows,
            "block_center_task_x",
            "block_center_task_y",
            "block_center_task_z",
        ),
        "track_box_count": track_box_stats.total,
        "valid_track_box_count": track_box_stats.valid,
        "skipped_track_box_count": track_box_stats.skipped,
        "target_region_present": isinstance(trial_config.get("target_region"), dict),
        "trajectory_map_used_track_boxes": track_box_stats.valid > 0,
        "track_region_semantics": "block_center_feasible_region",
        "block_footprint_overlay_count": footprint_analysis["block_footprint_overlay_count"],
        "slip_footprint_overlay_count": footprint_analysis["slip_footprint_overlay_count"],
        "blocked_footprint_overlay_count": footprint_analysis["blocked_footprint_overlay_count"],
        "slip_frames_with_geometry_check": footprint_analysis[
            "slip_frames_with_geometry_check"
        ],
        "slip_frames_pinch_inside_block_count": footprint_analysis[
            "slip_frames_pinch_inside_block_count"
        ],
        "slip_frames_pinch_outside_block_count": footprint_analysis[
            "slip_frames_pinch_outside_block_count"
        ],
        "derived_event_counts": derived_event_counts,
        "generated_plots": [],
        "skipped_event_label_count": skipped_event_label_count,
        "warnings": warnings,
    }

    if not no_plots:
        plot_dir = Path(out_dir) if out_dir is not None else session_dir / "plots"
        summary["generated_plots"] = _write_plots_best_effort(
            plot_dir=plot_dir,
            processed_rows=processed_rows,
            haptic_rows=haptic_rows,
            trial_config=trial_config,
            times=times,
            events=all_events,
            event_label_limit=event_label_limit,
            overwrite=overwrite,
            warnings=warnings,
            max_footprint_overlays=max_footprint_overlays,
        )

    _write_json(summary_path, summary)
    return summary


def _write_plots_best_effort(
    *,
    plot_dir: Path,
    processed_rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    overwrite: bool,
    warnings: list[str],
    max_footprint_overlays: int,
) -> list[str]:
    try:
        plt = _load_pyplot()
    except Exception:
        warnings.append("matplotlib not available; plots were skipped")
        return []

    plot_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []
    plotters = [
        ("timeseries_xyz_with_events.png", _plot_timeseries_xyz),
        ("pinch_distance_with_events.png", _plot_pinch_distance),
        ("trajectory_track_map.png", _plot_trajectory_track_map),
        (
            "trajectory_track_map_with_block_footprint.png",
            _plot_trajectory_track_map_with_block_footprint,
        ),
        ("state_timeline.png", _plot_state_timeline),
        ("haptic_timeline.png", _plot_haptic_timeline),
    ]
    for filename, plotter in plotters:
        path = plot_dir / filename
        if path.exists() and not overwrite:
            warnings.append(f"{filename} exists and was not overwritten.")
            continue
        try:
            plotter(
                plt,
                path,
                processed_rows,
                haptic_rows,
                trial_config,
                times,
                events,
                event_label_limit,
                warnings,
                max_footprint_overlays,
            )
            generated.append(str(path))
        except Exception as exc:  # pragma: no cover - best effort guard
            warnings.append(f"{filename} generation failed: {exc}")
            try:
                plt.close("all")
            except Exception:
                pass
    return generated


def _plot_timeseries_xyz(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del haptic_rows, trial_config, warnings, max_footprint_overlays
    plt.figure(figsize=(11, 6))
    _plot_series(plt, times.axis_values, rows, "pinch_center_task_x", "pinch x")
    _plot_series(plt, times.axis_values, rows, "pinch_center_task_y", "pinch y")
    _plot_series(plt, times.axis_values, rows, "pinch_center_task_z", "pinch z")
    _plot_series(plt, times.axis_values, rows, "block_center_task_x", "block x")
    _plot_series(plt, times.axis_values, rows, "block_center_task_y", "block y")
    _plot_series(plt, times.axis_values, rows, "block_center_task_z", "block z")
    _annotate_events(plt, events, event_label_limit, times)
    plt.xlabel(times.axis_label)
    plt.ylabel("task coordinate")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_pinch_distance(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del haptic_rows, warnings, max_footprint_overlays
    plt.figure(figsize=(11, 5))
    _plot_series(plt, times.axis_values, rows, "pinch_distance", "pinch distance")
    threshold = trial_config.get("pinch_threshold")
    if isinstance(threshold, dict):
        for label, value in threshold.items():
            number = _float_or_none(value)
            if number is not None:
                plt.axhline(number, linestyle="--", label=f"threshold {label}")
    else:
        number = _float_or_none(threshold)
        if number is not None:
            plt.axhline(number, linestyle="--", label="pinch threshold")
    _annotate_events(plt, events, event_label_limit, times)
    plt.xlabel(times.axis_label)
    plt.ylabel("pinch distance")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_trajectory_track_map(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del haptic_rows, event_label_limit, max_footprint_overlays
    plt.figure(figsize=(7, 7))
    _plot_trajectory_track_map_contents(plt, rows, trial_config, times, events, warnings)
    plt.xlabel("task x")
    plt.ylabel("task y")
    plt.axis("equal")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_trajectory_track_map_with_block_footprint(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del haptic_rows, event_label_limit
    plt.figure(figsize=(7, 7))
    _plot_trajectory_track_map_contents(plt, rows, trial_config, times, events, warnings)
    _plot_block_footprint_overlays(
        plt,
        rows,
        trial_config,
        max_footprint_overlays=max_footprint_overlays,
        warnings=warnings,
    )
    plt.xlabel("task x")
    plt.ylabel("task y")
    plt.axis("equal")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_trajectory_track_map_contents(
    plt: Any,
    rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    pinch_x = _float_column(rows, "pinch_center_task_x")
    pinch_y = _float_column(rows, "pinch_center_task_y")
    block_x = _float_column(rows, "block_center_task_x")
    block_y = _float_column(rows, "block_center_task_y")
    plt.plot(pinch_x, pinch_y, label="pinch path", alpha=0.8)
    plt.plot(block_x, block_y, label="block path", alpha=0.8)
    _plot_track_geometry(plt, trial_config, warnings)
    _plot_configured_block_start(plt, trial_config, warnings)
    _plot_endpoint_markers(plt, block_x, block_y)
    _plot_event_points(plt, rows, times, events, warnings)


def _plot_state_timeline(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del haptic_rows, trial_config, events, event_label_limit, warnings, max_footprint_overlays
    plt.figure(figsize=(11, 6))
    state_specs = [
        ("tracker_valid", "tracker_valid"),
        ("pinch_valid", "pinch_valid"),
        ("slip_active", "slip_active"),
        ("blocked_force_active", "blocked_force_active"),
        ("large_delta", "large_delta"),
    ]
    ytick_positions: list[float] = []
    ytick_labels: list[str] = []
    for offset, (column, label) in enumerate(state_specs):
        values = [offset + (1.0 if _bool(row.get(column)) else 0.0) * 0.8 for row in rows]
        plt.step(_filled_times(times.axis_values), values, where="post", label=label)
        ytick_positions.append(offset + 0.4)
        ytick_labels.append(label)
    base = len(state_specs)
    for index, column in enumerate(("contact_state", "block_motion_state")):
        mapped = _categorical_values(rows, column)
        values = [base + index + (value * 0.1) for value in mapped]
        plt.plot(_filled_times(times.axis_values), values, label=column)
        ytick_positions.append(base + index + 0.2)
        ytick_labels.append(column)
    plt.yticks(ytick_positions, ytick_labels)
    plt.xlabel(times.axis_label)
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_haptic_timeline(
    plt: Any,
    path: Path,
    rows: list[dict[str, str]],
    haptic_rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    times: TimeSeries,
    events: list[dict[str, Any]],
    event_label_limit: int,
    warnings: list[str],
    max_footprint_overlays: int,
) -> None:
    del trial_config, events, event_label_limit, warnings, max_footprint_overlays
    plt.figure(figsize=(11, 5))
    source_rows = haptic_rows if haptic_rows else rows
    plot_times = times.axis_values[: len(source_rows)]
    active = [_haptic_row_active(row) for row in source_rows]
    slip = [_bool(row.get("slip_active")) for row in source_rows]
    blocked = [_bool(row.get("blocked_force_active")) for row in source_rows]
    plt.step(_filled_times(plot_times), [1 if value else 0 for value in active], where="post", label="haptic active")
    plt.step(_filled_times(plot_times), [1.2 if value else 0 for value in slip], where="post", label="slip")
    plt.step(_filled_times(plot_times), [1.4 if value else 0 for value in blocked], where="post", label="blocked force")
    plt.xlabel(times.axis_label)
    plt.yticks([0, 1, 1.2, 1.4], ["off", "haptic", "slip", "blocked"])
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _load_pyplot() -> Any:
    import matplotlib.pyplot as plt

    return plt


def _select_time_series(
    rows: list[dict[str, str]],
    requested_column: str,
    warnings: list[str],
    *,
    relative_time: bool,
) -> TimeSeries:
    candidates: list[str] = []
    for column in (requested_column, "sample_time", "trial_time", "raw_timestamp", "frame_index"):
        if column not in candidates:
            candidates.append(column)

    for column in candidates:
        values = [_float_or_none(row.get(column)) for row in rows]
        if any(value is not None for value in values):
            if column != requested_column:
                warnings.append(
                    f"time column '{requested_column}' unavailable or empty; using '{column}'."
                )
            warnings.append(f"time column used: {column}")
            if column == "frame_index":
                warnings.append("using frame_index as time axis because no time column was available.")
            return _time_series_with_axis(column, values, relative_time=relative_time)

    warnings.append("using frame_index as time axis because no time column was available.")
    return _time_series_with_axis(
        "frame_index",
        [float(index) for index, _ in enumerate(rows)],
        relative_time=relative_time,
    )


def _time_series_with_axis(
    column: str,
    values: list[float | None],
    *,
    relative_time: bool,
) -> TimeSeries:
    if not relative_time:
        return TimeSeries(
            column=column,
            values=values,
            axis_values=values,
            axis_mode="absolute",
            zero=None,
            axis_label=column,
        )

    time_zero = next((value for value in values if value is not None), None)
    axis_values = [
        None if value is None or time_zero is None else value - time_zero
        for value in values
    ]
    return TimeSeries(
        column=column,
        values=values,
        axis_values=axis_values,
        axis_mode="relative",
        zero=time_zero,
        axis_label="time since session start (s)",
    )


def _recorded_events(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "source": "recorded",
                "frame_index": _int_or_none(row.get("frame_index")),
                "time": _float_or_none(row.get("time")),
                "event_type": row.get("event_type", ""),
                "details": row.get("details_json", ""),
            }
        )
    return events


def _derive_events(
    rows: list[dict[str, str]],
    times: TimeSeries,
    recorded_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    derived: list[dict[str, Any]] = []
    _extend_edge_events(rows, times, "slip_active", "slip_start", "slip_end", derived, recorded_events)
    _extend_edge_events(
        rows,
        times,
        "blocked_force_active",
        "blocked_start",
        "blocked_end",
        derived,
        recorded_events,
    )
    haptic_flags = [_haptic_row_active(row) for row in rows]
    previous = False
    for index, active in enumerate(haptic_flags):
        event_type = None
        if active and not previous:
            event_type = "haptic_on"
        elif previous and not active:
            event_type = "haptic_off"
        previous = active
        if event_type is not None:
            event = _derived_event(index, times, event_type)
            if not _has_duplicate_event(event, recorded_events):
                derived.append(event)
    return derived


def _extend_edge_events(
    rows: list[dict[str, str]],
    times: TimeSeries,
    column: str,
    start_type: str,
    end_type: str,
    derived: list[dict[str, Any]],
    recorded_events: list[dict[str, Any]],
) -> None:
    previous = False
    for index, row in enumerate(rows):
        active = _bool(row.get(column))
        event_type = None
        if active and not previous:
            event_type = start_type
        elif previous and not active:
            event_type = end_type
        previous = active
        if event_type is not None:
            event = _derived_event(index, times, event_type)
            if not _has_duplicate_event(event, recorded_events):
                derived.append(event)


def _derived_event(index: int, times: TimeSeries, event_type: str) -> dict[str, Any]:
    return {
        "source": "derived",
        "frame_index": index,
        "time": times.values[index] if index < len(times.values) else None,
        "event_type": event_type,
        "details": "",
    }


def _has_duplicate_event(event: dict[str, Any], recorded_events: list[dict[str, Any]]) -> bool:
    for recorded in recorded_events:
        if recorded.get("event_type") != event.get("event_type"):
            continue
        frame_a = event.get("frame_index")
        frame_b = recorded.get("frame_index")
        if frame_a is not None and frame_b is not None and frame_a == frame_b:
            return True
        time_a = event.get("time")
        time_b = recorded.get("time")
        if time_a is not None and time_b is not None and abs(time_a - time_b) < 1e-6:
            return True
    return False


def _haptic_active_flags(
    haptic_rows: list[dict[str, str]],
    processed_rows: list[dict[str, str]],
) -> tuple[list[bool], str | None]:
    if haptic_rows:
        return [_haptic_row_active(row) for row in haptic_rows], None
    return [_haptic_row_active(row) for row in processed_rows], (
        "haptic.csv missing or empty; haptic active statistics used processed_frames.csv fallback."
    )


def _haptic_row_active(row: dict[str, str]) -> bool:
    haptic_state = str(row.get("haptic_state", ""))
    command_type = str(row.get("command_type", ""))
    if haptic_state not in INACTIVE_HAPTIC_VALUES:
        return True
    return command_type not in INACTIVE_HAPTIC_VALUES


def _blocked_flags(rows: list[dict[str, str]]) -> tuple[list[bool], str | None]:
    values = [row.get("blocked_force_active", "") for row in rows]
    if any(str(value).strip() != "" for value in values):
        return [_bool(value) for value in values], None
    flags = [
        "BLOCKED"
        in " ".join(
            str(row.get(column, ""))
            for column in ("stop_reason", "block_motion_state", "contact_state")
        ).upper()
        for row in rows
    ]
    return flags, "blocked_force_active unavailable; blocked_frame_count used state-string fallback."


def _block_footprint_analysis(
    rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    *,
    max_footprint_overlays: int,
    warnings: list[str],
) -> dict[str, Any]:
    limit = max(0, int(max_footprint_overlays))
    block_size, _ = _resolve_block_size(trial_config, rows)
    slip_rows = [row for row in rows if _bool(row.get("slip_active"))]
    geometry_check = _slip_geometry_check(slip_rows, block_size, warnings)
    if block_size is None:
        _append_warning_once(
            warnings,
            "block_size missing; block footprint overlays and slip geometry checks were skipped.",
        )
        return {
            "overlays": [],
            "block_footprint_overlay_count": 0,
            "slip_footprint_overlay_count": 0,
            "blocked_footprint_overlay_count": 0,
            **geometry_check,
        }

    overlays: list[BlockFootprintOverlay] = []
    configured_center = _point3(trial_config.get("block_initial_center_task"))
    if configured_center is not None:
        overlays.append(
            BlockFootprintOverlay(
                kind="configured",
                label="configured block footprint",
                center=configured_center,
                size=block_size,
            )
        )

    block_centers = [
        center
        for center in (_row_block_center(row) for row in rows)
        if center is not None
    ]
    if block_centers:
        overlays.append(
            BlockFootprintOverlay(
                kind="start",
                label="block start footprint",
                center=block_centers[0],
                size=block_size,
            )
        )
        overlays.append(
            BlockFootprintOverlay(
                kind="end",
                label="block end footprint",
                center=block_centers[-1],
                size=block_size,
            )
        )

    slip_overlays = [
        BlockFootprintOverlay(
            kind="slip",
            label="sampled slip footprint",
            center=center,
            size=block_size,
        )
        for center in _sample_evenly(
            [_row_block_center(row) for row in slip_rows],
            limit,
        )
        if center is not None
    ]
    blocked_overlays = [
        BlockFootprintOverlay(
            kind="blocked",
            label="sampled blocked footprint",
            center=center,
            size=block_size,
        )
        for center in _sample_evenly(
            [_row_block_center(row) for row in rows if _row_track_blocked(row)],
            limit,
        )
        if center is not None
    ]
    overlays.extend(slip_overlays)
    overlays.extend(blocked_overlays)
    return {
        "overlays": overlays,
        "block_footprint_overlay_count": len(overlays),
        "slip_footprint_overlay_count": len(slip_overlays),
        "blocked_footprint_overlay_count": len(blocked_overlays),
        **geometry_check,
    }


def _slip_geometry_check(
    slip_rows: list[dict[str, str]],
    block_size: list[float] | None,
    warnings: list[str],
) -> dict[str, int]:
    checked = 0
    inside = 0
    outside = 0
    if block_size is None:
        return {
            "slip_frames_with_geometry_check": 0,
            "slip_frames_pinch_inside_block_count": 0,
            "slip_frames_pinch_outside_block_count": 0,
        }

    for row in slip_rows:
        pinch = _row_pinch_center(row)
        block = _row_block_center(row)
        if pinch is None or block is None:
            continue
        checked += 1
        if _point_inside_aabb(pinch, block, block_size):
            inside += 1
        else:
            outside += 1
    if outside > 0:
        _append_warning_once(
            warnings,
            "Some slip_active frames have pinch_center_task outside the reconstructed block AABB; inspect contact/slip recording semantics.",
        )
    return {
        "slip_frames_with_geometry_check": checked,
        "slip_frames_pinch_inside_block_count": inside,
        "slip_frames_pinch_outside_block_count": outside,
    }


def _slip_reason_audit(rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    pinch_insufficient = 0
    track_blocked = 0
    for row in rows:
        if not _bool(row.get("slip_active")):
            continue
        reason = _enum_name(row.get("slip_reason"))
        if reason is None:
            reason = _enum_name(row.get("stop_reason"))
        if reason is None:
            reason = "UNKNOWN"
        counts[reason] = counts.get(reason, 0) + 1
        if reason == "PINCH_INSUFFICIENT":
            pinch_insufficient += 1
        elif reason == "TRACK_BLOCKED":
            track_blocked += 1
    return {
        "slip_reason_counts": counts,
        "logical_slip_due_to_pinch_insufficient_count": pinch_insufficient,
        "logical_slip_due_to_track_blocked_count": track_blocked,
    }


def _resolve_block_size(
    trial_config: dict[str, Any],
    rows: list[dict[str, str]],
) -> tuple[list[float] | None, str | None]:
    for key in ("block_size", "block_size_task"):
        size = _size3(trial_config.get(key))
        if size is not None:
            return size, f"trial_config.{key}"
    for row in rows:
        for key in ("block_size", "block_size_task"):
            size = _size3(row.get(key))
            if size is not None:
                return size, f"processed_frames.{key}"
        size = _size_from_columns(row, ("block_size_x", "block_size_y", "block_size_z"))
        if size is not None:
            return size, "processed_frames.block_size_xyz"
        size = _size_from_columns(
            row,
            ("block_size_task_x", "block_size_task_y", "block_size_task_z"),
        )
        if size is not None:
            return size, "processed_frames.block_size_task_xyz"
    return None, None


def _size_from_columns(row: dict[str, str], columns: tuple[str, str, str]) -> list[float] | None:
    values = [_float_or_none(row.get(column)) for column in columns]
    if any(value is None or value <= 0.0 for value in values):
        return None
    return [float(values[0]), float(values[1]), float(values[2])]


def _size3(value: Any) -> list[float] | None:
    if isinstance(value, int | float):
        number = _float_or_none(value)
        if number is not None and number > 0.0:
            return [number, number, number]
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = _float_or_none(text)
        if parsed is not None and parsed > 0.0:
            return [parsed, parsed, parsed]
        if text.startswith("[") or text.startswith("{"):
            try:
                return _size3(json.loads(text))
            except json.JSONDecodeError:
                return None
        parts = [part.strip() for part in text.split(",")]
        if len(parts) == 3:
            values = [_float_or_none(part) for part in parts]
            if not any(value is None or value <= 0.0 for value in values):
                return [float(values[0]), float(values[1]), float(values[2])]
        return None
    if isinstance(value, dict):
        candidates = [
            [value.get("x"), value.get("y"), value.get("z")],
            [value.get("width"), value.get("depth"), value.get("height")],
        ]
        for candidate in candidates:
            size = _size3(candidate)
            if size is not None:
                return size
        return None
    if isinstance(value, list | tuple) and len(value) >= 3:
        values = [_float_or_none(item) for item in value[:3]]
        if not any(item is None or item <= 0.0 for item in values):
            return [float(values[0]), float(values[1]), float(values[2])]
    return None


def _row_block_center(row: dict[str, str]) -> list[float] | None:
    return _point3(
        [
            row.get("block_center_task_x"),
            row.get("block_center_task_y"),
            row.get("block_center_task_z"),
        ]
    )


def _row_pinch_center(row: dict[str, str]) -> list[float] | None:
    return _point3(
        [
            row.get("pinch_center_task_x"),
            row.get("pinch_center_task_y"),
            row.get("pinch_center_task_z"),
        ]
    )


def _row_track_blocked(row: dict[str, str]) -> bool:
    if _bool(row.get("blocked_force_active")):
        return True
    return "TRACK_BLOCKED" in str(row.get("stop_reason", "")).upper()


def _point_inside_aabb(point: list[float], center: list[float], size: list[float]) -> bool:
    for index in range(3):
        half = size[index] * 0.5
        if point[index] < center[index] - half - 1e-9:
            return False
        if point[index] > center[index] + half + 1e-9:
            return False
    return True


def _sample_evenly(values: list[Any], limit: int) -> list[Any]:
    finite_values = [value for value in values if value is not None]
    if limit <= 0:
        return []
    if len(finite_values) <= limit:
        return finite_values
    if limit == 1:
        return [finite_values[0]]
    step = (len(finite_values) - 1) / float(limit - 1)
    indices = [round(index * step) for index in range(limit)]
    return [finite_values[int(index)] for index in indices]


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    text = text.upper()
    if text in {"", "NONE", "OFF", "FALSE", "0"}:
        return None
    return text


def _track_box_stats(trial_config: dict[str, Any]) -> TrackBoxStats:
    track_boxes = trial_config.get("track_boxes")
    if not isinstance(track_boxes, list):
        return TrackBoxStats(total=0, valid=0, skipped=0, skipped_indices=[])
    skipped_indices: list[int] = []
    valid = 0
    for index, box in enumerate(track_boxes):
        if isinstance(box, dict) and _normalize_bounds(box) is not None:
            valid += 1
        else:
            skipped_indices.append(index)
    return TrackBoxStats(
        total=len(track_boxes),
        valid=valid,
        skipped=len(skipped_indices),
        skipped_indices=skipped_indices,
    )


def _extend_track_box_warnings(stats: TrackBoxStats, warnings: list[str]) -> None:
    for index in stats.skipped_indices:
        _append_warning_once(warnings, f"track_boxes[{index}] could not be parsed and was skipped.")


def _append_warning_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


def _plot_series(
    plt: Any,
    times: list[float | None],
    rows: list[dict[str, str]],
    column: str,
    label: str,
) -> None:
    pairs = [
        (time, _float_or_none(row.get(column)))
        for time, row in zip(times, rows)
        if time is not None and _float_or_none(row.get(column)) is not None
    ]
    if not pairs:
        return
    x, y = zip(*pairs)
    plt.plot(x, y, label=label)


def _annotate_events(
    plt: Any,
    events: list[dict[str, Any]],
    event_label_limit: int,
    times: TimeSeries,
) -> None:
    labeled = 0
    for event in events:
        if event["event_type"] not in KEY_EVENT_TYPES or event.get("time") is None:
            continue
        event_time = _axis_time(event["time"], times)
        if event_time is None:
            continue
        color = "0.75" if labeled >= event_label_limit else "0.55"
        plt.axvline(event_time, color=color, linewidth=0.8, alpha=0.5)
        if labeled < event_label_limit:
            plt.text(
                event_time,
                0.98,
                event["event_type"],
                rotation=90,
                transform=plt.gca().get_xaxis_transform(),
                va="top",
                fontsize=7,
            )
        labeled += 1


def _axis_time(value: Any, times: TimeSeries) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if times.axis_mode == "relative" and times.zero is not None:
        return number - times.zero
    return number


def _plot_track_geometry(plt: Any, trial_config: dict[str, Any], warnings: list[str]) -> None:
    # Prefer MapConfig-style track_boxes so trajectory maps show the real
    # multi-segment layout; older post-hoc sessions fall back to coarse bounds.
    if _plot_track_boxes(plt, trial_config, warnings):
        return
    warnings.append("trial_config.track_boxes missing or unusable; trajectory map used track bounds fallback.")
    _plot_track_bounds(plt, trial_config, warnings)


def _plot_track_boxes(plt: Any, trial_config: dict[str, Any], warnings: list[str]) -> bool:
    track_boxes = trial_config.get("track_boxes")
    if not isinstance(track_boxes, list) or not track_boxes:
        return False
    valid_boxes: list[dict[str, Any]] = []
    for index, box in enumerate(track_boxes):
        bounds = _normalize_bounds(box)
        if bounds is None:
            _append_warning_once(warnings, f"track_boxes[{index}] could not be parsed and was skipped.")
            continue
        valid_boxes.append({"payload": box, "bounds": bounds})
    if not valid_boxes:
        return False

    valid_boxes.sort(key=lambda item: _box_sort_key(item["payload"]))
    for index, item in enumerate(valid_boxes):
        box = item["payload"]
        bounds = item["bounds"]
        min_point = bounds["min"]
        max_point = bounds["max"]
        xs = [min_point[0], max_point[0], max_point[0], min_point[0], min_point[0]]
        ys = [min_point[1], min_point[1], max_point[1], max_point[1], min_point[1]]
        label = "track boxes" if index == 0 else None
        plt.fill(xs, ys, alpha=0.12, color="tab:gray", label=label)
        plt.plot(xs, ys, color="tab:gray", linewidth=0.9)
        if index < 20:
            cx = (min_point[0] + max_point[0]) * 0.5
            cy = (min_point[1] + max_point[1]) * 0.5
            text = str(box.get("order", box.get("id", index)))
            plt.text(cx, cy, text, ha="center", va="center", fontsize=7, color="black")
    _plot_target_region(plt, trial_config, warnings)
    return True


def _plot_target_region(plt: Any, trial_config: dict[str, Any], warnings: list[str]) -> None:
    target = trial_config.get("target_region")
    if not isinstance(target, dict):
        return
    bounds = _normalize_bounds(target)
    if bounds is None:
        warnings.append("target_region could not be parsed and was skipped.")
        return
    min_point = bounds["min"]
    max_point = bounds["max"]
    xs = [min_point[0], max_point[0], max_point[0], min_point[0], min_point[0]]
    ys = [min_point[1], min_point[1], max_point[1], max_point[1], min_point[1]]
    plt.plot(xs, ys, linestyle="--", linewidth=1.8, color="tab:green", label="target region")


def _plot_configured_block_start(
    plt: Any,
    trial_config: dict[str, Any],
    warnings: list[str],
) -> None:
    point = _point3(trial_config.get("block_initial_center_task"))
    if point is None:
        return
    plt.scatter([point[0]], [point[1]], marker="*", s=90, label="configured block start")


def _plot_block_footprint_overlays(
    plt: Any,
    rows: list[dict[str, str]],
    trial_config: dict[str, Any],
    *,
    max_footprint_overlays: int,
    warnings: list[str],
) -> None:
    analysis = _block_footprint_analysis(
        rows,
        trial_config,
        max_footprint_overlays=max_footprint_overlays,
        warnings=warnings,
    )
    overlays = analysis["overlays"]
    if not overlays:
        return
    styles = {
        "configured": {"edgecolor": "tab:blue", "linestyle": "-", "alpha": 0.35},
        "start": {"edgecolor": "tab:cyan", "linestyle": "-", "alpha": 0.35},
        "end": {"edgecolor": "tab:brown", "linestyle": "-", "alpha": 0.35},
        "slip": {"edgecolor": "tab:red", "linestyle": "--", "alpha": 0.22},
        "blocked": {"edgecolor": "tab:orange", "linestyle": ":", "alpha": 0.24},
    }
    labels_used: set[str] = set()
    for overlay in overlays:
        style = styles.get(overlay.kind, {"edgecolor": "black", "linestyle": "-", "alpha": 0.2})
        _draw_block_footprint(
            plt,
            overlay.center,
            overlay.size,
            label=overlay.label if overlay.label not in labels_used else None,
            **style,
        )
        labels_used.add(overlay.label)


def _draw_block_footprint(
    plt: Any,
    center: list[float],
    size: list[float],
    *,
    label: str | None,
    edgecolor: str,
    linestyle: str,
    alpha: float,
) -> None:
    half_x = size[0] * 0.5
    half_y = size[1] * 0.5
    x0 = center[0] - half_x
    x1 = center[0] + half_x
    y0 = center[1] - half_y
    y1 = center[1] + half_y
    xs = [x0, x1, x1, x0, x0]
    ys = [y0, y0, y1, y1, y0]
    plt.fill(xs, ys, facecolor=edgecolor, edgecolor=edgecolor, linestyle=linestyle, alpha=alpha, label=label)
    plt.plot(xs, ys, color=edgecolor, linestyle=linestyle, linewidth=1.0, alpha=min(1.0, alpha + 0.35))


def _plot_track_bounds(plt: Any, trial_config: dict[str, Any], warnings: list[str]) -> None:
    bounds = _find_track_bounds(trial_config)
    if bounds is None:
        warnings.append("track bounds could not be recognized; trajectory map omitted track boundary.")
        return
    min_point = bounds["min"]
    max_point = bounds["max"]
    x0, y0 = min_point[0], min_point[1]
    x1, y1 = max_point[0], max_point[1]
    xs = [x0, x1, x1, x0, x0]
    ys = [y0, y0, y1, y1, y0]
    plt.plot(xs, ys, linestyle="--", color="black", label="track bounds")


def _box_sort_key(box: dict[str, Any]) -> tuple[int, str]:
    order = _int_or_none(box.get("order"))
    if order is None:
        return (10_000_000, str(box.get("id", "")))
    return (order, str(box.get("id", "")))


def _find_track_bounds(payload: Any) -> dict[str, list[float]] | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("track_bounds_task"),
        payload.get("track_bounds"),
        payload.get("track_region"),
        payload.get("bounds"),
        _nested(payload, "scene_auto", "track_bounds"),
        _nested(payload, "scene_auto", "track_bounds_task"),
    ]
    for candidate in candidates:
        bounds = _normalize_bounds(candidate)
        if bounds is not None:
            return bounds
    return None


def _normalize_bounds(candidate: Any) -> dict[str, list[float]] | None:
    if not isinstance(candidate, dict):
        return None
    if "min" in candidate and "max" in candidate:
        min_point = _point3(candidate.get("min"))
        max_point = _point3(candidate.get("max"))
        if min_point is not None and max_point is not None:
            return {"min": min_point, "max": max_point}
    if "minimum" in candidate and "maximum" in candidate:
        min_point = _point3(candidate.get("minimum"))
        max_point = _point3(candidate.get("maximum"))
        if min_point is not None and max_point is not None:
            return {"min": min_point, "max": max_point}
    if "center" in candidate and "size" in candidate:
        center = _point3(candidate.get("center"))
        size = _point3(candidate.get("size"))
        if center is not None and size is not None:
            return {
                "min": [center[index] - size[index] / 2.0 for index in range(3)],
                "max": [center[index] + size[index] / 2.0 for index in range(3)],
            }
    boxes = candidate.get("boxes")
    if isinstance(boxes, list) and boxes:
        normalized_boxes = [_normalize_bounds(box) for box in boxes]
        normalized_boxes = [box for box in normalized_boxes if box is not None]
        if normalized_boxes:
            return {
                "min": [
                    min(box["min"][index] for box in normalized_boxes)
                    for index in range(3)
                ],
                "max": [
                    max(box["max"][index] for box in normalized_boxes)
                    for index in range(3)
                ],
            }
    return None


def _plot_endpoint_markers(plt: Any, block_x: list[float], block_y: list[float]) -> None:
    if block_x and block_y:
        plt.scatter([block_x[0]], [block_y[0]], marker="o", label="block start")
        plt.scatter([block_x[-1]], [block_y[-1]], marker="x", label="block end")


def _plot_event_points(
    plt: Any,
    rows: list[dict[str, str]],
    times: TimeSeries,
    events: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    event_groups = {
        "blocked": [event for event in events if "blocked" in event["event_type"]],
        "slip": [event for event in events if event["event_type"].startswith("slip_")],
        "haptic": [event for event in events if event["event_type"].startswith("haptic_")],
    }
    row_by_frame = {
        _int_or_none(row.get("frame_index")): row
        for row in rows
        if _int_or_none(row.get("frame_index")) is not None
    }
    for label, group in event_groups.items():
        xs: list[float] = []
        ys: list[float] = []
        for event in group:
            row = _row_for_event(event, rows, row_by_frame, times)
            if row is None:
                warnings.append(f"could not match event point for {event['event_type']}.")
                continue
            x_column = "block_center_task_x" if label == "blocked" else "pinch_center_task_x"
            y_column = "block_center_task_y" if label == "blocked" else "pinch_center_task_y"
            x = _float_or_none(row.get(x_column))
            y = _float_or_none(row.get(y_column))
            if x is None or y is None:
                warnings.append(f"missing trajectory coordinates for {event['event_type']}.")
                continue
            xs.append(x)
            ys.append(y)
        if xs:
            plt.scatter(xs, ys, s=16, label=f"{label} events")


def _row_for_event(
    event: dict[str, Any],
    rows: list[dict[str, str]],
    row_by_frame: dict[int, dict[str, str]],
    times: TimeSeries,
) -> dict[str, str] | None:
    frame_index = event.get("frame_index")
    if isinstance(frame_index, int) and frame_index in row_by_frame:
        return row_by_frame[frame_index]
    event_time = event.get("time")
    if event_time is None:
        return None
    best_index = None
    best_delta = math.inf
    for index, time in enumerate(times.values):
        if time is None:
            continue
        delta = abs(time - event_time)
        if delta < best_delta:
            best_delta = delta
            best_index = index
    if best_index is None:
        return None
    return rows[best_index]


def _read_json_optional(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"optional file missing: {path.name}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"could not read {path.name}: {exc}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_csv_optional(path: Path, warnings: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        warnings.append(f"optional file missing: {path.name}")
        return []
    try:
        return _read_csv(path)
    except Exception as exc:
        warnings.append(f"could not read {path.name}: {exc}")
        return []


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _extend_warning_list(warnings: list[str], values: Any) -> None:
    if isinstance(values, list):
        for value in values:
            text = str(value)
            if text not in warnings:
                warnings.append(text)
    elif values:
        text = str(values)
        if text not in warnings:
            warnings.append(text)


def _sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            event.get("time") is None,
            event.get("time") if event.get("time") is not None else math.inf,
            event.get("frame_index") if event.get("frame_index") is not None else math.inf,
        ),
    )


def _count_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type", ""))
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _count_event_type(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(event.get("event_type") == event_type for event in events)


def _edge_count(flags: list[bool]) -> int:
    previous = False
    count = 0
    for active in flags:
        if active and not previous:
            count += 1
        previous = active
    return count


def _large_delta_count(rows: list[dict[str, str]]) -> int:
    count = 0
    for row in rows:
        if _bool(row.get("large_delta")) or "LARGE_DELTA" in str(row.get("stop_reason", "")).upper():
            count += 1
    return count


def _block_displacement(rows: list[dict[str, str]]) -> dict[str, float | None]:
    coords = [
        (
            _float_or_none(row.get("block_center_task_x")),
            _float_or_none(row.get("block_center_task_y")),
            _float_or_none(row.get("block_center_task_z")),
        )
        for row in rows
    ]
    coords = [coord for coord in coords if all(value is not None for value in coord)]
    if len(coords) < 2:
        return {"dx": None, "dy": None, "dz": None, "distance": None}
    first = coords[0]
    last = coords[-1]
    dx = float(last[0] - first[0])
    dy = float(last[1] - first[1])
    dz = float(last[2] - first[2])
    return {"dx": dx, "dy": dy, "dz": dz, "distance": math.sqrt(dx * dx + dy * dy + dz * dz)}


def _trajectory_range(
    rows: list[dict[str, str]],
    x_column: str,
    y_column: str,
    z_column: str,
) -> dict[str, float | int | None]:
    xs = _float_column(rows, x_column)
    ys = _float_column(rows, y_column)
    zs = _float_column(rows, z_column)
    return {
        "point_count": min(len(xs), len(ys), len(zs)),
        "x_min": min(xs) if xs else None,
        "x_max": max(xs) if xs else None,
        "y_min": min(ys) if ys else None,
        "y_max": max(ys) if ys else None,
        "z_min": min(zs) if zs else None,
        "z_max": max(zs) if zs else None,
    }


def _min_float(rows: list[dict[str, str]], column: str) -> float | None:
    values = _float_column(rows, column)
    return min(values) if values else None


def _mean_float(rows: list[dict[str, str]], column: str) -> float | None:
    values = _float_column(rows, column)
    return sum(values) / len(values) if values else None


def _max_float(rows: list[dict[str, str]], column: str) -> float | None:
    values = _float_column(rows, column)
    return max(values) if values else None


def _float_column(rows: list[dict[str, str]], column: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = _float_or_none(row.get(column))
        if value is not None:
            values.append(value)
    return values


def _categorical_values(rows: list[dict[str, str]], column: str) -> list[int]:
    mapping: dict[str, int] = {}
    values: list[int] = []
    for row in rows:
        key = str(row.get(column, ""))
        if key not in mapping:
            mapping[key] = len(mapping)
        values.append(mapping[key])
    return values


def _filled_times(values: list[float | None]) -> list[float]:
    return [float(index) if value is None else value for index, value in enumerate(values)]


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    return text.lower() in {"1", "true", "yes", "y", "on"}


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _point3(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("z")]
    if not isinstance(value, list | tuple) or len(value) < 2:
        return None
    values = [_float_or_none(item) for item in value[:3]]
    if len(values) < 3 or any(item is None for item in values):
        return None
    return [float(values[0]), float(values[1]), float(values[2])]


def _error_summary(session_dir: Path, message: str, warnings: list[str]) -> dict[str, Any]:
    warnings.append(message)
    return {
        "status": "ERROR",
        "session_dir": str(session_dir),
        "error_message": message,
        "generated_plots": [],
        "warnings": warnings,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze a recorded session directory.")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--event-label-limit", type=int, default=40)
    parser.add_argument("--max-footprint-overlays", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    time_axis_group = parser.add_mutually_exclusive_group()
    time_axis_group.add_argument(
        "--relative-time",
        dest="relative_time",
        action="store_true",
        default=True,
        help="Use selected time minus the first finite selected time on plot x-axes (default).",
    )
    time_axis_group.add_argument(
        "--absolute-time",
        dest="relative_time",
        action="store_false",
        help="Use the selected time column directly on plot x-axes.",
    )
    parser.add_argument(
        "--time-column",
        default="sample_time",
        choices=("sample_time", "trial_time", "raw_timestamp"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = analyze_session(
        session_dir=Path(args.session_dir),
        out_dir=Path(args.out_dir) if args.out_dir is not None else None,
        no_plots=args.no_plots,
        event_label_limit=args.event_label_limit,
        overwrite=args.overwrite,
        time_column=args.time_column,
        relative_time=args.relative_time,
        max_footprint_overlays=args.max_footprint_overlays,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary.get("status") == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
