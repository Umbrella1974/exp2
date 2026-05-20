"""Simple replay helpers for Stage 2 mock samples."""

from __future__ import annotations

from collections.abc import Iterable

from trial_controller import ExperimentInputSample, TrialController, TrialFrameResult


def replay_samples(
    trial_controller: TrialController,
    samples: Iterable[ExperimentInputSample],
) -> list[TrialFrameResult]:
    """Run samples through a TrialController and collect frame results."""

    return [trial_controller.update(sample) for sample in samples]
