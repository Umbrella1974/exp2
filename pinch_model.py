"""Pinch hysteresis logic."""

from __future__ import annotations

from data_models import PinchState


def update_pinch_state(
    distance: float | None,
    previous_state: PinchState,
    grab_threshold: float,
    release_threshold: float,
    *,
    data_valid: bool = True,
) -> PinchState:
    """Update the pinch state using hysteresis thresholds."""

    if grab_threshold >= release_threshold:
        raise ValueError("grab_threshold must be smaller than release_threshold.")

    if not data_valid or distance is None:
        return PinchState.PINCH_UNKNOWN

    if distance < grab_threshold:
        return PinchState.PINCH_VALID

    if distance > release_threshold:
        return PinchState.PINCH_INSUFFICIENT

    return previous_state
