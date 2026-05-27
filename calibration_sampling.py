"""Sampling helpers for formal calibration segments."""

from __future__ import annotations

from typing import Any

import numpy as np

from calibration_geometry import fit_line_3d
from calibration_io import CalibrationLineRecord, CalibrationPointRecord


def extract_calibration_point_from_sample(
    sample: Any,
    device_frame: Any | None = None,
    *,
    source: str = "tracker_position_world",
) -> list[float] | None:
    """Extract one world-space calibration point from a parsed frame.

    This layer only selects raw geometry. It does not decide contact, block,
    trial, haptic, or parser semantics.
    """

    if source == "tracker_position_world":
        tracker = getattr(device_frame, "tracker", None)
        pose = getattr(tracker, "pose_world", None)
        if tracker is None or not getattr(tracker, "valid", False) or pose is None:
            return None
        return _finite_vec3_or_none(getattr(pose, "position", None))
    if source == "pinch_center_world":
        return _finite_vec3_or_none(getattr(sample, "pinch_center_world", None))
    raise ValueError(
        'source must be "tracker_position_world" or "pinch_center_world".'
    )


def summarize_static_points(points: object) -> dict[str, Any]:
    """Return mean/std/max-deviation metrics for a static point segment."""

    array = _as_points(points, minimum=1)
    mean = np.mean(array, axis=0)
    deltas = np.linalg.norm(array - mean, axis=1)
    return {
        "mean_world": mean.tolist(),
        "std_world": np.std(array, axis=0).tolist(),
        "max_deviation_m": float(np.max(deltas)),
        "sample_count": int(len(array)),
    }


def build_calibration_point_record(
    label: str,
    points: object,
    *,
    source: str,
    frame_start: int | None = None,
    frame_end: int | None = None,
    time_start: float | None = None,
    time_end: float | None = None,
    save_points: bool = False,
    metadata: dict[str, Any] | None = None,
) -> CalibrationPointRecord:
    """Build a CalibrationPointRecord from a static sample segment."""

    array = _as_points(points, minimum=1)
    summary = summarize_static_points(array)
    return CalibrationPointRecord(
        label=label,
        source=source,
        mean_world=summary["mean_world"],
        point_world=summary["mean_world"],
        std_world=summary["std_world"],
        max_deviation_m=summary["max_deviation_m"],
        sample_count=summary["sample_count"],
        duration_seconds=_duration(time_start, time_end),
        frame_start=frame_start,
        frame_end=frame_end,
        sample_time_start=time_start,
        sample_time_end=time_end,
        time_start=time_start,
        time_end=time_end,
        points_world=array.tolist() if save_points else None,
        metadata=metadata or {},
    )


def build_calibration_line_record(
    label: str,
    points: object,
    *,
    source: str,
    frame_start: int | None = None,
    frame_end: int | None = None,
    time_start: float | None = None,
    time_end: float | None = None,
    save_points: bool = True,
    metadata: dict[str, Any] | None = None,
) -> CalibrationLineRecord:
    """Build a CalibrationLineRecord from a line-sweep segment."""

    array = _as_points(points, minimum=2)
    fit = fit_line_3d(array)
    direction = np.asarray(fit["direction_world"], dtype=float)
    movement_hint = array[-1] - array[0]
    if float(np.dot(direction, movement_hint)) < 0.0:
        direction = -direction
    return CalibrationLineRecord(
        label=label,
        source=source,
        points_world=array.tolist() if save_points else [],
        centroid_world=fit["centroid_world"],
        direction_world=direction.tolist(),
        line_length_m=float(fit["line_length_m"]),
        rmse_m=float(fit["rmse_m"]),
        sample_count=int(fit["sample_count"]),
        line_fit_rmse_m=float(fit["rmse_m"]),
        endpoint_min_world=fit["endpoint_min_world"],
        endpoint_max_world=fit["endpoint_max_world"],
        duration_seconds=_duration(time_start, time_end),
        frame_start=frame_start,
        frame_end=frame_end,
        sample_time_start=time_start,
        sample_time_end=time_end,
        time_start=time_start,
        time_end=time_end,
        metadata=metadata or {},
    )


def _finite_vec3_or_none(value: Any) -> list[float] | None:
    if value is None:
        return None
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        return None
    return vector.tolist()


def _as_points(points: object, *, minimum: int) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("Expected points with shape (N, 3).")
    if len(array) < minimum:
        raise ValueError(f"Expected at least {minimum} points.")
    if not np.all(np.isfinite(array)):
        raise ValueError("Points must contain finite values.")
    return array


def _duration(time_start: float | None, time_end: float | None) -> float | None:
    if time_start is None or time_end is None:
        return None
    return float(time_end) - float(time_start)
