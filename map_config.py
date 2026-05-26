"""Map configuration models and compilation helpers.

This module converts manual or generated task-space map JSON into the existing
TrackRegion / Box3D types used by BlockController. It does not implement new
physics or modify controller behavior.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from data_models import Box3D, TrackRegion, Vec3
from geometry import point_in_box


MAP_CONFIG_VERSION = 1
EPSILON = 1e-9


@dataclass(frozen=True)
class MapBoxSpec:
    """One axis-aligned box in task coordinates."""

    id: str
    min: list[float]
    max: list[float]
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    order: int | None = None


@dataclass(frozen=True)
class MapConfig:
    """Complete task-space map configuration."""

    map_id: str
    description: str | None
    coordinate_space: str
    unit: str
    block_initial_center_task: list[float]
    block_size: list[float]
    track_boxes: list[MapBoxSpec]
    target_region: MapBoxSpec | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MapValidationResult:
    """Validation result with separate errors and warnings."""

    errors: list[str]
    warnings: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def load_map_config(path: str | Path) -> MapConfig:
    """Load a map JSON file into MapConfig."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("map config must be a JSON object.")
    return map_config_from_dict(payload)


def map_config_from_dict(payload: dict[str, Any]) -> MapConfig:
    """Convert a plain dict into MapConfig."""

    return MapConfig(
        map_id=str(payload.get("map_id", "")),
        description=payload.get("description"),
        coordinate_space=str(payload.get("coordinate_space", "")),
        unit=str(payload.get("unit", "")),
        block_initial_center_task=_float_list(payload.get("block_initial_center_task", [])),
        block_size=_float_list(payload.get("block_size", [])),
        track_boxes=[
            map_box_spec_from_dict(box_payload)
            for box_payload in payload.get("track_boxes", [])
            if isinstance(box_payload, dict)
        ],
        target_region=(
            map_box_spec_from_dict(payload["target_region"])
            if isinstance(payload.get("target_region"), dict)
            else None
        ),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {},
    )


def map_box_spec_from_dict(payload: dict[str, Any]) -> MapBoxSpec:
    """Convert a plain dict into MapBoxSpec."""

    order_value = payload.get("order")
    return MapBoxSpec(
        id=str(payload.get("id", "")),
        min=_float_list(payload.get("min", [])),
        max=_float_list(payload.get("max", [])),
        label=payload.get("label"),
        metadata=dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {},
        order=int(order_value) if order_value is not None else None,
    )


def validate_map_config(config: MapConfig) -> MapValidationResult:
    """Validate a map config without compiling controller logic."""

    errors: list[str] = []
    warnings: list[str] = []

    if not config.map_id:
        errors.append("map_id is required.")
    if config.coordinate_space != "task":
        errors.append('coordinate_space must be "task".')
    if config.unit != "m":
        errors.append('unit must be "m".')
    _validate_vec3(config.block_initial_center_task, "block_initial_center_task", errors)
    _validate_vec3(config.block_size, "block_size", errors, require_positive=True)
    if not config.track_boxes:
        errors.append("track_boxes must not be empty.")

    for index, box in enumerate(config.track_boxes):
        _validate_box(box, f"track_boxes[{index}]", errors)

    if config.target_region is not None:
        _validate_box(config.target_region, "target_region", errors)

    if errors:
        return MapValidationResult(errors=errors, warnings=warnings)

    block_center = _vec3(config.block_initial_center_task)
    if not any(_point_in_spec(block_center, box) for box in config.track_boxes):
        errors.append("block_initial_center_task must be inside at least one track box.")

    if config.target_region is not None:
        target_relation = _target_relation(config.target_region, config.track_boxes)
        if target_relation == "none":
            errors.append("target_region must intersect at least one track box with positive volume.")
        elif target_relation == "touch":
            warnings.append("target_region only touches track boxes without positive volume overlap.")

    ordered_boxes = [box for box in config.track_boxes if box.order is not None]
    if ordered_boxes:
        ordered_boxes = sorted(ordered_boxes, key=lambda box: int(box.order or 0))
        for previous, current in zip(ordered_boxes, ordered_boxes[1:]):
            relation = _box_relation(previous, current)
            if relation == "gap":
                errors.append(
                    f"ordered track boxes {previous.id} and {current.id} have a gap."
                )
            elif relation in ("edge_or_point_touch", "touch_without_area"):
                errors.append(
                    f"ordered track boxes {previous.id} and {current.id} only touch by edge/point or zero-area face."
                )

    return MapValidationResult(errors=errors, warnings=warnings)


def compile_map_to_track_region(config: MapConfig) -> tuple[TrackRegion, Vec3, Vec3]:
    """Compile MapConfig to TrackRegion plus block initial center and size."""

    validation = validate_map_config(config)
    if not validation.is_valid:
        raise ValueError("; ".join(validation.errors))
    track = TrackRegion(boxes=tuple(_box_spec_to_box3d(box) for box in config.track_boxes))
    return track, _vec3(config.block_initial_center_task), _vec3(config.block_size)


