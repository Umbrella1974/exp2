"""Template map generation aligned to an observed task-space trajectory.

Template maps are diagnostic helpers for old offline data. A template is written
in a local "template" coordinate space, then rotated by a 90-degree x-y rotation
and translated into task space. The output is a normal MapConfig.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from map_config import MapBoxSpec, MapConfig, map_box_spec_from_dict


DIRECTION_VECTORS = {
    "x+": np.asarray([1.0, 0.0, 0.0], dtype=float),
    "x-": np.asarray([-1.0, 0.0, 0.0], dtype=float),
    "y+": np.asarray([0.0, 1.0, 0.0], dtype=float),
    "y-": np.asarray([0.0, -1.0, 0.0], dtype=float),
}

DIRECTION_ANGLES = {
    "x+": 0,
    "y+": 90,
    "x-": 180,
    "y-": 270,
}

MIN_DIRECTION_LENGTH = 1e-4


@dataclass(frozen=True)
class MapTemplateConfig:
    """Map template written in local template coordinates."""

    template_id: str
    description: str | None
    coordinate_space: str
    unit: str
    anchor_direction: str
    block_initial_center_template: list[float]
    block_size: list[float]
    track_boxes: list[MapBoxSpec]
    target_region: MapBoxSpec | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectionInfo:
    """Estimated and snapped main direction in task coordinates."""

    raw_main_direction: list[float]
    snapped_main_direction: str
    snap_angle_degrees: float
    points_used: int
    estimation_method: str


def load_map_template(path: str | Path) -> MapTemplateConfig:
    """Load and validate a map template JSON."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("map template must be a JSON object.")
    return map_template_from_dict(payload)


def map_template_from_dict(payload: dict[str, Any]) -> MapTemplateConfig:
    """Convert a plain dict into a MapTemplateConfig."""

    track_payloads = payload.get("track_boxes", [])
    track_boxes = [
        map_box_spec_from_dict(item)
        for item in track_payloads
        if isinstance(item, dict)
    ]
    template = MapTemplateConfig(
        template_id=str(payload.get("template_id", "")),
        description=payload.get("description"),
        coordinate_space=str(payload.get("coordinate_space", "")),
        unit=str(payload.get("unit", "")),
        anchor_direction=str(payload.get("anchor_direction", "x+")),
        block_initial_center_template=_vec3(
            payload.get("block_initial_center_template", [0.0, 0.0, 0.0]),
            "block_initial_center_template",
        ),
        block_size=_positive_vec3(payload.get("block_size", []), "block_size"),
        track_boxes=track_boxes,
        target_region=(
            map_box_spec_from_dict(payload["target_region"])
            if isinstance(payload.get("target_region"), dict)
            else None
        ),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {},
    )
    _validate_template(template)
    return template


def estimate_main_direction_from_points(points: Any, n_frames: int) -> DirectionInfo:
    """Estimate the first-segment main direction and snap it to task x/y axes."""

    if n_frames <= 0:
        raise ValueError("n_frames must be > 0.")
    array = _valid_points(points)[:n_frames]
    if len(array) < 2:
        raise ValueError("template direction estimation requires at least two valid points.")
    origin = array[0]
    offsets = array - origin
    offsets[:, 2] = 0.0
    distances = np.linalg.norm(offsets, axis=1)
    index = int(np.argmax(distances))
    if float(distances[index]) >= MIN_DIRECTION_LENGTH:
        raw = offsets[index] / distances[index]
        method = "farthest_point_from_first_anchor_window"
    else:
        raw = _pca_direction_xy(array)
        method = "pca_first_component_anchor_window"

    snapped, angle = _snap_direction_to_task_axis(raw)
    return DirectionInfo(
        raw_main_direction=[float(raw[0]), float(raw[1]), 0.0],
        snapped_main_direction=snapped,
        snap_angle_degrees=angle,
        points_used=int(len(array)),
        estimation_method=method,
    )


