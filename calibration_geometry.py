"""Geometry helpers for formal table-line calibration.

This module is intentionally independent from controllers. It only estimates
world-space axes and fit-quality metrics from calibration samples.
"""

from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any

import numpy as np


def normalize_vector(vector: object) -> list[float]:
    """Return a finite unit vector as a JSON-friendly list."""

    return _normalize(_as_vec3(vector)).tolist()


def angle_degrees(a: object, b: object) -> float:
    """Return the unsigned angle between two vectors in degrees."""

    a_unit = _normalize(_as_vec3(a))
    b_unit = _normalize(_as_vec3(b))
    dot = float(np.dot(a_unit, b_unit))
    return float(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))


def fit_line_3d(points: object) -> dict[str, Any]:
    """Fit a 3D line to points with PCA and return serializable metrics."""

    array = _as_points(points, minimum=2)
    centroid = np.mean(array, axis=0)
    centered = array - centroid
    _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    direction = _normalize(vh[0])
    projections = centered @ direction
    endpoint_min = centroid + float(np.min(projections)) * direction
    endpoint_max = centroid + float(np.max(projections)) * direction
    residuals = centered - np.outer(projections, direction)
    rmse = float(np.sqrt(np.mean(np.sum(residuals * residuals, axis=1))))
    return {
        "centroid_world": centroid.tolist(),
        "direction_world": direction.tolist(),
        "rmse_m": rmse,
        "line_length_m": float(np.max(projections) - np.min(projections)),
        "endpoint_min_world": endpoint_min.tolist(),
        "endpoint_max_world": endpoint_max.tolist(),
        "sample_count": int(len(array)),
        "singular_values": singular_values.tolist(),
    }


def fit_plane_3d(points: object) -> dict[str, Any]:
    """Fit a 3D plane to points and return normal plus residual metrics."""

    array = _as_points(points, minimum=3)
    centroid = np.mean(array, axis=0)
    centered = array - centroid
    _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    normal = _normalize(vh[-1])
    distances = centered @ normal
    return {
        "centroid_world": centroid.tolist(),
        "normal_world": normal.tolist(),
        "rmse_m": float(np.sqrt(np.mean(distances * distances))),
        "max_abs_distance_m": float(np.max(np.abs(distances))),
        "sample_count": int(len(array)),
        "singular_values": singular_values.tolist(),
    }


def project_vector_to_plane(vector: object, plane_normal: object) -> list[float]:
    """Project a vector onto the plane perpendicular to plane_normal."""

    source = _as_vec3(vector)
    normal = _normalize(_as_vec3(plane_normal))
    projected = source - float(np.dot(source, normal)) * normal
    return _normalize(projected).tolist()


def choose_up_direction(plane_normal: object, up_hint: object) -> list[float]:
    """Normalize plane_normal and flip it toward up_hint when needed."""

    normal = _normalize(_as_vec3(plane_normal))
    hint = _normalize(_as_vec3(up_hint))
    if float(np.dot(normal, hint)) < 0.0:
        normal = -normal
    return normal.tolist()


def build_axes_from_table_lines(
    origin_world: object,
    long_line: object,
    width_line: object,
    diagonal_line: object,
    *,
    up_hint: object = (0.0, 0.0, 1.0),
) -> dict[str, Any]:
    """Build formal task axes from origin, long/width/diagonal line samples.

    x follows the long-side fitted line projected onto the table plane.
    y is always cross(up, x). The width line is only a quality check.
    """

    origin = _as_vec3(origin_world)
    long_fit = _line_fit_from_record(long_line)
    width_fit = _line_fit_from_record(width_line)
    diagonal_fit = _line_fit_from_record(diagonal_line)
    all_points = _collect_line_points(long_line, width_line, diagonal_line)
    plane_fit = fit_plane_3d(all_points)

    up_axis = np.asarray(choose_up_direction(plane_fit["normal_world"], up_hint), dtype=float)
    long_raw_fitted_direction = np.asarray(normalize_vector(long_fit["direction_world"]), dtype=float)
    long_fitted_direction = long_raw_fitted_direction.copy()
    long_motion_direction = _motion_direction_from_record(long_line)
    long_direction_flipped = False
    if long_motion_direction is not None and float(np.dot(long_fitted_direction, long_motion_direction)) < 0.0:
        long_fitted_direction = -long_fitted_direction
        long_direction_flipped = True
    long_fit = {
        **long_fit,
        "direction_world": long_fitted_direction.tolist(),
    }
    long_direction = np.asarray(project_vector_to_plane(long_fit["direction_world"], up_axis))
    width_direction = np.asarray(project_vector_to_plane(width_fit["direction_world"], up_axis))
    diagonal_direction = np.asarray(project_vector_to_plane(diagonal_fit["direction_world"], up_axis))
    y_axis = _normalize(np.cross(up_axis, long_direction))
    x_axis = _normalize(np.cross(y_axis, up_axis))
    width_dot_y = float(np.dot(width_direction, y_axis))

    quality = {
        "plane_fit_rmse_m": plane_fit["rmse_m"],
        "plane_fit_max_abs_distance_m": plane_fit["max_abs_distance_m"],
        "long_line_fit_rmse_m": long_fit["rmse_m"],
        "width_line_fit_rmse_m": width_fit["rmse_m"],
        "diagonal_line_fit_rmse_m": diagonal_fit["rmse_m"],
        "long_line_length_m": long_fit["line_length_m"],
        "width_line_length_m": width_fit["line_length_m"],
        "diagonal_line_length_m": diagonal_fit["line_length_m"],
        "long_line_motion_direction_world": (
            long_motion_direction.tolist() if long_motion_direction is not None else None
        ),
        "long_line_fitted_direction_world": long_raw_fitted_direction.tolist(),
        "long_line_direction_flipped_to_match_motion": long_direction_flipped,
        "width_line_dot_y_axis": width_dot_y,
        "width_line_angle_to_y_axis_degrees": angle_degrees(width_direction, y_axis),
        "width_line_direction_matches_y_positive": width_dot_y >= 0.0,
        "long_width_angle_degrees": _acute_angle(long_direction, width_direction),
        "width_y_angle_degrees": _acute_angle(width_direction, y_axis),
        "x_y_angle_degrees": angle_degrees(x_axis, y_axis),
        "x_up_angle_degrees": angle_degrees(x_axis, up_axis),
        "y_up_angle_degrees": angle_degrees(y_axis, up_axis),
        "diagonal_x_angle_degrees": _acute_angle(diagonal_direction, x_axis),
        "diagonal_y_angle_degrees": _acute_angle(diagonal_direction, y_axis),
    }
    return {
        "origin_world": origin.tolist(),
        "x_axis_world": x_axis.tolist(),
        "y_axis_world": y_axis.tolist(),
        "z_axis_world": up_axis.tolist(),
        "up_axis_world": up_axis.tolist(),
        "plane_fit": plane_fit,
        "long_line_fit": long_fit,
        "width_line_fit": width_fit,
        "diagonal_line_fit": diagonal_fit,
        "quality": quality,
    }


