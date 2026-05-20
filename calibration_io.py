"""Save and load task coordinate calibration files."""

from __future__ import annotations

import json
from pathlib import Path

from task_coordinate_system import TaskCoordinateSystem


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
