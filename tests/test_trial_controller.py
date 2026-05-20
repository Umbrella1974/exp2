"""Tests for Stage 2 trial lifecycle behavior."""

from __future__ import annotations

from block_controller import BlockController
from config import EngineConfig
from data_models import BlockMotionState, Box3D, StopReason, TrackRegion, Vec3
from task_coordinate_system import build_from_origin_and_x_point
from trial_controller import ExperimentInputSample, TrialController, TrialState


def make_config(**overrides: float) -> EngineConfig:
    defaults = {
        "block_size_x": 1.0,
        "block_size_y": 1.0,
        "block_size_z": 1.0,
        "pinch_grab_threshold": 0.02,
        "pinch_release_threshold": 0.04,
        "max_hand_delta_per_frame": 0.5,
        "trial_timeout_seconds": 1.0,
        "max_detach_count": 3,
        "slip_motion_threshold": 0.05,
        "binary_search_iterations": 40,
    }
    defaults.update(overrides)
    return EngineConfig(**defaults)


def make_track(size: float = 4.0) -> TrackRegion:
    return TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(size, size, size)),))


def make_trial_controller(
    config: EngineConfig | None = None,
    *,
    track: TrackRegion | None = None,
    initial_block_center: Vec3 = Vec3(0.0, 0.0, 0.0),
    recorder: object | None = None,
) -> TrialController:
    config = config or make_config()
    track = track or make_track()

    def factory() -> BlockController:
        return BlockController(config, track, initial_block_center)

    system = build_from_origin_and_x_point([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    return TrialController(factory, system, config, recorder=recorder)


def sample(
    time: float,
    x: float,
    *,
    distance: float = 0.01,
    tracker_valid: bool = True,
    subject_end: bool = False,
) -> ExperimentInputSample:
    return ExperimentInputSample(
        time=time,
        pinch_center_task=Vec3(x, 0.0, 0.0),
        pinch_distance=distance,
        tracker_valid=tracker_valid,
        subject_end=subject_end,
    )


def event_types(controller: TrialController) -> list[str]:
    return [event.event_type for event in controller.event_history]


def test_start_trial_enters_running_and_records_prompt_on() -> None:
    controller = make_trial_controller()
    controller.start_trial(time=10.0, trial_id="trial-a")
    assert controller.trial_state == TrialState.RUNNING
    assert controller.prompt_on_time == 10.0
    assert event_types(controller)[:2] == ["trial_started", "prompt_on"]
    assert controller.event_history[0].trial_id == "trial-a"


def test_subject_end_updates_block_first_then_ends_trial() -> None:
    controller = make_trial_controller()
    controller.start_trial(time=0.0, trial_id=1)
    controller.update(sample(0.0, 0.0))
    result = controller.update(sample(0.1, 0.2, subject_end=True))
    assert result.trial_state == TrialState.ENDED_BY_SUBJECT
    assert result.frame_output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
    assert "subject_end" in [event.event_type for event in result.events]
    assert result.failure_reason is None


def test_subject_end_has_priority_over_timeout() -> None:
    controller = make_trial_controller(make_config(trial_timeout_seconds=0.1))
    controller.start_trial(time=0.0, trial_id=1)
    result = controller.update(sample(1.0, 0.0, subject_end=True))
    assert result.trial_state == TrialState.ENDED_BY_SUBJECT
    assert "timeout" not in [event.event_type for event in result.events]


def test_timeout_after_frame_update_records_event() -> None:
    controller = make_trial_controller(make_config(trial_timeout_seconds=0.1))
    controller.start_trial(time=0.0, trial_id=1)
    result = controller.update(sample(0.2, 0.0))
    assert result.trial_state == TrialState.FAILED_TIMEOUT
    assert result.failure_reason == "timeout"
    assert "timeout" in [event.event_type for event in result.events]


def test_too_many_detaches_after_frame_update_records_event() -> None:
    controller = make_trial_controller(make_config(max_detach_count=0))
    controller.start_trial(time=0.0, trial_id=1)
    controller.update(sample(0.0, 0.0))
    result = controller.update(sample(0.1, 0.7))
    assert result.trial_state == TrialState.FAILED_TOO_MANY_DETACHES
    assert result.failure_reason == "too_many_detaches"
    assert "too_many_detaches" in [event.event_type for event in result.events]


def test_tracking_invalid_keeps_running_and_recovery_defers_contact_edges() -> None:
    controller = make_trial_controller()
    controller.start_trial(time=0.0, trial_id=1)
    controller.update(sample(0.0, 0.0))

    invalid = controller.update(sample(0.1, 1.0, tracker_valid=False))
    assert invalid.trial_state == TrialState.RUNNING
    assert invalid.frame_output.feedback_state.stop_reason == StopReason.TRACKING_INVALID
    assert "tracking_invalid" in [event.event_type for event in invalid.events]
    assert "contact_exit" not in [event.event_type for event in invalid.events]

    recovery = controller.update(sample(0.2, 1.0, tracker_valid=True))
    assert recovery.trial_state == TrialState.RUNNING
    assert recovery.frame_output.feedback_state.recovery_frame is True
    assert "tracking_recovered" in [event.event_type for event in recovery.events]
    assert "contact_exit" not in [event.event_type for event in recovery.events]

    reenter = controller.update(sample(0.3, 0.0, tracker_valid=True))
    assert "contact_enter" in [event.event_type for event in reenter.events]


def test_recorder_is_optional() -> None:
    controller = make_trial_controller(recorder=None)
    controller.start_trial(time=0.0, trial_id=1)
    result = controller.update(sample(0.0, 0.0))
    assert result.trial_state == TrialState.RUNNING


def test_world_coordinate_input_is_converted_to_task_space() -> None:
    controller = make_trial_controller()
    controller.start_trial(time=0.0, trial_id=1)
    result = controller.update(
        ExperimentInputSample(
            time=0.0,
            pinch_center_world=[0.0, 0.0, 0.0],
            pinch_distance=0.01,
            tracker_valid=True,
            coordinate_space="world",
        )
    )
    assert result.frame_output.pinch_center_task == Vec3(0.0, 0.0, 0.0)