def _line_fit_from_record(record: object) -> dict[str, Any]:
    payload = _record_payload(record)
    if isinstance(payload.get("fit"), dict):
        return dict(payload["fit"])
    if payload.get("direction_world") is not None:
        return {
            "centroid_world": payload.get("centroid_world", payload.get("mean_world", [0.0, 0.0, 0.0])),
            "direction_world": normalize_vector(payload["direction_world"]),
            "rmse_m": float(payload.get("rmse_m", payload.get("line_fit_rmse_m", 0.0))),
            "line_length_m": float(payload.get("line_length_m", payload.get("length_m", 0.0))),
            "sample_count": int(payload.get("sample_count", 0)),
        }
    points = payload.get("points_world")
    if points is None:
        points = payload.get("samples_world")
    if points is None:
        raise ValueError("line record must contain fit, direction_world, or points_world.")
    return fit_line_3d(points)


def _motion_direction_from_record(record: object) -> np.ndarray | None:
    payload = _record_payload(record)
    points = payload.get("points_world", payload.get("samples_world"))
    if points is None or len(points) < 2:
        return None
    array = _as_points(points, minimum=2)
    motion = array[-1] - array[0]
    return _normalize(motion)


def _collect_line_points(*records: object) -> np.ndarray:
    arrays: list[np.ndarray] = []
    fallback_points: list[np.ndarray] = []
    for record in records:
        payload = _record_payload(record)
        points = payload.get("points_world", payload.get("samples_world"))
        if points is not None and len(points) >= 2:
            arrays.append(_as_points(points, minimum=2))
            continue
        fit = _line_fit_from_record(record)
        center = _as_vec3(fit.get("centroid_world", [0.0, 0.0, 0.0]))
        direction = _normalize(_as_vec3(fit["direction_world"]))
        half_length = float(fit.get("line_length_m", 0.0)) * 0.5
        fallback_points.extend([center - half_length * direction, center + half_length * direction])
    if fallback_points:
        arrays.append(np.vstack(fallback_points))
    if arrays:
        return np.vstack(arrays)
    raise ValueError("No line points or fitted line endpoints are available.")


def _record_payload(record: object) -> dict[str, Any]:
    if is_dataclass(record):
        return asdict(record)
    if isinstance(record, dict):
        return dict(record)
    if hasattr(record, "__dict__"):
        return dict(vars(record))
    raise TypeError("record must be a dataclass, dict, or object with __dict__.")


def _as_vec3(value: object) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,):
        raise ValueError("Expected a 3D vector.")
    if not np.all(np.isfinite(vector)):
        raise ValueError("Vector must contain finite values.")
    return vector


def _as_points(points: object, *, minimum: int) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("Expected points with shape (N, 3).")
    if len(array) < minimum:
        raise ValueError(f"Expected at least {minimum} points.")
    if not np.all(np.isfinite(array)):
        raise ValueError("Points must contain finite values.")
    return array


def _normalize(value: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(value))
    if norm <= 0.0:
        raise ValueError("Cannot normalize a zero-length vector.")
    return value / norm


def _acute_angle(a: object, b: object) -> float:
    angle = angle_degrees(a, b)
    return float(min(angle, 180.0 - angle))
