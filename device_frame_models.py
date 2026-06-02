"""Stable Stage 3 adapter-layer models for MANUS/Vive style input."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import numpy as np


def as_vector3(value: object, *, field_name: str = "vector") -> np.ndarray:
    """Convert a value to a finite numpy vector with shape (3,)."""

    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,):
        raise ValueError(f"{field_name} must have shape (3,).")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{field_name} must contain finite values.")
    return vector


def as_rotation4(value: object | None, *, field_name: str = "rotation") -> np.ndarray | None:
    """Convert a value to a finite quaternion vector with shape (4,), or None."""

    if value is None:
        return None
    rotation = np.asarray(value, dtype=float)
    if rotation.shape != (4,):
        raise ValueError(f"{field_name} must have shape (4,).")
    if not np.all(np.isfinite(rotation)):
        raise ValueError(f"{field_name} must contain finite values.")
    return rotation


@dataclass(frozen=True)
class DeviceAdapterConfig:
    """Configuration for Stage 3 parsing and simple device adaptation."""

    PINCH_POSITION_MODES = ("tracker_plus_local", "nodes_world")

    skeleton_index: int = 0
    tracker_index: int = 0
    thumb_tip_node_id: int = 4
    index_tip_node_id: int = 9
    local_offset: object = field(default_factory=lambda: np.zeros(3))
    local_scale: float = 1.0
    use_tracker_rotation: bool = False
    pinch_position_mode: str = "tracker_plus_local"
    timestamp_scale: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "local_offset",
            as_vector3(self.local_offset, field_name="local_offset"),
        )
        scale = float(self.local_scale)
        if not isfinite(scale):
            raise ValueError("local_scale must be a finite float.")
        object.__setattr__(self, "local_scale", scale)

        mode = str(self.pinch_position_mode)
        if mode not in self.PINCH_POSITION_MODES:
            allowed = ", ".join(self.PINCH_POSITION_MODES)
            raise ValueError(f"pinch_position_mode must be one of: {allowed}.")
        object.__setattr__(self, "pinch_position_mode", mode)

        timestamp_scale = float(self.timestamp_scale)
        if not isfinite(timestamp_scale):
            raise ValueError("timestamp_scale must be a finite float.")
        object.__setattr__(self, "timestamp_scale", timestamp_scale)


@dataclass(frozen=True)
class Pose3D:
    """3D pose in adapter space."""

    position: np.ndarray
    rotation: np.ndarray | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", as_vector3(self.position, field_name="position"))
        object.__setattr__(self, "rotation", as_rotation4(self.rotation))


@dataclass(frozen=True)
class ManusNodeData:
    """One MANUS skeleton node sample."""

    node_id: int
    position: np.ndarray
    rotation: np.ndarray | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", int(self.node_id))
        object.__setattr__(self, "position", as_vector3(self.position, field_name="position"))
        object.__setattr__(self, "rotation", as_rotation4(self.rotation))


@dataclass(frozen=True)
class ManusHandFrame:
    """Parsed MANUS hand frame."""

    glove_id: str | None
    side: str | None
    nodes: dict[int, ManusNodeData]
    valid: bool


@dataclass(frozen=True)
class ViveTrackerFrame:
    """Parsed Vive tracker frame."""

    tracker_index: int
    tracker_id: str | None
    pose_world: Pose3D
    valid: bool
    quality: int | bool | None = None
    last_update_time: float | int | None = None


@dataclass(frozen=True)
class DeviceFrame:
    """Stable device adapter frame consumed before Stage 2 conversion."""

    time: float
    source_timestamp: float | int | str | None
    source_frame_id: int | None
    tracker: ViveTrackerFrame | None
    hand: ManusHandFrame | None
    raw: dict[str, Any] | None = None
    combined_monotonic_ms: float | None = None
    skeleton_publish_time: float | int | None = None
    tracker_publish_time: float | int | None = None
    skeleton_receive_monotonic_ms: float | None = None
    tracker_receive_monotonic_ms: float | None = None
    skeleton_callback_index: int | None = None
    tracker_callback_index: int | None = None
    skeleton_frame_id: int | None = None
    tracker_frame_id: int | None = None
    sync_delta_ms: float | None = None
