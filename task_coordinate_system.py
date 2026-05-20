"""Task-space coordinate system utilities for Stage 2 experiments."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _as_vector(value: object) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,):
        raise ValueError("Expected a 3D vector.")
    return vector


def _normalize(value: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(value))
    if norm <= 0.0:
        raise ValueError("Cannot normalize a zero-length vector.")
    return value / norm


@dataclass(frozen=True)
class TaskCoordinateSystem:
    """Right-handed task coordinate system built from world-space axes."""

    origin_world: np.ndarray
    x_axis_world: np.ndarray
    y_axis_world: np.ndarray
    z_axis_world: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin_world", _as_vector(self.origin_world))
        object.__setattr__(self, "x_axis_world", _normalize(_as_vector(self.x_axis_world)))
        object.__setattr__(self, "y_axis_world", _normalize(_as_vector(self.y_axis_world)))
        object.__setattr__(self, "z_axis_world", _normalize(_as_vector(self.z_axis_world)))

    def world_to_task(self, p_world: object) -> np.ndarray:
        """Convert a world-space point to task-space coordinates."""

        relative = _as_vector(p_world) - self.origin_world
        return np.array(
            [
                float(np.dot(relative, self.x_axis_world)),
                float(np.dot(relative, self.y_axis_world)),
                float(np.dot(relative, self.z_axis_world)),
            ]
        )

    def task_to_world(self, p_task: object) -> np.ndarray:
        """Convert a task-space point to world-space coordinates."""

        point = _as_vector(p_task)
        return (
            self.origin_world
            + point[0] * self.x_axis_world
            + point[1] * self.y_axis_world
            + point[2] * self.z_axis_world
        )

    def vector_world_to_task(self, v_world: object) -> np.ndarray:
        """Convert a world-space vector to task-space coordinates."""

        vector = _as_vector(v_world)
        return np.array(
            [
                float(np.dot(vector, self.x_axis_world)),
                float(np.dot(vector, self.y_axis_world)),
                float(np.dot(vector, self.z_axis_world)),
            ]
        )

    def vector_task_to_world(self, v_task: object) -> np.ndarray:
        """Convert a task-space vector to world-space coordinates."""

        vector = _as_vector(v_task)
        return (
            vector[0] * self.x_axis_world
            + vector[1] * self.y_axis_world
            + vector[2] * self.z_axis_world
        )

    @classmethod
    def build_from_origin_and_x_point(
        cls,
        origin_world: object,
        x_point_world: object,
        up_axis_world: object,
        min_x_axis_length: float = 0.1,
    ) -> "TaskCoordinateSystem":
        """Build task axes from an origin, a horizontal x-point, and up axis."""

        origin = _as_vector(origin_world)
        x_point = _as_vector(x_point_world)
        z_axis = _normalize(_as_vector(up_axis_world))

        x_raw = x_point - origin
        x_projected = x_raw - np.dot(x_raw, z_axis) * z_axis
        x_length = float(np.linalg.norm(x_projected))
        if x_length < min_x_axis_length:
            raise ValueError("Projected x_axis is too short.")

        x_axis = x_projected / x_length
        y_axis = _normalize(np.cross(z_axis, x_axis))
        x_axis = _normalize(np.cross(y_axis, z_axis))

        return cls(
            origin_world=origin,
            x_axis_world=x_axis,
            y_axis_world=y_axis,
            z_axis_world=z_axis,
        )


def build_from_origin_and_x_point(
    origin_world: object,
    x_point_world: object,
    up_axis_world: object,
    min_x_axis_length: float = 0.1,
) -> TaskCoordinateSystem:
    """Build a TaskCoordinateSystem from world-space calibration points."""

    return TaskCoordinateSystem.build_from_origin_and_x_point(
        origin_world,
        x_point_world,
        up_axis_world,
        min_x_axis_length=min_x_axis_length,
    )