def transform_template_to_map_config(
    template: MapTemplateConfig,
    origin_task: Any,
    snapped_main_direction: str,
    *,
    direction_info: DirectionInfo | None = None,
) -> MapConfig:
    """Rotate and translate a template map into task-space MapConfig."""

    if snapped_main_direction not in DIRECTION_VECTORS:
        raise ValueError(f"unsupported snapped_main_direction: {snapped_main_direction}")
    origin = np.asarray(_vec3(origin_task, "origin_task"), dtype=float)
    anchor = np.asarray(template.block_initial_center_template, dtype=float)
    rotation_degrees = _rotation_degrees(template.anchor_direction, snapped_main_direction)
    rotation = _rotation_matrix_z(rotation_degrees)

    def transform_point(point: list[float]) -> list[float]:
        source = np.asarray(point, dtype=float)
        transformed = origin + rotation @ (source - anchor)
        return [float(transformed[0]), float(transformed[1]), float(transformed[2])]

    metadata = dict(template.metadata)
    metadata.update(
        {
            "generated": True,
            "generator_name": "template_aligned_to_trajectory",
            "template_id": template.template_id,
            "template_anchor_direction": template.anchor_direction,
            "raw_main_direction": (
                list(direction_info.raw_main_direction) if direction_info is not None else None
            ),
            "snapped_main_direction": snapped_main_direction,
            "snap_angle_degrees": (
                direction_info.snap_angle_degrees if direction_info is not None else None
            ),
            "direction_points_used": direction_info.points_used if direction_info is not None else None,
            "direction_estimation_method": (
                direction_info.estimation_method if direction_info is not None else None
            ),
            "rotation_degrees": rotation_degrees,
            "origin_task": [float(origin[0]), float(origin[1]), float(origin[2])],
            "post_hoc": True,
            "diagnostic": True,
        }
    )

    return MapConfig(
        map_id=template.template_id,
        description=template.description,
        coordinate_space="task",
        unit=template.unit,
        block_initial_center_task=transform_point(template.block_initial_center_template),
        block_size=_transform_size(template.block_size, rotation),
        track_boxes=[
            _transform_box(box, transform_point, index)
            for index, box in enumerate(template.track_boxes)
        ],
        target_region=(
            _transform_box(template.target_region, transform_point, None)
            if template.target_region is not None
            else None
        ),
        metadata=metadata,
    )


def _transform_box(
    box: MapBoxSpec,
    transform_point: Any,
    fallback_order: int | None,
) -> MapBoxSpec:
    corners = []
    for x in (box.min[0], box.max[0]):
        for y in (box.min[1], box.max[1]):
            for z in (box.min[2], box.max[2]):
                corners.append(transform_point([x, y, z]))
    array = np.asarray(corners, dtype=float)
    return MapBoxSpec(
        id=box.id,
        order=box.order if box.order is not None else fallback_order,
        label=box.label,
        min=[float(np.min(array[:, axis])) for axis in range(3)],
        max=[float(np.max(array[:, axis])) for axis in range(3)],
        metadata=dict(box.metadata),
    )


def _transform_size(size: list[float], rotation: np.ndarray) -> list[float]:
    half = np.asarray(size, dtype=float) * 0.5
    corners = []
    for x in (-half[0], half[0]):
        for y in (-half[1], half[1]):
            for z in (-half[2], half[2]):
                corners.append(rotation @ np.asarray([x, y, z], dtype=float))
    array = np.asarray(corners, dtype=float)
    return [float(np.max(array[:, axis]) - np.min(array[:, axis])) for axis in range(3)]


def _valid_points(points: Any) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("points must be an Nx3 array-like value.")
    mask = np.all(np.isfinite(array), axis=1)
    return array[mask]


