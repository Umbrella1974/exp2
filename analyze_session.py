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


def analyze_session(
    *,
    session_dir: Path,
    out_dir: Path | None = None,
    no_plots: bool = False,
    event_label_limit: int = 40,
    overwrite: bool = False,
    time_column: str = "sample_time",
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

    times = _select_time_series(processed_rows, time_column, warnings)
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
        "total_processed_frames": len(processed_rows),
        "total_events": len(recorded_events),
        "total_haptic_records": len(haptic_rows),
        "haptic_active_frame_count": sum(haptic_active_flags),
        "haptic_event_count": _edge_count(haptic_active_flags),
        "contact_enter_count": _count_event_type(recorded_events, "contact_enter"),
        "contact_exit_count": _count_event_type(recorded_events, "contact_exit"),
        "slip_active_frame_count": sum(_bool(row.get("slip_active")) for row in processed_rows),
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
        "track_box_count": _track_box_count(trial_config),
        "target_region_present": isinstance(trial_config.get("target_region"), dict),
        "trajectory_map_used_track_boxes": _has_track_boxes(trial_config),
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
) -> None:
    del haptic_rows, trial_config, warnings
    plt.figure(figsize=(11, 6))
    _plot_series(plt, times.values, rows, "pinch_center_task_x", "pinch x")
    _plot_series(plt, times.values, rows, "pinch_center_task_y", "pinch y")
    _plot_series(plt, times.values, rows, "pinch_center_task_z", "pinch z")
    _plot_series(plt, times.values, rows, "block_center_task_x", "block x")
    _plot_series(plt, times.values, rows, "block_center_task_y", "block y")
    _plot_series(plt, times.values, rows, "block_center_task_z", "block z")
    _annotate_events(plt, events, event_label_limit)
    plt.xlabel(times.column)
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
) -> None:
    del haptic_rows, warnings
    plt.figure(figsize=(11, 5))
    _plot_series(plt, times.values, rows, "pinch_distance", "pinch distance")
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
    _annotate_events(plt, events, event_label_limit)
    plt.xlabel(times.column)
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
) -> None:
    del haptic_rows, event_label_limit
    plt.figure(figsize=(7, 7))
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
    plt.xlabel("task x")
    plt.ylabel("task y")
    plt.axis("equal")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


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
) -> None:
    del haptic_rows, trial_config, events, event_label_limit, warnings
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
        plt.step(_filled_times(times.values), values, where="post", label=label)
        ytick_positions.append(offset + 0.4)
        ytick_labels.append(label)
    base = len(state_specs)
    for index, column in enumerate(("contact_state", "block_motion_state")):
        mapped = _categorical_values(rows, column)
        values = [base + index + (value * 0.1) for value in mapped]
        plt.plot(_filled_times(times.values), values, label=column)
        ytick_positions.append(base + index + 0.2)
        ytick_labels.append(column)
    plt.yticks(ytick_positions, ytick_labels)
    plt.xlabel(times.column)
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
) -> None:
    del trial_config, events, event_label_limit, warnings
    plt.figure(figsize=(11, 5))
    source_rows = haptic_rows if haptic_rows else rows
    plot_times = times.values[: len(source_rows)]
    active = [_haptic_row_active(row) for row in source_rows]
    slip = [_bool(row.get("slip_active")) for row in source_rows]
    blocked = [_bool(row.get("blocked_force_active")) for row in source_rows]
    plt.step(_filled_times(plot_times), [1 if value else 0 for value in active], where="post", label="haptic active")
    plt.step(_filled_times(plot_times), [1.2 if value else 0 for value in slip], where="post", label="slip")
    plt.step(_filled_times(plot_times), [1.4 if value else 0 for value in blocked], where="post", label="blocked force")
    plt.xlabel(times.column)
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
            return TimeSeries(column=column, values=values)

    warnings.append("using frame_index as time axis because no time column was available.")
    return TimeSeries(
        column="frame_index",
        values=[float(index) for index, _ in enumerate(rows)],
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


def _has_track_boxes(trial_config: dict[str, Any]) -> bool:
    track_boxes = trial_config.get("track_boxes")
    return isinstance(track_boxes, list) and any(
        isinstance(box, dict) and _normalize_bounds(box) is not None
        for box in track_boxes
    )


def _track_box_count(trial_config: dict[str, Any]) -> int:
    track_boxes = trial_config.get("track_boxes")
    if not isinstance(track_boxes, list):
        return 0
    return len(track_boxes)


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


def _annotate_events(plt: Any, events: list[dict[str, Any]], event_label_limit: int) -> None:
    labeled = 0
    for event in events:
        if event["event_type"] not in KEY_EVENT_TYPES or event.get("time") is None:
            continue
        color = "0.75" if labeled >= event_label_limit else "0.55"
        plt.axvline(event["time"], color=color, linewidth=0.8, alpha=0.5)
        if labeled < event_label_limit:
            plt.text(
                event["time"],
                0.98,
                event["event_type"],
                rotation=90,
                transform=plt.gca().get_xaxis_transform(),
                va="top",
                fontsize=7,
            )
        labeled += 1


def _plot_track_geometry(plt: Any, trial_config: dict[str, Any], warnings: list[str]) -> None:
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
            warnings.append(f"track_boxes[{index}] could not be parsed and was skipped.")
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
    parser.add_argument("--overwrite", action="store_true")
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
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary.get("status") == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
