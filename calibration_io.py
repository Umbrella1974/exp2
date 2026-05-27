"""Save and load task coordinate calibration files.

The original Stage 3 task-calibration helpers are kept intact. Stage 5 extends
this module with formal table-line calibration records and validation.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np

from task_coordinate_system import TaskCoordinateSystem


FORMAL_CALIBRATION_VERSION = 1
FORMAL_CALIBRATION_TYPE = "formal_table_lines"


@dataclass(frozen=True)
class CalibrationPointRecord:
    """Static calibration point sampled in world space."""

    label: str
    source: str
    mean_world: list[float]
    sample_count: int
    point_world: list[float] = field(default_factory=list)
    std_world: list[float] = field(default_factory=list)
    max_deviation_m: float = 0.0
    duration_seconds: float | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    sample_time_start: float | None = None
    sample_time_end: float | None = None
    time_start: float | None = None
    time_end: float | None = None
    points_world: list[list[float]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationLineRecord:
    """Line calibration segment sampled in world space."""

    label: str
    source: str
    points_world: list[list[float]]
    centroid_world: list[float]
    direction_world: list[float]
    line_length_m: float
    rmse_m: float
    sample_count: int
    line_fit_rmse_m: float | None = None
    endpoint_min_world: list[float] | None = None
    endpoint_max_world: list[float] | None = None
    duration_seconds: float | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    sample_time_start: float | None = None
    sample_time_end: float | None = None
    time_start: float | None = None
    time_end: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlaneFitRecord:
    """Table plane fit record."""

    centroid_world: list[float]
    normal_world: list[float]
    rmse_m: float
    max_abs_distance_m: float
    sample_count: int
    plane_fit_rmse_m: float | None = None
    source_labels: list[str] = field(default_factory=list)
    singular_values: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FormalCalibration:
    """Formal table-line calibration payload for offline formal replay."""

    calibration_id: str
    created_at: str
    origin_world: list[float]
    x_axis_world: list[float]
    y_axis_world: list[float]
    z_axis_world: list[float]
    up_axis_world: list[float]
    origin_record: CalibrationPointRecord
    long_line: CalibrationLineRecord
    width_line: CalibrationLineRecord
    diagonal_line: CalibrationLineRecord
    plane_fit: PlaneFitRecord
    calibration_type: str = FORMAL_CALIBRATION_TYPE
    coordinate_space: str = "world_to_task"
    unit: str = "m"
    is_formal_calibration: bool = True
    point_source: str = "tracker_position_world"
    quality: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationValidationResult:
    """Validation result with errors, warnings, and an aggregate status."""

    status: str
    errors: list[str]
    warnings: list[str]
    thresholds: dict[str, float]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def save_task_calibration(
    path: str | Path,
    task_coordinate_system: TaskCoordinateSystem,
    metadata: dict[str, object] | None = None,
) -> None:
    """Save a TaskCoordinateSystem and metadata to JSON."""

    payload = {
        "origin_world": task_coordinate_system.origin_world.tolist(),
        "x_axis_world": task_coordinate_system.x_axis_world.tolist(),
        "y_axis_world": task_coordinate_system.y_axis_world.tolist(),
        "z_axis_world": task_coordinate_system.z_axis_world.tolist(),
        "metadata": metadata or {},
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_task_calibration(path: str | Path) -> TaskCoordinateSystem:
    """Load a TaskCoordinateSystem from JSON."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TaskCoordinateSystem(
        origin_world=payload["origin_world"],
        x_axis_world=payload["x_axis_world"],
        y_axis_world=payload["y_axis_world"],
        z_axis_world=payload["z_axis_world"],
    )