def _pca_direction_xy(points: np.ndarray) -> np.ndarray:
    xy = points[:, :2]
    centered = xy - np.mean(xy, axis=0)
    if float(np.max(np.linalg.norm(centered, axis=1))) < MIN_DIRECTION_LENGTH:
        raise ValueError("Unable to estimate template main direction from concentrated task points.")
    _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    if len(singular_values) == 0 or float(singular_values[0]) < MIN_DIRECTION_LENGTH:
        raise ValueError("Unable to estimate template main direction from concentrated task points.")
    axis_xy = vh[0]
    hint = xy[-1] - xy[0]
    if float(np.dot(axis_xy, hint)) < 0.0:
        axis_xy = -axis_xy
    norm = float(np.linalg.norm(axis_xy))
    if norm < MIN_DIRECTION_LENGTH:
        raise ValueError("Unable to estimate template main direction from concentrated task points.")
    return np.asarray([axis_xy[0] / norm, axis_xy[1] / norm, 0.0], dtype=float)


def _snap_direction_to_task_axis(direction: np.ndarray) -> tuple[str, float]:
    xy_norm = float(np.linalg.norm(direction[:2]))
    if xy_norm < MIN_DIRECTION_LENGTH:
        raise ValueError("Unable to estimate template main direction from vertical-only movement.")
    unit = np.asarray([direction[0] / xy_norm, direction[1] / xy_norm, 0.0], dtype=float)
    best_label = "x+"
    best_dot = -math.inf
    for label, axis in DIRECTION_VECTORS.items():
        dot = float(np.dot(unit, axis))
        if dot > best_dot:
            best_label = label
            best_dot = dot
    angle = math.degrees(math.acos(max(-1.0, min(1.0, best_dot))))
    return best_label, float(angle)


def _rotation_degrees(anchor_direction: str, target_direction: str) -> int:
    if anchor_direction not in DIRECTION_ANGLES:
        raise ValueError(f"unsupported template anchor_direction: {anchor_direction}")
    target = DIRECTION_ANGLES[target_direction]
    anchor = DIRECTION_ANGLES[anchor_direction]
    degrees = (target - anchor) % 360
    if degrees > 180:
        degrees -= 360
    return int(degrees)


def _rotation_matrix_z(degrees: int) -> np.ndarray:
    radians = math.radians(degrees)
    c = math.cos(radians)
    s = math.sin(radians)
    return np.asarray(
        [
            [c, -s, 0.0],
            [s, c, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def _validate_template(template: MapTemplateConfig) -> None:
    errors: list[str] = []
    if not template.template_id:
        errors.append("template_id is required.")
    if template.coordinate_space != "template":
        errors.append('coordinate_space must be "template".')
    if template.unit != "m":
        errors.append('unit must be "m".')
    if template.anchor_direction not in DIRECTION_VECTORS:
        errors.append("anchor_direction must be one of x+, x-, y+, y-.")
    if not template.track_boxes:
        errors.append("track_boxes must not be empty.")
    for index, box in enumerate(template.track_boxes):
        _validate_box(box, f"track_boxes[{index}]", errors)
    if template.target_region is not None:
        _validate_box(template.target_region, "target_region", errors)
    if errors:
        raise ValueError("map template validation failed: " + "; ".join(errors))


def _validate_box(box: MapBoxSpec, name: str, errors: list[str]) -> None:
    if not box.id:
        errors.append(f"{name}.id is required.")
    try:
        minimum = _vec3(box.min, f"{name}.min")
        maximum = _vec3(box.max, f"{name}.max")
    except ValueError as exc:
        errors.append(str(exc))
        return
    if any(minimum[index] >= maximum[index] for index in range(3)):
        errors.append(f"{name}.min must be < max on every axis.")


def _positive_vec3(value: Any, name: str) -> list[float]:
    values = _vec3(value, name)
    if any(component <= 0.0 for component in values):
        raise ValueError(f"{name} components must be > 0.")
    return values


def _vec3(value: Any, name: str) -> list[float]:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ValueError(f"{name} must be a 3D list.")
    result: list[float] = []
    for component in value:
        try:
            number = float(component)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain numeric values.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must contain finite values.")
        result.append(number)
    return result
