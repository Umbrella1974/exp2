"""Tests for Stage 2 mock input replay."""

from __future__ import annotations

from block_controller import BlockController
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from mock_input import generate_blocked_trial, generate_straight_motion_trial, generate_tracking_loss_trial
from replay import replay_samples
from trial_controller import TrialController, TrialState


def make_controller(
    config: EngineConfig | None = None,
    *,
    track_size: float = 4.0,
    initial_block_center: Vec3 = Vec3(0.0, 0.0, 0.0),
) -> TrialController:
    config = config or EngineConfig(max_hand_delta_per_frame=0.5, slip_motion_threshold=0.05)
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(track_size, track_size, track_size)),))

    def factory() -> BlockController:
        return BlockController(config, track, initial_block_center)

    return TrialController(factory, None, config)


def test_mock_straight_trial_runs_complete_flow() -> None:
    controller = make_controller()
    controller.start_trial(time=0.0, trial_id="mock-straight")
    results = replay_samples(controller, generate_straight_motion_trial())
    event_types = [event.event_type for event in controller.event_history]
    assert results[-1].trial_state == TrialState.ENDED_BY_SUBJECT
    assert "prompt_on" in event_types
    assert "contact_enter" in event_types
    assert "block_moved" in event_types
    assert "subject_end" in event_types


def test_mock_tracking_loss_trial_records_tracking_edges() -> None:
    controller = make_controller()
    controller.start_trial(time=0.0, trial_id="mock-tracking")
    replay_samples(controller, generate_tracking_loss_trial())
    event_types = [event.event_type for event in controller.event_history]
    assert event_types.count("tracking_invalid") == 1
    assert event_types.count("tracking_recovered") == 1


def test_mock_blocked_trial_reaches_blocked_feedback() -> None:
    controller = make_controller(track_size=2.0, initial_block_center=Vec3(0.7, 0.0, 0.0))
    controller.start_trial(time=0.0, trial_id="mock-blocked")
    results = replay_samples(controller, generate_blocked_trial())
    event_types = [event.event_type for event in controller.event_history]
    assert "block_blocked_start" in event_types
    assert any(result.frame_output.haptic_feedback.blocked_force_active for result in results)
