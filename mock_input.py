"""Mock input generators for Stage 2 tests and local replay."""

from __future__ import annotations

from collections.abc import Iterable

from data_models import Vec3
from trial_controller import ExperimentInputSample


MockInputSample = ExperimentInputSample


def generate_straight_motion_trial(
    *,
    start_time: float = 0.0,
    dt: float = 0.1,
) -> Iterable[MockInputSample]:
    """Generate a simple successful straight-motion trial in task space."""

    samples = (
        (-0.8, 0.06, True, False),
        (0.0, 0.06, True, False),
        (0.1, 0.01, True, False),
        (0.25, 0.01, True, False),
        (0.4, 0.01, True, False),
        (0.55, 0.01, True, True),
    )
    for index, (x, distance, valid, subject_end) in enumerate(samples):
        yield MockInputSample(
            time=start_time + index * dt,
            pinch_center_task=Vec3(x, 0.0, 0.0),
            pinch_distance=distance,
            tracker_valid=valid,
            subject_end=subject_end,
        )


def generate_tracking_loss_trial(
    *,
    start_time: float = 0.0,
    dt: float = 0.1,
) -> Iterable[MockInputSample]:
    """Generate contact, tracking loss, recovery, and resumed contact frames."""

    samples = (
        (0.0, 0.01, True, False),
        (0.2, 0.01, True, False),
        (1.0, 0.01, False, False),
        (1.0, 0.01, False, False),
        (1.0, 0.01, True, False),
        (0.2, 0.01, True, True),
    )
    for index, (x, distance, valid, subject_end) in enumerate(samples):
        yield MockInputSample(
            time=start_time + index * dt,
            pinch_center_task=Vec3(x, 0.0, 0.0),
            pinch_distance=distance,
            tracker_valid=valid,
            subject_end=subject_end,
        )


def generate_blocked_trial(
    *,
    start_time: float = 0.0,
    dt: float = 0.1,
) -> Iterable[MockInputSample]:
    """Generate frames that drive the block into a track boundary."""

    samples = (
        (0.7, 0.01, True, False),
        (1.1, 0.01, True, False),
        (1.2, 0.01, True, True),
    )
    for index, (x, distance, valid, subject_end) in enumerate(samples):
        yield MockInputSample(
            time=start_time + index * dt,
            pinch_center_task=Vec3(x, 0.0, 0.0),
            pinch_distance=distance,
            tracker_valid=valid,
            subject_end=subject_end,
        )
