"""Configuration for the constrained block interaction engine."""

from __future__ import annotations

from dataclasses import dataclass

from data_models import Vec3


@dataclass(frozen=True)
class EngineConfig:
    """Tunable thresholds and dimensions for the engine."""

    block_size_x: float = 1.0
    block_size_y: float = 1.0
    block_size_z: float = 1.0
    track_epsilon: float = 1e-6
    pinch_grab_threshold: float = 0.025
    pinch_release_threshold: float = 0.035
    max_hand_delta_per_frame: float = 0.25
    min_block_move_distance: float = 1e-5
    blocked_feedback_threshold: float = 1e-4
    binary_search_iterations: int = 24
    max_detach_count: int = 3
    trial_timeout_seconds: float = 60.0
    slip_motion_threshold: float = 1e-4

    @property
    def block_size(self) -> Vec3:
        """Return the configured block size as a vector."""

        return Vec3(self.block_size_x, self.block_size_y, self.block_size_z)
