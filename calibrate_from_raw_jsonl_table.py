"""Build a formal table-line calibration JSON from raw JSONL segments.

This is an offline format/quality test tool. It simulates the formal workflow
from existing recordings; it is not the future live calibration UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from calibration_geometry import build_axes_from_table_lines
from calibration_io import (
    FormalCalibration,
    PlaneFitRecord,
    calibration_to_dict,
    save_calibration,
    validate_calibration,
)
from calibration_sampling import (
    build_calibration_line_record,
    build_calibration_point_record,
    extract_calibration_point_from_sample,
)
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_frame_source import JsonlRawFrameSource
from raw_manus_vive_parser import parse_raw_manus_vive_frame


OFFLINE_TABLE_CALIBRATION_WARNING = (
    "This calibration was created from replayed raw JSONL for testing the calibration "
    "format; live formal calibration should collect points interactively."
)


@dataclass(frozen=True)
class RawCalibrationFrame:
    """One raw frame with parsed device/sample data and a selected calibration point."""

    raw_index: int
    raw: dict[str, Any]
    device_frame: Any
    sample: Any
    point_world: list[float] | None


@dataclass(frozen=True)
class SegmentSelection:
    """Selected frame/point window for one calibration action."""

    label: str
    start_frame: int
    frames: list[RawCalibrationFrame]
    points_world: list[list[float]]

    @property
    def frame_start(self) -> int | None:
        return self.frames[0].raw_index if self.frames else None

    @property
    def frame_end(self) -> int | None:
        return self.frames[-1].raw_index if self.frames else None

    @property
    def time_start(self) -> float | None:
        return self.frames[0].sample.time if self.frames else None

    @property
    def time_end(self) -> float | None:
        return self.frames[-1].sample.time if self.frames else None


def load_calibration_frames(config: argparse.Namespace) -> list[RawCalibrationFrame]:
    """Load, parse, adapt, and select calibration points from a raw JSONL file."""

    adapter_config = DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    source = JsonlRawFrameSource(config.raw_jsonl)
    records: list[RawCalibrationFrame] = []
    try:
        for raw_index, raw in enumerate(source):
            device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
            sample = adapter.to_experiment_input_sample(device_frame)
            point = extract_calibration_point_from_sample(
                sample,
                device_frame,
                source=config.point_source,
            )
            records.append(
                RawCalibrationFrame(
                    raw_index=raw_index,
                    raw=raw,
                    device_frame=device_frame,
                    sample=sample,
                    point_world=point,
                )
            )
    finally:
        source.close()
    return records


def build_formal_calibration(config: argparse.Namespace) -> FormalCalibration:
    """Build a FormalCalibration object from configured raw-frame windows."""

    records = load_calibration_frames(config)
    origin_segment = select_segment(records, "origin", config.origin_start_frame, config)
    long_segment = select_segment(
        records,
        "long_axis_line",
        config.long_line_start_frame,
        config,
    )
    width_segment = select_segment(
        records,
        "width_axis_line",
        config.width_line_start_frame,
        config,
    )
    diagonal_segment = select_segment(
        records,
        "diagonal_line",
        config.diagonal_line_start_frame,
        config,
    )
    _require_points(origin_segment, config.min_samples)
    _require_points(long_segment, config.min_samples)
    _require_points(width_segment, config.min_samples)
    _require_points(diagonal_segment, config.min_samples)

    origin = build_calibration_point_record(
        "origin",
        origin_segment.points_world,
        source=config.point_source,
        frame_start=origin_segment.frame_start,
        frame_end=origin_segment.frame_end,
        time_start=origin_segment.time_start,
        time_end=origin_segment.time_end,
        save_points=True,
    )
    long_line = build_calibration_line_record(
        "long_axis_line",
        long_segment.points_world,
        source=config.point_source,
        frame_start=long_segment.frame_start,
        frame_end=long_segment.frame_end,
        time_start=long_segment.time_start,
        time_end=long_segment.time_end,
    )
    width_line = build_calibration_line_record(
        "width_axis_line",
        width_segment.points_world,
        source=config.point_source,
        frame_start=width_segment.frame_start,
        frame_end=width_segment.frame_end,
        time_start=width_segment.time_start,
        time_end=width_segment.time_end,
    )
    diagonal_line = build_calibration_line_record(
        "diagonal_line",
        diagonal_segment.points_world,
        source=config.point_source,
        frame_start=diagonal_segment.frame_start,
        frame_end=diagonal_segment.frame_end,
        time_start=diagonal_segment.time_start,
        time_end=diagonal_segment.time_end,
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
    thresholds = _quality_thresholds(config)
    quality = {
        **axes["quality"],
        "origin_sample_count": origin.sample_count,
        "long_line_sample_count": long_line.sample_count,
        "width_line_sample_count": width_line.sample_count,
        "diagonal_line_sample_count": diagonal_line.sample_count,
        "origin_max_deviation_m": origin.max_deviation_m,
        "origin_std_world": origin.std_world,
        "diagonal_plane_rmse_m": axes["quality"]["diagonal_line_fit_rmse_m"],
        "thresholds": thresholds,
    }
    metadata = {
        "source": "raw_jsonl_simulated_table_line_calibration",
        "sample_duration_seconds": config.sample_duration_seconds,
        "sample_window_frames": config.sample_window_frames,
        "point_source": config.point_source,
        "raw_jsonl": str(config.raw_jsonl),
        "timestamp_scale": config.timestamp_scale,
        "warning": OFFLINE_TABLE_CALIBRATION_WARNING,
    }
    if config.notes is not None:
        metadata["notes"] = config.notes

    calibration = FormalCalibration(
        calibration_id=config.calibration_id or f"table_line_{uuid4().hex[:12]}",
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
    validation = validate_calibration(
        calibration,
        min_samples=config.min_samples,
        min_line_length=config.min_line_length,
    )
    return FormalCalibration(
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
        metadata={**calibration.metadata, "thresholds": thresholds},
    )


def select_segment(
    records: list[RawCalibrationFrame],
    label: str,
    start_frame: int,
    config: argparse.Namespace,
) -> SegmentSelection:
    """Select a calibration segment by frame count or by sample-time duration."""

    candidates = [record for record in records if record.raw_index >= start_frame]
    if not candidates:
        raise ValueError(f"{label}: no frames found at or after start frame {start_frame}.")

    if config.sample_window_frames is not None:
        selected = candidates[: config.sample_window_frames]
    else:
        if not _has_reliable_time(candidates[0]):
            raise ValueError(
                f"{label}: raw timestamp is unavailable or non-numeric; "
                "specify --sample-window-frames."
            )
        start_time = candidates[0].sample.time
        if start_time is None:
            raise ValueError(
                f"{label}: frame time is unavailable; specify --sample-window-frames."
            )
        end_time = float(start_time) + float(config.sample_duration_seconds)
        selected = [record for record in candidates if float(record.sample.time) <= end_time]
        if not selected:
            raise ValueError(
                f"{label}: no time-window frames selected; specify --sample-window-frames."
            )

    points = [record.point_world for record in selected if record.point_world is not None]
    return SegmentSelection(
        label=label,
        start_frame=start_frame,
        frames=selected,
        points_world=[list(point) for point in points],
    )


def _has_reliable_time(record: RawCalibrationFrame) -> bool:
    try:
        float(record.raw.get("timestamp"))
    except (TypeError, ValueError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    config = _parse_args(argv)
    try:
        calibration = build_formal_calibration(config)
        validation = validate_calibration(
            calibration,
            min_samples=config.min_samples,
            min_line_length=config.min_line_length,
        )
        if validation.errors:
            _print_validation_failure(validation.errors, validation.warnings)
            return 1
        Path(config.out).parent.mkdir(parents=True, exist_ok=True)
        save_calibration(calibration, config.out)
        print(json.dumps(_summary(calibration), indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1


def _require_points(segment: SegmentSelection, min_samples: int) -> None:
    if len(segment.points_world) < min_samples:
        raise ValueError(
            f"{segment.label}: only {len(segment.points_world)} valid calibration points; "
            f"need at least {min_samples}."
        )


def _summary(calibration: FormalCalibration) -> dict[str, Any]:
    payload = calibration_to_dict(calibration)
    return {
        "calibration_id": calibration.calibration_id,
        "calibration_type": calibration.calibration_type,
        "is_formal_calibration": calibration.is_formal_calibration,
        "origin_world": calibration.origin_world,
        "x_axis_world": calibration.x_axis_world,
        "y_axis_world": calibration.y_axis_world,
        "up_axis_world": calibration.up_axis_world,
        "quality": payload["quality"],
        "warnings": calibration.warnings,
    }


def _quality_thresholds(config: argparse.Namespace) -> dict[str, float]:
    return {
        "min_samples": float(config.min_samples),
        "min_line_length_m": float(config.min_line_length),
        "origin_max_deviation_warning_m": 0.02,
        "line_fit_rmse_warning_m": 0.02,
        "plane_fit_rmse_warning_m": 0.02,
        "x_y_angle_warning_degrees": 10.0,
        "x_y_angle_error_degrees": 25.0,
        "diagonal_axis_angle_warning_min_degrees": 10.0,
        "diagonal_axis_angle_warning_max_degrees": 80.0,
    }


def _print_validation_failure(errors: list[str], warnings: list[str]) -> None:
    print(
        json.dumps(
            {"status": "error", "errors": errors, "warnings": warnings},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def _parse_vec3(value: str) -> tuple[float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated floats")
    vector = np.asarray(parts, dtype=float)
    if not np.all(np.isfinite(vector)):
        raise argparse.ArgumentTypeError("vector components must be finite")
    return (float(vector[0]), float(vector[1]), float(vector[2]))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create formal table-line calibration JSON from raw JSONL windows."
    )
    parser.add_argument("--raw-jsonl", required=True, type=Path)
    parser.add_argument("--out", default="data/calibration/table_line_calibration.json", type=Path)
    parser.add_argument("--calibration-id", default=None)
    parser.add_argument("--origin-start-frame", required=True, type=int)
    parser.add_argument("--long-line-start-frame", required=True, type=int)
    parser.add_argument("--width-line-start-frame", required=True, type=int)
    parser.add_argument("--diagonal-line-start-frame", required=True, type=int)
    parser.add_argument("--sample-duration-seconds", default=5.0, type=float)
    parser.add_argument("--sample-window-frames", default=None, type=int)
    parser.add_argument(
        "--point-source",
        choices=("tracker_position_world", "pinch_center_world"),
        default="tracker_position_world",
    )
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--up-hint", default=(0.0, 0.0, 1.0), type=_parse_vec3)
    parser.add_argument("--min-samples", default=10, type=int)
    parser.add_argument("--min-line-length", default=0.10, type=float)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args(argv)
    if args.sample_window_frames is not None and args.sample_window_frames <= 0:
        parser.error("--sample-window-frames must be > 0.")
    if args.sample_duration_seconds <= 0:
        parser.error("--sample-duration-seconds must be > 0.")
    if args.min_samples <= 0:
        parser.error("--min-samples must be > 0.")
    if args.min_line_length <= 0:
        parser.error("--min-line-length must be > 0.")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
