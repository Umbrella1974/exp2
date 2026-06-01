"""Live table-line calibration runner.

The runner converts raw MANUS/Vive frames into calibration samples only. It
does not start trials, blocks, maps, or haptic hardware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from calibration_geometry import build_axes_from_table_lines
from calibration_io import (
    FormalCalibration,
    PlaneFitRecord,
    save_calibration,
    validate_calibration,
)
from calibration_sampling import (
    build_calibration_line_record,
    build_calibration_point_record,
    extract_calibration_point_from_sample,
)
from device_frame_models import DeviceAdapterConfig
from live_raw_stream import LiveRawFrame
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_manus_vive_parser import parse_raw_manus_vive_frame


RAW_JSONL_SIMULATED_LIVE_WARNING = (
    "This calibration was collected from raw JSONL simulated live mode; use live "
    "stream for formal subject calibration."
)
LIVE_STREAM_WARNING = "This calibration was collected from live stream."


@dataclass(frozen=True)
class CalibrationSegmentSpec:
    """One live calibration segment."""

    label: str
    prompt: str
    duration_seconds: float
    min_samples: int
    segment_type: str
    sequence_index: int = 0


@dataclass(frozen=True)
class CalibrationLiveConfig:
    """Configuration for live table-line calibration."""

    calibration_id: str = ""
    point_source: str = "tracker_position_world"
    sample_duration_seconds: float = 5.0
    min_samples: int = 10
    min_line_length: float = 0.10
    up_hint: list[float] = field(default_factory=lambda: [0.0, 0.0, 1.0])
    timestamp_scale: float = 0.001
    output_path: Path = Path("data/calibration/live_table_calibration.json")
    notes: str | None = None
    require_enter_between_segments: bool = True
    auto_advance: bool = False
    collection_mode: str = "raw_jsonl_simulated_live"
    source_path: str | None = None
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    print_every: int = 30
    queue_cleared_before_segment: bool = False


@dataclass(frozen=True)
class CalibrationLiveResult:
    """Result of one live table-line calibration run."""

    calibration: FormalCalibration | None
    segment_summaries: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    live_metrics_summary: dict[str, Any]


ProgressCallback = Callable[[dict[str, Any]], None]
BeforeSegmentCallback = Callable[[CalibrationSegmentSpec], None]


def default_segment_specs(config: CalibrationLiveConfig) -> list[CalibrationSegmentSpec]:
    """Return the four standard table-line calibration segments."""

    return [
        CalibrationSegmentSpec(
            label="origin",
            prompt="Keep the calibration point still at the table origin.",
            duration_seconds=config.sample_duration_seconds,
            min_samples=config.min_samples,
            segment_type="static_point",
            sequence_index=0,
        ),
        CalibrationSegmentSpec(
            label="long_axis_line",
            prompt="Move along the long table edge / intended x direction.",
            duration_seconds=config.sample_duration_seconds,
            min_samples=config.min_samples,
            segment_type="line",
            sequence_index=1,
        ),
        CalibrationSegmentSpec(
            label="width_axis_line",
            prompt="Move along the table width direction.",
            duration_seconds=config.sample_duration_seconds,
            min_samples=config.min_samples,
            segment_type="line",
            sequence_index=2,
        ),
        CalibrationSegmentSpec(
            label="diagonal_line",
            prompt="Move along the table diagonal.",
            duration_seconds=config.sample_duration_seconds,
            min_samples=config.min_samples,
            segment_type="line",
            sequence_index=3,
        ),
    ]


def collect_calibration_segment(
    frame_iter: Any,
    segment_spec: CalibrationSegmentSpec,
    config: CalibrationLiveConfig,
    *,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Collect one calibration segment from raw dicts or LiveRawFrame objects."""

    if segment_spec.duration_seconds <= 0.0:
        raise ValueError("segment duration_seconds must be > 0.")
    cursor = _as_cursor(frame_iter)
    adapter_config = _adapter_config(config)
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)

    summary: dict[str, Any] = {
        "label": segment_spec.label,
        "segment_type": segment_spec.segment_type,
        "time_mode": "monotonic_live" if _uses_live_wall_clock(config) else "frame_time_simulated",
        "start_monotonic_time": None,
        "end_monotonic_time": None,
        "segment_start_monotonic": None,
        "segment_end_monotonic": None,
        "segment_start_frame_time": None,
        "segment_end_frame_time": None,
        "duration_seconds": 0.0,
        "duration_seconds_measured": 0.0,
        "received_frame_count": 0,
        "valid_sample_count": 0,
        "invalid_sample_count": 0,
        "tracker_valid_count": 0,
        "hand_valid_count": 0,
        "point_source": config.point_source,
        "warnings": [],
        "errors": [],
        "parse_error_count": 0,
        "adapter_error_count": 0,
        "frame_start": None,
        "frame_end": None,
        "points_world": [],
    }

    live_start = time.monotonic()
    live_end = live_start + float(segment_spec.duration_seconds)
    simulated_window_start: float | None = None
    simulated_window_end: float | None = None
    segment_started = False

    while True:
        if _uses_live_wall_clock(config):
            remaining = live_end - time.monotonic()
            if remaining <= 0.0:
                break
            frame = cursor.get_frame(timeout=min(0.1, max(0.0, remaining)))
            if frame is None:
                continue
        else:
            frame = cursor.get_frame(timeout=0.0)
            if frame is None:
                summary["warnings"].append(
                    "source ended before this calibration segment reached its requested duration."
                )
                break

        processed = _process_input_frame(frame, adapter_config, adapter, config)
        frame_time = processed["frame_time"]
        now_monotonic = time.monotonic()

        if not _uses_live_wall_clock(config):
            if cursor.simulated_base_time is None:
                cursor.simulated_base_time = frame_time
            if simulated_window_start is None or simulated_window_end is None:
                simulated_window_start = (
                    cursor.simulated_base_time
                    + float(segment_spec.sequence_index) * float(segment_spec.duration_seconds)
                )
                simulated_window_end = simulated_window_start + float(segment_spec.duration_seconds)
            if frame_time < simulated_window_start:
                continue
            if frame_time > simulated_window_end:
                cursor.push_back(frame)
                break
            if cursor.previous_simulated_frame_time is not None and frame_time < cursor.previous_simulated_frame_time:
                raise ValueError(
                    "raw JSONL simulated live mode requires monotonic frame_time; "
                    "use calibrate_from_raw_jsonl_table.py for explicit offline windows."
                )
            cursor.previous_simulated_frame_time = frame_time
            elapsed = frame_time - simulated_window_start
            legacy_start_time = simulated_window_start
            legacy_end_time = frame_time
            segment_start_monotonic = None
            segment_end_monotonic = None
            segment_start_frame_time = simulated_window_start
            segment_end_frame_time = frame_time
        else:
            elapsed = now_monotonic - live_start
            legacy_start_time = live_start
            legacy_end_time = now_monotonic
            segment_start_monotonic = live_start
            segment_end_monotonic = now_monotonic
            segment_start_frame_time = frame_time
            segment_end_frame_time = frame_time

        if not segment_started:
            segment_started = True
            summary["start_monotonic_time"] = legacy_start_time
            summary["segment_start_monotonic"] = segment_start_monotonic
            summary["segment_start_frame_time"] = segment_start_frame_time

        summary["received_frame_count"] += 1
        summary["end_monotonic_time"] = legacy_end_time
        summary["segment_end_monotonic"] = segment_end_monotonic
        summary["segment_end_frame_time"] = segment_end_frame_time
        summary["duration_seconds"] = max(0.0, float(elapsed))
        summary["duration_seconds_measured"] = max(0.0, float(elapsed))
        if summary["frame_start"] is None:
            summary["frame_start"] = processed["frame_index"]
        summary["frame_end"] = processed["frame_index"]

        if processed["parse_ok"]:
            if processed["tracker_valid"]:
                summary["tracker_valid_count"] += 1
            if processed["hand_valid"]:
                summary["hand_valid_count"] += 1
        else:
            summary["parse_error_count"] += 1
        if processed["parse_ok"] and not processed["adapter_ok"]:
            summary["adapter_error_count"] += 1

        point = processed["point_world"]
        if point is None:
            summary["invalid_sample_count"] += 1
        else:
            summary["points_world"].append(point)
            summary["valid_sample_count"] += 1

        if (
            progress_callback is not None
            and config.print_every > 0
            and summary["received_frame_count"] % config.print_every == 0
        ):
            progress_callback(dict(summary))
        if elapsed >= float(segment_spec.duration_seconds):
            break

    if not segment_started:
        summary["start_monotonic_time"] = live_start if _uses_live_wall_clock(config) else None
        summary["end_monotonic_time"] = summary["start_monotonic_time"]
        summary["segment_start_monotonic"] = summary["start_monotonic_time"]
        summary["segment_end_monotonic"] = summary["end_monotonic_time"]
        summary["duration_seconds"] = 0.0
        summary["duration_seconds_measured"] = 0.0
    if summary["valid_sample_count"] < segment_spec.min_samples:
        summary["errors"].append(
            f"{segment_spec.label}: only {summary['valid_sample_count']} valid calibration "
            f"points; need at least {segment_spec.min_samples}."
        )
    return summary