def load_task_calibration_metadata(path: str | Path) -> dict[str, object]:
    """Load calibration metadata from JSON."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    return metadata


def save_calibration(
    calibration_or_path: FormalCalibration | str | Path,
    path_or_calibration: str | Path | FormalCalibration,
) -> None:
    """Save a formal calibration JSON file.

    The preferred Stage 5 order is save_calibration(calibration, path). The
    path-first order is also accepted to keep call sites ergonomic beside the
    older save_task_calibration(path, system) helper.
    """

    if isinstance(calibration_or_path, FormalCalibration):
        calibration = calibration_or_path
        path = path_or_calibration
    else:
        path = calibration_or_path
        calibration = path_or_calibration
    if not isinstance(calibration, FormalCalibration):
        raise TypeError("save_calibration expected a FormalCalibration.")
    Path(path).write_text(
        json.dumps(calibration_to_dict(calibration), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def load_calibration(path: str | Path) -> FormalCalibration:
    """Load a formal calibration JSON file."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("calibration file must be a JSON object.")
    return calibration_from_dict(payload)


def calibration_to_dict(calibration: FormalCalibration) -> dict[str, Any]:
    """Convert a formal calibration dataclass to a JSON-safe dict."""

    payload = _json_safe(asdict(calibration))
    payload["version"] = FORMAL_CALIBRATION_VERSION
    payload["origin"] = payload["origin_record"]
    payload["long_axis_line"] = payload["long_line"]
    payload["width_axis_line"] = payload["width_line"]
    payload["task_coordinate_system"] = {
        "origin_world": payload["origin_world"],
        "x_axis_world": payload["x_axis_world"],
        "y_axis_world": payload["y_axis_world"],
        "z_axis_world": payload["z_axis_world"],
    }
    return payload