def map_config_to_trial_config(config: MapConfig) -> dict[str, Any]:
    """Return a JSON-serializable trial config payload for sessions."""

    generated = bool(config.metadata.get("generated", False))
    return {
        "map_config_version": MAP_CONFIG_VERSION,
        "map_id": config.map_id,
        "map_source": config.map_id,
        "map_source_type": "generated" if generated else "manual",
        "coordinate_space": config.coordinate_space,
        "unit": config.unit,
        "block_initial_center_task": list(config.block_initial_center_task),
        "block_size": list(config.block_size),
        "track_boxes": [map_box_spec_to_dict(box) for box in config.track_boxes],
        "target_region": (
            map_box_spec_to_dict(config.target_region)
            if config.target_region is not None
            else None
        ),
        "is_generated": generated,
        "metadata": _json_safe(config.metadata),
    }


def map_config_to_dict(config: MapConfig) -> dict[str, Any]:
    """Return a JSON-serializable map config dict."""

    return {
        "map_id": config.map_id,
        "description": config.description,
        "coordinate_space": config.coordinate_space,
        "unit": config.unit,
        "block_initial_center_task": list(config.block_initial_center_task),
        "block_size": list(config.block_size),
        "track_boxes": [map_box_spec_to_dict(box) for box in config.track_boxes],
        "target_region": (
            map_box_spec_to_dict(config.target_region)
            if config.target_region is not None
            else None
        ),
        "metadata": _json_safe(config.metadata),
    }


def map_box_spec_to_dict(box: MapBoxSpec) -> dict[str, Any]:
    """Return a JSON-serializable box dict."""

    return {
        "id": box.id,
        "order": box.order,
        "label": box.label,
        "min": list(box.min),
        "max": list(box.max),
        "metadata": _json_safe(box.metadata),
    }


def _validate_vec3(
    value: list[float],
    name: str,
    errors: list[str],
    *,
    require_positive: bool = False,
) -> None:
    if len(value) != 3:
        errors.append(f"{name} must be a 3D list.")
        return
    for component in value:
        if not isinstance(component, int | float):
            errors.append(f"{name} must contain numeric values.")
            return
        if require_positive and component <= 0:
            errors.append(f"{name} components must be > 0.")
            return


def _validate_box(box: MapBoxSpec, name: str, errors: list[str]) -> None:
    if not box.id:
        errors.append(f"{name}.id is required.")
    _validate_vec3(box.min, f"{name}.min", errors)
    _validate_vec3(box.max, f"{name}.max", errors)
    if len(box.min) == 3 and len(box.max) == 3:
        for axis in range(3):
            if box.min[axis] >= box.max[axis]:
                errors.append(f"{name}.min must be < max on every axis.")
                break


def _box_spec_to_box3d(box: MapBoxSpec) -> Box3D:
    min_corner = box.min
    max_corner = box.max
    center = [
        (min_corner[index] + max_corner[index]) * 0.5
        for index in range(3)
    ]
    size = [max_corner[index] - min_corner[index] for index in range(3)]
    return Box3D(center=_vec3(center), size=_vec3(size))


def _point_in_spec(point: Vec3, box: MapBoxSpec) -> bool:
    return point_in_box(point, _box_spec_to_box3d(box), epsilon=EPSILON)


def _target_relation(target: MapBoxSpec, boxes: list[MapBoxSpec]) -> str:
    saw_touch = False
    for box in boxes:
        relation = _box_relation(target, box)
        if relation == "volume_overlap":
            return "volume"
        if relation != "gap":
            saw_touch = True
    return "touch" if saw_touch else "none"


def _box_relation(a: MapBoxSpec, b: MapBoxSpec) -> str:
    overlaps = [_axis_overlap(a.min[index], a.max[index], b.min[index], b.max[index]) for index in range(3)]
    positive_axes = sum(value > EPSILON for value in overlaps)
    touching_axes = sum(abs(value) <= EPSILON for value in overlaps)
    if any(value < -EPSILON for value in overlaps):
        return "gap"
    if positive_axes == 3:
        return "volume_overlap"
    if positive_axes == 2 and touching_axes == 1:
        return "face_touch"
    if positive_axes < 2 and touching_axes >= 1:
        return "edge_or_point_touch"
    return "touch_without_area"


def _axis_overlap(a_min: float, a_max: float, b_min: float, b_max: float) -> float:
    return min(a_max, b_max) - max(a_min, b_min)


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, list | tuple):
        return []
    result: list[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            result.append(item)
    return result


def _vec3(values: list[float]) -> Vec3:
    return Vec3(float(values[0]), float(values[1]), float(values[2]))


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    return str(value)
