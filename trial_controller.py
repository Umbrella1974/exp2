"""Stage 2 trial lifecycle orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from block_controller import BlockController
from config import EngineConfig
from data_models import (
    BlockMotionState,
    DetachState,
    FrameInput,
    FrameOutput,
    HapticEventType,
    HapticFeedbackState,
    PinchState,
    StopReason,
    Vec3,
)
from task_coordinate_system import TaskCoordinateSystem


class TrialState(Enum):
    """Lifecycle state for a trial.

    TRACKING_INVALID is reserved for future use. Stage 2/3 keeps tracking loss
    inside RUNNING and records tracking_invalid/tracking_recovered edge events.
    """

    WAITING = auto()
    PROMPT = auto()
    RUNNING = auto()
    ENDED_BY_SUBJECT = auto()
    FAILED_TIMEOUT = auto()
    FAILED_TOO_MANY_DETACHES = auto()
    TRACKING_INVALID = auto()


@dataclass(frozen=True)
class ExperimentInputSample:
    """One experiment input sample before conversion to task-space FrameInput."""

    time: float
    pinch_distance: float | None
    tracker_valid: bool
    pinch_center_world: object | None = None
    pinch_center_task: object | None = None
    coordinate_space: str = "task"
    subject_end: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EventRecord:
    """Serializable event record emitted by the trial layer."""

    time: float
    trial_id: int | str
    event_type: str
    state: TrialState
    value: object | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TrialFrameResult:
    """Per-frame result returned by TrialController.update()."""

    trial_id: int | str
    trial_state: TrialState
    frame_output: FrameOutput
    haptic_feedback_state: HapticFeedbackState
    events: tuple[EventRecord, ...]
    time_since_prompt: float
    failure_reason: str | None = None


class TrialController:
    """Manage trial lifecycle around a BlockController instance."""

    def __init__(
        self,
        block_controller_factory: Callable[[], BlockController],
        task_coordinate_system: TaskCoordinateSystem | None,
        config: EngineConfig,
        recorder: object | None = None,
    ) -> None:
        self.block_controller_factory = block_controller_factory
        self.task_coordinate_system = task_coordinate_system
        self.config = config
        self.recorder = recorder
        self.trial_state = TrialState.WAITING
        self.trial_id: int | str | None = None
        self.prompt_on_time: float | None = None
        self.block_controller: BlockController | None = None
        self.event_history: list[EventRecord] = []
        self._previous_tracking_valid = True
        self._previous_pinch_state: PinchState | None = None
        self._previous_slip_active = False
        self._previous_blocked_force_active = False
        self._previous_blocked_active = False
        self._previous_moving_active = False

    def start_trial(self, time: float, trial_id: int | str) -> None:
        """Start a new trial and create a fresh BlockController."""

        self.block_controller = self.block_controller_factory()
        self.trial_id = trial_id
        self.prompt_on_time = time
        self.trial_state = TrialState.RUNNING
        self._previous_tracking_valid = True
        self._previous_pinch_state = None
        self._previous_slip_active = False
        self._previous_blocked_force_active = False
        self._previous_blocked_active = False
        self._previous_moving_active = False

        events = (
            self._make_event(time, "trial_started"),
            self._make_event(time, "prompt_on"),
        )
        self._record_events(events)

    def update(self, sample: ExperimentInputSample) -> TrialFrameResult:
        """Process one sample through the trial and block controllers."""

        if self.trial_state == TrialState.WAITING or self.block_controller is None:
            raise RuntimeError("Trial must be started before update().")
        if self.trial_state not in (TrialState.RUNNING, TrialState.TRACKING_INVALID):
            raise RuntimeError("Cannot update a trial after it has ended.")

        pinch_center_task = self._pinch_center_task(sample)
        effective_tracking_valid = sample.tracker_valid and pinch_center_task is not None

        frame_input = FrameInput(
            time=sample.time,
            pinch_center_task=pinch_center_task,
            pinch_distance=sample.pinch_distance,
            tracker_valid=effective_tracking_valid,
            subject_end=sample.subject_end,
        )
        frame_output = self.block_controller.update(frame_input)
        events = list(self._build_frame_events(sample, frame_output, effective_tracking_valid))

        next_state = self.trial_state
        failure_reason = None
        time_since_prompt = self._time_since_prompt(sample.time)

        if sample.subject_end:
            next_state = TrialState.ENDED_BY_SUBJECT
            events.append(self._make_event(sample.time, "subject_end", state=next_state))
        elif time_since_prompt > self.config.trial_timeout_seconds:
            next_state = TrialState.FAILED_TIMEOUT
            failure_reason = "timeout"
            events.append(self._make_event(sample.time, "timeout", state=next_state))
        elif frame_output.detach_counts.total_detach_count > self.config.max_detach_count:
            next_state = TrialState.FAILED_TOO_MANY_DETACHES
            failure_reason = "too_many_detaches"
            events.append(
                self._make_event(
                    sample.time,
                    "too_many_detaches",
                    state=next_state,
                    value=frame_output.detach_counts.total_detach_count,
                )
            )

        self.trial_state = next_state
        result = TrialFrameResult(
            trial_id=self._require_trial_id(),
            trial_state=next_state,
            frame_output=frame_output,
            haptic_feedback_state=frame_output.haptic_feedback,
            events=tuple(events),
            time_since_prompt=time_since_prompt,
            failure_reason=failure_reason,
        )
        self._record_events(result.events)
        self._record_frame(result)
        self._commit_previous_state(frame_output, effective_tracking_valid)
        return result

    def end_by_subject(self, time: float) -> None:
        """End the trial without consuming a new input sample."""

        if self.trial_state == TrialState.WAITING:
            raise RuntimeError("No active trial to end.")
        self.trial_state = TrialState.ENDED_BY_SUBJECT
        self._record_events((self._make_event(time, "subject_end", state=self.trial_state),))

    def reset(self, time: float | None = None) -> None:
        """Reset the trial lifecycle without mutating any old BlockController."""

        if self.trial_id is not None and time is not None:
            self._record_events((self._make_event(time, "trial_reset"),))
        self.trial_state = TrialState.WAITING
        self.trial_id = None
        self.prompt_on_time = None
        self.block_controller = None
        self._previous_tracking_valid = True
        self._previous_pinch_state = None
        self._previous_slip_active = False
        self._previous_blocked_force_active = False
        self._previous_blocked_active = False
        self._previous_moving_active = False

    def _build_frame_events(
        self,
        sample: ExperimentInputSample,
        frame_output: FrameOutput,
        effective_tracking_valid: bool,
    ) -> tuple[EventRecord, ...]:
        events: list[EventRecord] = []
        time = sample.time

        if self._previous_tracking_valid and not effective_tracking_valid:
            events.append(self._make_event(time, "tracking_invalid"))
        elif not self._previous_tracking_valid and effective_tracking_valid:
            events.append(self._make_event(time, "tracking_recovered"))

        for haptic_event in frame_output.events:
            if haptic_event.event_type == HapticEventType.CONTACT_ENTER:
                events.append(self._make_event(time, "contact_enter"))
            elif haptic_event.event_type == HapticEventType.CONTACT_EXIT:
                events.append(
                    self._make_event(
                        time,
                        "contact_exit",
                        details={"detach_state": haptic_event.detach_state.name},
                    )
                )
                detach_event_type = self._detach_event_type(haptic_event.detach_state)
                if detach_event_type is not None:
                    events.append(self._make_event(time, detach_event_type))

        if frame_output.pinch_state != self._previous_pinch_state:
            if frame_output.pinch_state == PinchState.PINCH_VALID:
                events.append(self._make_event(time, "pinch_valid"))
            elif frame_output.pinch_state == PinchState.PINCH_INSUFFICIENT:
                events.append(self._make_event(time, "pinch_insufficient"))

        moving_active = frame_output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
        if moving_active and not self._previous_moving_active:
            events.append(self._make_event(time, "block_moved"))

        blocked_active = frame_output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
        if blocked_active and not self._previous_blocked_active:
            events.append(self._make_event(time, "block_blocked_start"))
        elif self._previous_blocked_active and not blocked_active:
            events.append(self._make_event(time, "block_blocked_end"))

        if frame_output.feedback_state.stop_reason == StopReason.LARGE_DELTA:
            events.append(self._make_event(time, "large_delta"))

        slip_active = frame_output.haptic_feedback.slip_active
        if slip_active and not self._previous_slip_active:
            events.append(
                self._make_event(
                    time,
                    "slip_start",
                    details={
                        "slip_reason": (
                            frame_output.haptic_feedback.slip_reason.name
                            if frame_output.haptic_feedback.slip_reason is not None
                            else ""
                        )
                    },
                )
            )
        elif self._previous_slip_active and not slip_active:
            events.append(self._make_event(time, "slip_end"))

        blocked_force_active = frame_output.haptic_feedback.blocked_force_active
        if blocked_force_active and not self._previous_blocked_force_active:
            events.append(self._make_event(time, "blocked_force_start"))
        elif self._previous_blocked_force_active and not blocked_force_active:
            events.append(self._make_event(time, "blocked_force_end"))

        return tuple(events)

    def _commit_previous_state(
        self,
        frame_output: FrameOutput,
        effective_tracking_valid: bool,
    ) -> None:
        self._previous_tracking_valid = effective_tracking_valid
        self._previous_pinch_state = frame_output.pinch_state
        self._previous_slip_active = frame_output.haptic_feedback.slip_active
        self._previous_blocked_force_active = frame_output.haptic_feedback.blocked_force_active
        self._previous_blocked_active = frame_output.feedback_state.stop_reason == StopReason.TRACK_BLOCKED
        self._previous_moving_active = (
            frame_output.block_state.motion_state == BlockMotionState.GRABBED_MOVING
        )

    def _pinch_center_task(self, sample: ExperimentInputSample) -> Vec3 | None:
        if sample.coordinate_space == "world":
            if sample.pinch_center_world is None:
                return None
            if self.task_coordinate_system is None:
                raise ValueError("task_coordinate_system is required for world coordinates.")
            return _to_vec3(self.task_coordinate_system.world_to_task(sample.pinch_center_world))

        if sample.coordinate_space != "task":
            raise ValueError('coordinate_space must be "task" or "world".')

        if sample.pinch_center_task is None:
            return None
        return _to_vec3(sample.pinch_center_task)

    def _time_since_prompt(self, time: float) -> float:
        if self.prompt_on_time is None:
            return 0.0
        return time - self.prompt_on_time

    def _make_event(
        self,
        time: float,
        event_type: str,
        *,
        state: TrialState | None = None,
        value: object | None = None,
        details: dict[str, object] | None = None,
    ) -> EventRecord:
        return EventRecord(
            time=time,
            trial_id=self._require_trial_id(),
            event_type=event_type,
            state=state or self.trial_state,
            value=value,
            details=details or {},
        )

    def _record_events(self, events: tuple[EventRecord, ...]) -> None:
        self.event_history.extend(events)
        if self.recorder is not None and hasattr(self.recorder, "log_events"):
            self.recorder.log_events(events)

    def _record_frame(self, result: TrialFrameResult) -> None:
        if self.recorder is not None and hasattr(self.recorder, "log_frame"):
            self.recorder.log_frame(result)

    def _require_trial_id(self) -> int | str:
        if self.trial_id is None:
            raise RuntimeError("trial_id is not set.")
        return self.trial_id

    def _detach_event_type(self, detach_state: DetachState) -> str | None:
        if detach_state == DetachState.ACTIVE_RELEASE:
            return "active_release"
        if detach_state == DetachState.FORCED_DETACH:
            return "forced_detach"
        if detach_state == DetachState.UNEXPECTED_DETACH:
            return "unexpected_detach"
        return None


def _to_vec3(value: object) -> Vec3:
    if isinstance(value, Vec3):
        return value
    try:
        x, y, z = value  # type: ignore[misc]
    except TypeError as exc:
        raise ValueError("Expected a 3D point.") from exc
    return Vec3(float(x), float(y), float(z))