def calibration_from_dict(payload: dict[str, Any]) -> FormalCalibration:
    """Convert a dict payload into a FormalCalibration dataclass."""

    if payload.get("calibration_type") != FORMAL_CALIBRATION_TYPE:
        raise ValueError(f'calibration_type must be "{FORMAL_CALIBRATION_TYPE}".')
    return FormalCalibration(
        calibration_id=str(payload.get("calibration_id", "")),
        created_at=str(payload.get("created_at", "")),
        calibration_type=str(payload.get("calibration_type", "")),
        coordinate_space=str(payload.get("coordinate_space", "")),
        unit=str(payload.get("unit", "")),
        is_formal_calibration=bool(payload.get("is_formal_calibration", False)),
        point_source=str(payload.get("point_source", "")),
        origin_world=_float_list(payload.get("origin_world", [])),
        x_axis_world=_float_list(payload.get("x_axis_world", [])),
        y_axis_world=_float_list(payload.get("y_axis_world", [])),
        z_axis_world=_float_list(payload.get("z_axis_world", [])),
        up_axis_world=_float_list(payload.get("up_axis_world", payload.get("z_axis_world", []))),
        origin_record=_point_record_from_dict(
            _dict_value(payload, "origin_record") or _dict_value(payload, "origin")
        ),
        long_line=_line_record_from_dict(
            _dict_value(payload, "long_line") or _dict_value(payload, "long_axis_line")
        ),
        width_line=_line_record_from_dict(
            _dict_value(payload, "width_line") or _dict_value(payload, "width_axis_line")
        ),
        diagonal_line=_line_record_from_dict(_dict_value(payload, "diagonal_line")),
        plane_fit=_plane_fit_from_dict(_dict_value(payload, "plane_fit")),
        quality=_dict_value(payload, "quality"),
        warnings=list(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else [],
        metadata=_dict_value(payload, "metadata"),
    )


def validate_calibration(
    calibration: FormalCalibration,
    *,
    min_samples: int = 10,
    min_line_length: float = 0.10,
) -> CalibrationValidationResult:
    """Validate a formal calibration without modifying it."""

    thresholds = {
        "min_samples": float(min_samples),
        "min_line_length_m": float(min_line_length),
        "min_duration_seconds_error": 1.0,
        "min_duration_seconds_warning": 3.0,
        "origin_max_deviation_warning_m": 0.02,
        "line_fit_rmse_warning_m": 0.02,
        "plane_fit_rmse_warning_m": 0.02,
        "long_width_angle_warning_degrees": 10.0,
        "long_width_angle_error_degrees": 25.0,
        "diagonal_axis_angle_warning_min_degrees": 10.0,
        "diagonal_axis_angle_warning_max_degrees": 80.0,
    }
    errors: list[str] = []
    warnings: list[str] = []

    if calibration.calibration_type != FORMAL_CALIBRATION_TYPE:
        errors.append(f'calibration_type must be "{FORMAL_CALIBRATION_TYPE}".')
    if calibration.coordinate_space != "world_to_task":
        errors.append('coordinate_space must be "world_to_task".')
    if calibration.unit != "m":
        errors.append('unit must be "m".')
    if calibration.is_formal_calibration is not True:
        errors.append("is_formal_calibration must be true.")
    if not calibration.calibration_id:
        warnings.append("calibration_id is empty.")

    origin = _finite_vec3(calibration.origin_world, "origin_world", errors)
    x_axis = _finite_vec3(calibration.x_axis_world, "x_axis_world", errors)
    y_axis = _finite_vec3(calibration.y_axis_world, "y_axis_world", errors)
    z_axis = _finite_vec3(calibration.z_axis_world, "z_axis_world", errors)
    up_axis = _finite_vec3(calibration.up_axis_world, "up_axis_world", errors)
    if x_axis is not None:
        _validate_unit_vector(x_axis, "x_axis_world", errors, warnings)
    if y_axis is not None:
        _validate_unit_vector(y_axis, "y_axis_world", errors, warnings)
    if z_axis is not None:
        _validate_unit_vector(z_axis, "z_axis_world", errors, warnings)
    if up_axis is not None:
        _validate_unit_vector(up_axis, "up_axis_world", errors, warnings)
    if x_axis is not None and y_axis is not None:
        _validate_axis_angle(x_axis, y_axis, "x/y axes", errors, warnings)
    if x_axis is not None and z_axis is not None:
        _validate_axis_angle(x_axis, z_axis, "x/z axes", errors, warnings)
    if y_axis is not None and z_axis is not None:
        _validate_axis_angle(y_axis, z_axis, "y/z axes", errors, warnings)
    if z_axis is not None and up_axis is not None and abs(float(np.dot(z_axis, up_axis))) < 0.999:
        warnings.append("z_axis_world and up_axis_world differ.")
    if origin is not None and not np.all(np.isfinite(origin)):
        errors.append("origin_world must contain finite values.")

    _validate_point_record(calibration.origin_record, "origin_record", min_samples, errors, warnings)
    _validate_line_record(
        calibration.long_line,
        "long_line",
        min_samples,
        min_line_length,
        errors,
        warnings,
    )
    _validate_line_record(
        calibration.width_line,
        "width_line",
        min_samples,
        min_line_length,
        errors,
        warnings,
    )
    _validate_line_record(
        calibration.diagonal_line,
        "diagonal_line",
        min_samples,
        min_line_length,
        errors,
        warnings,
    )
    _validate_plane_fit(calibration.plane_fit, errors, warnings)
    _validate_quality(calibration.quality, errors, warnings)

    status = "error" if errors else "warning" if warnings else "ok"
    return CalibrationValidationResult(
        status=status,
        errors=errors,
        warnings=warnings,
        thresholds=thresholds,
    )


def build_task_coordinate_system_from_calibration(
    calibration: FormalCalibration,
) -> TaskCoordinateSystem:
    """Build TaskCoordinateSystem from formal origin, x axis, and up axis."""

    origin = np.asarray(calibration.origin_world, dtype=float)
    x_axis = np.asarray(calibration.x_axis_world, dtype=float)
    return TaskCoordinateSystem.build_from_origin_and_x_point(
        origin,
        origin + x_axis,
        calibration.up_axis_world,
        min_x_axis_length=1e-6,
    )


def _validate_point_record(
    record: CalibrationPointRecord,
    name: str,
    min_samples: int,
    errors: list[str],
    warnings: list[str],
) -> None:
    _finite_vec3(record.mean_world, f"{name}.mean_world", errors)
    if record.sample_count < min_samples:
        errors.append(f"{name}.sample_count must be >= {min_samples}.")
    _validate_duration(record.duration_seconds, name, errors, warnings)
    if record.max_deviation_m > 0.02:
        warnings.append(f"{name}.max_deviation_m is high: {record.max_deviation_m:.6f} m.")


def _validate_line_record(
    record: CalibrationLineRecord,
    name: str,
    min_samples: int,
    min_line_length: float,
    errors: list[str],
    warnings: list[str],
) -> None:
    _finite_vec3(record.centroid_world, f"{name}.centroid_world", errors)
    direction = _finite_vec3(record.direction_world, f"{name}.direction_world", errors)
    if direction is not None:
        _validate_unit_vector(direction, f"{name}.direction_world", errors, warnings)
    if record.sample_count < min_samples:
        errors.append(f"{name}.sample_count must be >= {min_samples}.")
    if record.line_length_m < min_line_length:
        errors.append(f"{name}.line_length_m must be >= {min_line_length}.")
    if record.rmse_m > 0.02:
        warnings.append(f"{name}.rmse_m is high: {record.rmse_m:.6f} m.")
    _validate_duration(record.duration_seconds, name, errors, warnings)


def _validate_plane_fit(
    plane_fit: PlaneFitRecord,
    errors: list[str],
    warnings: list[str],
) -> None:
    _finite_vec3(plane_fit.centroid_world, "plane_fit.centroid_world", errors)
    normal = _finite_vec3(plane_fit.normal_world, "plane_fit.normal_world", errors)
    if normal is not None:
        _validate_unit_vector(normal, "plane_fit.normal_world", errors, warnings)
    if plane_fit.rmse_m > 0.02:
        warnings.append(f"plane_fit.rmse_m is high: {plane_fit.rmse_m:.6f} m.")


def _validate_quality(
    quality: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    long_width_angle = _optional_float(quality.get("long_width_angle_degrees"))
    if long_width_angle is not None:
        deviation = abs(long_width_angle - 90.0)
        if deviation > 25.0:
            errors.append(
                f"long_width_angle_degrees deviates from 90 by {deviation:.3f} degrees."
            )
        elif deviation > 10.0:
            warnings.append(
                f"long_width_angle_degrees deviates from 90 by {deviation:.3f} degrees."
            )
    for key in ("diagonal_x_angle_degrees", "diagonal_y_angle_degrees"):
        angle = _optional_float(quality.get(key))
        if angle is None:
            continue
        if angle < 10.0 or angle > 80.0:
            warnings.append(f"{key} is outside the expected 10-80 degree range: {angle:.3f}.")


def _validate_duration(
    duration_seconds: float | None,
    name: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    if duration_seconds is None:
        return
    if duration_seconds < 1.0:
        errors.append(f"{name}.duration_seconds must be >= 1.0.")
    elif duration_seconds < 3.0:
        warnings.append(f"{name}.duration_seconds is short: {duration_seconds:.3f} s.")


def _validate_unit_vector(
    vector: np.ndarray,
    name: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        errors.append(f"{name} must not be zero.")
    elif abs(norm - 1.0) > 0.01:
        errors.append(f"{name} must be unit length.")
    elif abs(norm - 1.0) > 0.001:
        warnings.append(f"{name} is not exactly unit length.")


def _validate_axis_angle(
    a: np.ndarray,
    b: np.ndarray,
    name: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    angle = _angle_degrees(a, b)
    deviation = abs(angle - 90.0)
    if deviation > 10.0:
        errors.append(f"{name} are not close to orthogonal: {angle:.3f} degrees.")
    elif deviation > 2.0:
        warnings.append(f"{name} angle differs from 90 degrees: {angle:.3f}.")


def _finite_vec3(value: Any, name: str, errors: list[str]) -> np.ndarray | None:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,):
        errors.append(f"{name} must be a 3D list.")
        return None
    if not np.all(np.isfinite(vector)):
        errors.append(f"{name} must contain finite values.")
        return None
    return vector


def _angle_degrees(a: np.ndarray, b: np.ndarray) -> float:
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if a_norm <= 0.0 or b_norm <= 0.0:
        return math.nan
    dot = float(np.dot(a / a_norm, b / b_norm))
    return float(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    return dict(value) if isinstance(value, dict) else {}


def _point_record_from_dict(payload: dict[str, Any]) -> CalibrationPointRecord:
    return CalibrationPointRecord(
        label=str(payload.get("label", "")),
        source=str(payload.get("source", "")),
        mean_world=_float_list(payload.get("mean_world", [])),
        point_world=_float_list(payload.get("point_world", payload.get("mean_world", []))),
        std_world=_float_list(payload.get("std_world", [])),
        max_deviation_m=float(payload.get("max_deviation_m", 0.0)),
        sample_count=int(payload.get("sample_count", 0)),
        duration_seconds=_optional_float(payload.get("duration_seconds")),
        frame_start=_optional_int(payload.get("frame_start")),
        frame_end=_optional_int(payload.get("frame_end")),
        sample_time_start=_optional_float(payload.get("sample_time_start")),
        sample_time_end=_optional_float(payload.get("sample_time_end")),
        time_start=_optional_float(payload.get("time_start", payload.get("sample_time_start"))),
        time_end=_optional_float(payload.get("time_end", payload.get("sample_time_end"))),
        points_world=_points_or_none(payload.get("points_world")),
        metadata=_dict_value(payload, "metadata"),
    )


def _line_record_from_dict(payload: dict[str, Any]) -> CalibrationLineRecord:
    return CalibrationLineRecord(
        label=str(payload.get("label", "")),
        source=str(payload.get("source", "")),
        points_world=_points_or_none(payload.get("points_world")) or [],
        centroid_world=_float_list(payload.get("centroid_world", [])),
        direction_world=_float_list(payload.get("direction_world", [])),
        line_length_m=float(payload.get("line_length_m", 0.0)),
        rmse_m=float(payload.get("rmse_m", payload.get("line_fit_rmse_m", 0.0))),
        sample_count=int(payload.get("sample_count", 0)),
        line_fit_rmse_m=_optional_float(payload.get("line_fit_rmse_m", payload.get("rmse_m"))),
        endpoint_min_world=_float_list_or_none(payload.get("endpoint_min_world")),
        endpoint_max_world=_float_list_or_none(payload.get("endpoint_max_world")),
        duration_seconds=_optional_float(payload.get("duration_seconds")),
        frame_start=_optional_int(payload.get("frame_start")),
        frame_end=_optional_int(payload.get("frame_end")),
        sample_time_start=_optional_float(payload.get("sample_time_start")),
        sample_time_end=_optional_float(payload.get("sample_time_end")),
        time_start=_optional_float(payload.get("time_start", payload.get("sample_time_start"))),
        time_end=_optional_float(payload.get("time_end", payload.get("sample_time_end"))),
        metadata=_dict_value(payload, "metadata"),
    )


def _plane_fit_from_dict(payload: dict[str, Any]) -> PlaneFitRecord:
    return PlaneFitRecord(
        centroid_world=_float_list(payload.get("centroid_world", [])),
        normal_world=_float_list(payload.get("normal_world", [])),
        rmse_m=float(payload.get("rmse_m", payload.get("plane_fit_rmse_m", 0.0))),
        max_abs_distance_m=float(payload.get("max_abs_distance_m", 0.0)),
        sample_count=int(payload.get("sample_count", 0)),
        plane_fit_rmse_m=_optional_float(payload.get("plane_fit_rmse_m", payload.get("rmse_m"))),
        source_labels=(
            [str(item) for item in payload.get("source_labels", [])]
            if isinstance(payload.get("source_labels"), list)
            else []
        ),
        singular_values=_float_list(payload.get("singular_values", [])),
        metadata=_dict_value(payload, "metadata"),
    )


def _points_or_none(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list | tuple):
        return None
    points: list[list[float]] = []
    for item in value:
        converted = _float_list(item)
        if len(converted) == 3:
            points.append(converted)
    return points


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    result: list[float] = []
    for item in value:
        result.append(float(item))
    return result


def _float_list_or_none(value: Any) -> list[float] | None:
    result = _float_list(value)
    return result if result else None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return str(value)