def run_live_table_calibration(
    frame_iter: Any,
    config: CalibrationLiveConfig,
    *,
    before_segment_callback: BeforeSegmentCallback | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CalibrationLiveResult:
    """Collect four table-line segments and build a FormalCalibration."""

    _validate_config(config)
    if hasattr(frame_iter, "start"):
        frame_iter.start()
    cursor = _FrameCursor(frame_iter)
    segment_summaries: list[dict[str, Any]] = []

    for segment in default_segment_specs(config):
        if before_segment_callback is not None:
            before_segment_callback(segment)
        queue_cleared = False
        if config.collection_mode == "live_stream":
            queue_cleared = _drain_live_queue(cursor)
        segment_summary = collect_calibration_segment(
            cursor,
            segment,
            config,
            progress_callback=progress_callback,
        )
        segment_summary["queue_cleared_before_segment"] = queue_cleared
        segment_summaries.append(segment_summary)

    errors = [
        error
        for summary in segment_summaries
        for error in summary.get("errors", [])
    ]
    warnings = [
        warning
        for summary in segment_summaries
        for warning in summary.get("warnings", [])
    ]
    live_metrics_summary = _live_metrics_summary(frame_iter, segment_summaries, config)
    if errors:
        return CalibrationLiveResult(
            calibration=None,
            segment_summaries=_public_segment_summaries(segment_summaries),
            warnings=warnings,
            errors=errors,
            live_metrics_summary=live_metrics_summary,
        )

    try:
        calibration = _build_formal_calibration(config, segment_summaries)
    except Exception as exc:
        return CalibrationLiveResult(
            calibration=None,
            segment_summaries=_public_segment_summaries(segment_summaries),
            warnings=warnings,
            errors=[*errors, str(exc)],
            live_metrics_summary=live_metrics_summary,
        )

    validation = validate_calibration(
        calibration,
        min_samples=config.min_samples,
        min_line_length=config.min_line_length,
    )
    warnings.extend(validation.warnings)
    errors.extend(validation.errors)
    calibration = FormalCalibration(
        calibration_id=calibration.calibration_id,
        created_at=calibration.created_at,
        point_source=calibration.point_source,
        origin_world=calibration.origin_world,
        x_axis_world=calibration.x_axis_world,
        y_axis_world=calibration.y_axis_world,
        z_axis_world=calibration.z_axis_world,
        up_axis_world=calibration.up_axis_world,
        origin_record=calibration.origin_record,
        long_line=calibration.long_line,
        width_line=calibration.width_line,
        diagonal_line=calibration.diagonal_line,
        plane_fit=calibration.plane_fit,
        quality={
            **calibration.quality,
            "calibration_quality_status": validation.status,
            "validation_errors": validation.errors,
            "validation_warnings": validation.warnings,
        },
        warnings=validation.warnings,
        metadata={
            **calibration.metadata,
            "validation_status": validation.status,
            "validation_thresholds": validation.thresholds,
        },
    )
    return CalibrationLiveResult(
        calibration=calibration,
        segment_summaries=_public_segment_summaries(segment_summaries),
        warnings=warnings,
        errors=errors,
        live_metrics_summary=live_metrics_summary,
    )


def save_live_calibration_result(result: CalibrationLiveResult, path: str | Path) -> None:
    """Save a successful live calibration result."""

    if result.calibration is None:
        raise ValueError("cannot save calibration because result.calibration is None.")
    if result.errors:
        raise ValueError("cannot save calibration because validation errors are present.")
    save_calibration(result.calibration, path)


def _build_formal_calibration(
    config: CalibrationLiveConfig,
    segment_summaries: list[dict[str, Any]],
) -> FormalCalibration:
    segments = {summary["label"]: summary for summary in segment_summaries}
    origin_summary = segments["origin"]
    long_summary = segments["long_axis_line"]
    width_summary = segments["width_axis_line"]
    diagonal_summary = segments["diagonal_line"]

    origin = build_calibration_point_record(
        "origin",
        origin_summary["points_world"],
        source=config.point_source,
        frame_start=origin_summary["frame_start"],
        frame_end=origin_summary["frame_end"],
        time_start=origin_summary["start_monotonic_time"],
        time_end=origin_summary["end_monotonic_time"],
        save_points=True,
        metadata=_segment_record_metadata(origin_summary),
    )
    long_line = build_calibration_line_record(
        "long_axis_line",
        long_summary["points_world"],
        source=config.point_source,
        frame_start=long_summary["frame_start"],
        frame_end=long_summary["frame_end"],
        time_start=long_summary["start_monotonic_time"],
        time_end=long_summary["end_monotonic_time"],
        metadata=_segment_record_metadata(long_summary),
    )
    width_line = build_calibration_line_record(
        "width_axis_line",
        width_summary["points_world"],
        source=config.point_source,
        frame_start=width_summary["frame_start"],
        frame_end=width_summary["frame_end"],
        time_start=width_summary["start_monotonic_time"],
        time_end=width_summary["end_monotonic_time"],
        metadata=_segment_record_metadata(width_summary),
    )
    diagonal_line = build_calibration_line_record(
        "diagonal_line",
        diagonal_summary["points_world"],
        source=config.point_source,
        frame_start=diagonal_summary["frame_start"],
        frame_end=diagonal_summary["frame_end"],
        time_start=diagonal_summary["start_monotonic_time"],
        time_end=diagonal_summary["end_monotonic_time"],
        metadata=_segment_record_metadata(diagonal_summary),
    )

    axes = build_axes_from_table_lines(
        origin.mean_world,
        long_line,
        width_line,
        diagonal_line,
        up_hint=config.up_hint,
    )
    plane_payload = axes["plane_fit"]
    plane_fit = PlaneFitRecord(
        centroid_world=plane_payload["centroid_world"],
        normal_world=plane_payload["normal_world"],
        rmse_m=float(plane_payload["rmse_m"]),
        plane_fit_rmse_m=float(plane_payload["rmse_m"]),
        max_abs_distance_m=float(plane_payload["max_abs_distance_m"]),
        sample_count=int(plane_payload["sample_count"]),
        source_labels=["long_axis_line", "width_axis_line", "diagonal_line"],
        singular_values=list(plane_payload.get("singular_values", [])),
    )
    metadata = _calibration_metadata(config, segment_summaries)
    quality = {
        **axes["quality"],
        "origin_sample_count": origin.sample_count,
        "long_line_sample_count": long_line.sample_count,
        "width_line_sample_count": width_line.sample_count,
        "diagonal_line_sample_count": diagonal_line.sample_count,
        "origin_max_deviation_m": origin.max_deviation_m,
        "origin_std_world": origin.std_world,
        "diagonal_plane_rmse_m": axes["quality"]["diagonal_line_fit_rmse_m"],
        "segment_summaries": _public_segment_summaries(segment_summaries),
    }
    return FormalCalibration(
        calibration_id=config.calibration_id or f"live_table_{uuid4().hex[:12]}",
        created_at=datetime.now(timezone.utc).isoformat(),
        point_source=config.point_source,
        origin_world=origin.mean_world,
        x_axis_world=axes["x_axis_world"],
        y_axis_world=axes["y_axis_world"],
        z_axis_world=axes["z_axis_world"],
        up_axis_world=axes["up_axis_world"],
        origin_record=origin,
        long_line=long_line,
        width_line=width_line,
        diagonal_line=diagonal_line,
        plane_fit=plane_fit,
        quality=quality,
        metadata=metadata,
    )


def _process_input_frame(
    frame: Any,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    config: CalibrationLiveConfig,
) -> dict[str, Any]:
    raw, frame_index, receive_time = _unwrap_frame(frame)
    parse_ok = False
    adapter_ok = False
    error_message = ""
    device_frame = None
    sample = None
    point = None
    frame_time = receive_time

    try:
        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        parse_ok = True
        if frame_time is None:
            frame_time = _strict_raw_frame_time(raw, config.timestamp_scale)
        sample = adapter.to_experiment_input_sample(device_frame)
        adapter_ok = True
        point = extract_calibration_point_from_sample(
            sample,
            device_frame,
            source=config.point_source,
        )
    except Exception as exc:
        error_message = str(exc)
        if not _uses_live_wall_clock(config) and "numeric raw timestamp" in error_message:
            raise
        if frame_time is None:
            frame_time = time.monotonic()

    tracker = getattr(device_frame, "tracker", None) if device_frame is not None else None
    hand = getattr(device_frame, "hand", None) if device_frame is not None else None
    return {
        "raw": raw,
        "frame_index": frame_index,
        "frame_time": float(frame_time),
        "parse_ok": parse_ok,
        "adapter_ok": adapter_ok,
        "tracker_valid": bool(getattr(tracker, "valid", False)),
        "hand_valid": bool(getattr(hand, "valid", False)),
        "point_world": point,
        "error_message": error_message,
    }


def _adapter_config(config: CalibrationLiveConfig) -> DeviceAdapterConfig:
    return DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )


def _unwrap_frame(frame: Any) -> tuple[dict[str, Any], int | None, float | None]:
    if isinstance(frame, LiveRawFrame):
        return frame.raw_frame, frame.frame_index, float(frame.receive_time_monotonic)
    if isinstance(frame, dict):
        frame_id = frame.get("frame")
        try:
            frame_index = int(frame_id) if frame_id is not None else None
        except (TypeError, ValueError):
            frame_index = None
        return frame, frame_index, None
    raise TypeError("frame_iter must yield raw dicts or LiveRawFrame objects.")


def _strict_raw_frame_time(raw: dict[str, Any], timestamp_scale: float) -> float:
    try:
        return float(raw["timestamp"]) * float(timestamp_scale)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "raw JSONL simulated live mode requires numeric raw timestamps; "
            "use calibrate_from_raw_jsonl_table.py with --sample-window-frames "
            "for recordings without reliable timestamps."
        ) from exc


def _uses_live_wall_clock(config: CalibrationLiveConfig) -> bool:
    return config.collection_mode == "live_stream"


def _validate_config(config: CalibrationLiveConfig) -> None:
    if config.collection_mode not in {"raw_jsonl_simulated_live", "live_stream"}:
        raise ValueError('collection_mode must be "raw_jsonl_simulated_live" or "live_stream".')
    if config.point_source not in {"tracker_position_world", "pinch_center_world"}:
        raise ValueError('point_source must be "tracker_position_world" or "pinch_center_world".')
    if config.sample_duration_seconds <= 0.0:
        raise ValueError("sample_duration_seconds must be > 0.")
    if config.min_samples <= 0:
        raise ValueError("min_samples must be > 0.")
    if config.min_line_length <= 0.0:
        raise ValueError("min_line_length must be > 0.")


