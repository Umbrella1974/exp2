"""Tests for pinch hysteresis logic."""

from __future__ import annotations

from data_models import PinchState
from pinch_model import update_pinch_state


def test_pinch_hysteresis() -> None:
    grab_threshold = 0.02
    release_threshold = 0.04

    state = update_pinch_state(0.01, PinchState.PINCH_UNKNOWN, grab_threshold, release_threshold)
    assert state == PinchState.PINCH_VALID

    state = update_pinch_state(0.03, state, grab_threshold, release_threshold)
    assert state == PinchState.PINCH_VALID

    state = update_pinch_state(0.05, state, grab_threshold, release_threshold)
    assert state == PinchState.PINCH_INSUFFICIENT

    state = update_pinch_state(None, state, grab_threshold, release_threshold, data_valid=False)
    assert state == PinchState.PINCH_UNKNOWN