class _FrameCursor:
    """Small cursor with one-frame pushback for time-window boundaries."""

    _is_frame_cursor = True

    def __init__(self, source: Any) -> None:
        self.source = source
        self._iterator = None if hasattr(source, "get_frame") else iter(source)
        self._pending: list[Any] = []
        self.simulated_base_time: float | None = None
        self.previous_simulated_frame_time: float | None = None

    def get_frame(self, timeout: float | None = None) -> Any | None:
        if self._pending:
            return self._pending.pop()
        if hasattr(self.source, "get_frame"):
            return self.source.get_frame(timeout=timeout)
        assert self._iterator is not None
        try:
            return next(self._iterator)
        except StopIteration:
            return None

    def push_back(self, frame: Any) -> None:
        self._pending.append(frame)

    def clear_pending(self) -> None:
        self._pending.clear()


def _as_cursor(frame_iter: Any) -> _FrameCursor:
    if getattr(frame_iter, "_is_frame_cursor", False):
        return frame_iter
    return _FrameCursor(frame_iter)


def _drain_live_queue(cursor: _FrameCursor) -> bool:
    source = cursor.source
    cursor.clear_pending()
    drained = False
    if not hasattr(source, "get_frame"):
        return False
    while True:
        frame = source.get_frame(timeout=0.0)
        if frame is None:
            break
        drained = True
    return drained or hasattr(source, "queue_size")


def _calibration_metadata(
    config: CalibrationLiveConfig,
    segment_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    warning = (
        RAW_JSONL_SIMULATED_LIVE_WARNING
        if config.collection_mode == "raw_jsonl_simulated_live"
        else LIVE_STREAM_WARNING
    )
    metadata: dict[str, Any] = {
        "calibration_type": "formal_table_lines",
        "is_formal_calibration": True,
        "collection_mode": config.collection_mode,
        "point_source": config.point_source,
        "sample_duration_seconds": config.sample_duration_seconds,
        "timestamp_scale": config.timestamp_scale,
        "warning": warning,
        "queue_cleared_before_segment": any(
            bool(summary.get("queue_cleared_before_segment")) for summary in segment_summaries
        ),
        "segment_summaries": _public_segment_summaries(segment_summaries),
    }
    if config.source_path is not None:
        metadata["source_path"] = config.source_path
    if config.notes is not None:
        metadata["notes"] = config.notes
    return metadata


def _segment_record_metadata(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "received_frame_count": summary["received_frame_count"],
        "valid_sample_count": summary["valid_sample_count"],
        "invalid_sample_count": summary["invalid_sample_count"],
        "tracker_valid_count": summary["tracker_valid_count"],
        "hand_valid_count": summary["hand_valid_count"],
        "warnings": list(summary.get("warnings", [])),
    }


def _public_segment_summaries(segment_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for summary in segment_summaries:
        item = {key: value for key, value in summary.items() if key != "points_world"}
        item["point_count"] = int(summary.get("valid_sample_count", 0))
        public.append(item)
    return public


def _live_metrics_summary(
    source: Any,
    segment_summaries: list[dict[str, Any]],
    config: CalibrationLiveConfig,
) -> dict[str, Any]:
    stats = _source_stats(source)
    received = sum(int(summary.get("received_frame_count", 0)) for summary in segment_summaries)
    valid = sum(int(summary.get("valid_sample_count", 0)) for summary in segment_summaries)
    invalid = sum(int(summary.get("invalid_sample_count", 0)) for summary in segment_summaries)
    parse_errors = sum(int(summary.get("parse_error_count", 0)) for summary in segment_summaries)
    return {
        "collection_mode": config.collection_mode,
        "received_frame_count": received,
        "valid_sample_count": valid,
        "invalid_sample_count": invalid,
        "parse_error_count": parse_errors + int(stats.get("parse_error_count", 0)),
        "bad_json_line_count": int(stats.get("bad_json_line_count", 0)),
        "dropped_frame_count": int(stats.get("dropped_frame_count", 0)),
        "queue_cleared_before_segment": any(
            bool(summary.get("queue_cleared_before_segment")) for summary in segment_summaries
        ),
        "source_stop_reason": stats.get("stop_reason"),
    }


def _source_stats(source: Any) -> dict[str, Any]:
    if hasattr(source, "stats_snapshot"):
        snapshot = source.stats_snapshot()
        if isinstance(snapshot, dict):
            return dict(snapshot)
        if hasattr(snapshot, "__dict__"):
            return dict(snapshot.__dict__)
    return {
        "parse_error_count": getattr(source, "parse_error_count", 0),
        "bad_json_line_count": getattr(source, "bad_json_line_count", 0),
        "dropped_frame_count": getattr(source, "dropped_frame_count", 0),
        "stop_reason": getattr(source, "stop_reason", None),
    }
