"""Core state update logic for the constrained block interaction engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from config import EngineConfig
from data_models import (
    BlockMotionState,
    BlockedInfo,
    BlockState,
    Box3D,
    ClampResult,
    ContactState,
    DetachCounts,
    DetachState,
    FeedbackState,
    FrameInput,
    FrameOutput,
    PinchState,
    StopReason,
    Surface,
    TrackRegion,
    TrackState,
    Vec3,
)
from geometry import clamp_segment_to_track, point_in_box, point_in_track
from haptic_feedback import build_haptic_output
from pinch_model import update_pinch_state


@dataclass
class _ControllerState:
    """Mutable internal controller state."""

    block_center: Vec3
    previous_pinch_center: Vec3 | None = None
    previous_contact_state: ContactState = ContactState.OUTSIDE_BLOCK
    previous_motion_state: BlockMotionState = BlockMotionState.FREE_VISIBLE
    previous_stop_reason: StopReason = StopReason.NONE
    stable_pinch_state: PinchState = PinchState.PINCH_UNKNOWN
    detach_counts: DetachCounts = field(default_factory=DetachCounts)
    previous_tracker_valid: bool = True
    boundary_lock_active: bool = False
    boundary_lock_surface: Surface | None = None
    boundary_lock_position: Vec3 | None = None
    boundary_lock_entry_pinch_center: Vec3 | None = None
    boundary_lock_escape_direction: Vec3 | None = None
    boundary_lock_blocked_info: BlockedInfo | None = None
    boundary_lock_escape_progress: float = 0.0


class BlockController:
    """Update block state from frame inputs under track and pinch constraints."""

    def __init__(
        self,
        config: EngineConfig,
        track_region: TrackRegion,
        initial_block_center: Vec3,
    ) -> None:
        self.config = config
        self.track_region = track_region
        if not point_in_track(initial_block_center, track_region, epsilon=config.track_epsilon):
            raise ValueError("initial_block_center must start inside the track region.")

        self._state = _ControllerState(block_center=initial_block_center)

    @property
    def block_center(self) -> Vec3:
        """Return the current real block center."""

        return self._state.block_center

    def update(self, frame_input: FrameInput) -> FrameOutput:
        """Advance the controller by one frame."""

        previous_contact_state = self._state.previous_contact_state
        effective_tracking_valid = (
            frame_input.tracker_valid and frame_input.pinch_center_task is not None
        )

        if not effective_tracking_valid:
            frame_output = self._handle_tracking_invalid(frame_input)
        else:
            assert frame_input.pinch_center_task is not None
            current_contact_state = self._compute_contact_state(frame_input.pinch_center_task)
            current_pinch_state = update_pinch_state(
                frame_input.pinch_distance,
                self._state.stable_pinch_state,
                self.config.pinch_grab_threshold,
                self.config.pinch_release_threshold,
                data_valid=True,
            )

            recovery_frame = not self._state.previous_tracker_valid
            if recovery_frame:
                frame_output = self._handle_recovery_frame(
                    frame_input,
                    current_contact_state,
                    current_pinch_state,
                )
            elif (
                previous_contact_state == ContactState.INSIDE_BLOCK
                and current_contact_state == ContactState.OUTSIDE_BLOCK
            ):
                frame_output = self._handle_detach_frame(frame_input, current_pinch_state)
            elif current_contact_state == ContactState.OUTSIDE_BLOCK:
                frame_output = self._handle_outside_frame(frame_input, current_pinch_state)
            elif previous_contact_state == ContactState.OUTSIDE_BLOCK:
                frame_output = self._handle_contact_enter_frame(frame_input, current_pinch_state)
            else:
                frame_output = self._handle_inside_frame(frame_input, current_pinch_state)

        events, haptic_feedback = build_haptic_output(
            frame_output,
            previous_contact_state,
            self.config,
        )
        frame_output.events = events
        frame_output.haptic_feedback = haptic_feedback
        self._commit_frame(frame_input, frame_output)
        return frame_output

    def _handle_tracking_invalid(self, frame_input: FrameInput) -> FrameOutput:
        self._clear_boundary_lock()
        contact_state = self._state.previous_contact_state
        motion_state = (
            BlockMotionState.FREE_VISIBLE
            if contact_state == ContactState.OUTSIDE_BLOCK
            else BlockMotionState.CONTACT_HIDDEN
        )
        return self._build_output(
            frame_input=frame_input,
            contact_state=contact_state,
            pinch_state=PinchState.PINCH_UNKNOWN,
            block_center=self._state.block_center,
            motion_state=motion_state,
            visible=(contact_state == ContactState.OUTSIDE_BLOCK),
            feedback_state=FeedbackState(
                tracking_valid=False,
                recovery_frame=False,
                stop_reason=StopReason.TRACKING_INVALID,
            ),
        )

    def _handle_recovery_frame(
        self,
        frame_input: FrameInput,
        contact_state: ContactState,
        pinch_state: PinchState,
    ) -> FrameOutput:
        motion_state = (
            BlockMotionState.CONTACT_HIDDEN
            if contact_state == ContactState.INSIDE_BLOCK
            else BlockMotionState.FREE_VISIBLE
        )
        return self._build_output(
            frame_input=frame_input,
            contact_state=contact_state,
            pinch_state=pinch_state,
            block_center=self._state.block_center,
            motion_state=motion_state,
            visible=(contact_state == ContactState.OUTSIDE_BLOCK),
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=True,
            ),
        )

    def _handle_detach_frame(
        self,
        frame_input: FrameInput,
        pinch_state: PinchState,
    ) -> FrameOutput:
        self._clear_boundary_lock()
        detach_state = self._classify_detach_state()
        detach_counts = self._increment_detach_counts(detach_state)
        return self._build_output(
            frame_input=frame_input,
            contact_state=ContactState.OUTSIDE_BLOCK,
            pinch_state=pinch_state,
            block_center=self._state.block_center,
            motion_state=BlockMotionState.FREE_VISIBLE,
            visible=True,
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
                detach_state=detach_state,
            ),
            detach_counts=detach_counts,
        )

    def _handle_outside_frame(
        self,
        frame_input: FrameInput,
        pinch_state: PinchState,
    ) -> FrameOutput:
        self._clear_boundary_lock()
        return self._build_output(
            frame_input=frame_input,
            contact_state=ContactState.OUTSIDE_BLOCK,
            pinch_state=pinch_state,
            block_center=self._state.block_center,
            motion_state=BlockMotionState.FREE_VISIBLE,
            visible=True,
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
            ),
        )

    def _handle_contact_enter_frame(
        self,
        frame_input: FrameInput,
        pinch_state: PinchState,
    ) -> FrameOutput:
        return self._build_output(
            frame_input=frame_input,
            contact_state=ContactState.INSIDE_BLOCK,
            pinch_state=pinch_state,
            block_center=self._state.block_center,
            motion_state=BlockMotionState.CONTACT_HIDDEN,
            visible=False,
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
            ),
        )

    def _handle_inside_frame(
        self,
        frame_input: FrameInput,
        pinch_state: PinchState,
    ) -> FrameOutput:
        current_pinch_center = frame_input.pinch_center_task
        previous_pinch_center = self._state.previous_pinch_center
        if current_pinch_center is None or previous_pinch_center is None:
            if self._state.boundary_lock_active:
                return self._handle_boundary_lock_frame(
                    frame_input,
                    pinch_state,
                    hand_delta=Vec3.zero(),
                )
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=self._state.block_center,
                motion_state=BlockMotionState.CONTACT_HIDDEN,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                ),
            )

        hand_delta = current_pinch_center - previous_pinch_center
        if self._state.boundary_lock_active:
            return self._handle_boundary_lock_frame(
                frame_input,
                pinch_state,
                hand_delta=hand_delta,
            )
        if pinch_state == PinchState.PINCH_UNKNOWN:
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=self._state.block_center,
                motion_state=BlockMotionState.CONTACT_HIDDEN,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    hand_delta=hand_delta,
                ),
            )

        if pinch_state == PinchState.PINCH_INSUFFICIENT:
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=self._state.block_center,
                motion_state=BlockMotionState.GRABBED_PINCH_INSUFFICIENT,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    stop_reason=StopReason.PINCH_INSUFFICIENT,
                    hand_delta=hand_delta,
                ),
            )

        if hand_delta.norm() > self.config.max_hand_delta_per_frame:
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=self._state.block_center,
                motion_state=BlockMotionState.STOPPED_BY_LARGE_DELTA,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    stop_reason=StopReason.LARGE_DELTA,
                    track_state=TrackState.HAND_DELTA_TOO_LARGE,
                    hand_delta=hand_delta,
                ),
            )

        candidate_center = self._state.block_center + hand_delta
        if point_in_track(candidate_center, self.track_region, epsilon=self.config.track_epsilon):
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=candidate_center,
                motion_state=BlockMotionState.GRABBED_MOVING,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    hand_delta=hand_delta,
                    candidate_block_center=candidate_center,
                ),
            )

        clamp_result = self._clamp_candidate(candidate_center)
        moved_distance = self._state.block_center.distance_to(clamp_result.clamped_point)
        next_block_center = (
            clamp_result.clamped_point
            if moved_distance >= self.config.min_block_move_distance
            else self._state.block_center
        )
        blocked_info = clamp_result.blocked_info
        track_state = self._surface_to_track_state(
            blocked_info.primary_blocked_surface if blocked_info is not None else None
        )
        if self._should_enter_boundary_lock(blocked_info):
            self._enter_boundary_lock(
                next_block_center,
                current_pinch_center,
                blocked_info,
            )
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=next_block_center,
                motion_state=BlockMotionState.GRABBED_BLOCKED,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    stop_reason=StopReason.TRACK_BLOCKED,
                    track_state=track_state,
                    hand_delta=hand_delta,
                    candidate_block_center=candidate_center,
                    blocked_info=blocked_info,
                    boundary_lock_active=True,
                    boundary_lock_surface=blocked_info.primary_blocked_surface,
                    boundary_lock_escape_progress=0.0,
                    boundary_lock_unlock_delta_m=self.config.boundary_lock_unlock_delta_m,
                    boundary_lock_event="lock_enter",
                ),
            )
        return self._build_output(
            frame_input=frame_input,
            contact_state=ContactState.INSIDE_BLOCK,
            pinch_state=pinch_state,
            block_center=next_block_center,
            motion_state=BlockMotionState.GRABBED_BLOCKED,
            visible=False,
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
                stop_reason=StopReason.TRACK_BLOCKED,
                track_state=track_state,
                hand_delta=hand_delta,
                candidate_block_center=candidate_center,
                blocked_info=blocked_info,
            ),
        )

    def _handle_boundary_lock_frame(
        self,
        frame_input: FrameInput,
        pinch_state: PinchState,
        *,
        hand_delta: Vec3,
    ) -> FrameOutput:
        current_pinch_center = frame_input.pinch_center_task
        block_center = self._state.boundary_lock_position or self._state.block_center
        blocked_info = self._state.boundary_lock_blocked_info
        surface = self._state.boundary_lock_surface
        progress = self._boundary_lock_escape_progress(current_pinch_center)
        self._state.boundary_lock_escape_progress = progress
        if (
            pinch_state == PinchState.PINCH_VALID
            and progress >= self.config.boundary_lock_unlock_delta_m
        ):
            self._clear_boundary_lock()
            self._state.previous_pinch_center = current_pinch_center
            return self._build_output(
                frame_input=frame_input,
                contact_state=ContactState.INSIDE_BLOCK,
                pinch_state=pinch_state,
                block_center=block_center,
                motion_state=BlockMotionState.CONTACT_HIDDEN,
                visible=False,
                feedback_state=FeedbackState(
                    tracking_valid=True,
                    recovery_frame=False,
                    hand_delta=hand_delta,
                    blocked_info=blocked_info,
                    boundary_lock_active=False,
                    boundary_lock_surface=surface,
                    boundary_lock_escape_progress=progress,
                    boundary_lock_unlock_delta_m=self.config.boundary_lock_unlock_delta_m,
                    boundary_lock_event="unlock",
                ),
            )

        return self._build_output(
            frame_input=frame_input,
            contact_state=ContactState.INSIDE_BLOCK,
            pinch_state=pinch_state,
            block_center=block_center,
            motion_state=BlockMotionState.GRABBED_BLOCKED,
            visible=False,
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
                stop_reason=StopReason.TRACK_BLOCKED,
                track_state=self._surface_to_track_state(surface),
                hand_delta=hand_delta,
                blocked_info=blocked_info,
                boundary_lock_active=True,
                boundary_lock_surface=surface,
                boundary_lock_escape_progress=progress,
                boundary_lock_unlock_delta_m=self.config.boundary_lock_unlock_delta_m,
                boundary_lock_event="locked",
            ),
        )

    def _build_output(
        self,
        *,
        frame_input: FrameInput,
        contact_state: ContactState,
        pinch_state: PinchState,
        block_center: Vec3,
        motion_state: BlockMotionState,
        visible: bool,
        feedback_state: FeedbackState,
        detach_counts: DetachCounts | None = None,
    ) -> FrameOutput:
        return FrameOutput(
            time=frame_input.time,
            pinch_center_task=frame_input.pinch_center_task,
            pinch_distance=frame_input.pinch_distance,
            block_state=BlockState(
                center=block_center,
                size=self.config.block_size,
                visible=visible,
                motion_state=motion_state,
            ),
            contact_state=contact_state,
            pinch_state=pinch_state,
            feedback_state=feedback_state,
            detach_counts=detach_counts or self._state.detach_counts,
        )

    def _compute_contact_state(self, pinch_center: Vec3) -> ContactState:
        block_box = self._current_block_box()
        epsilon = self.config.track_epsilon
        if self._state.boundary_lock_active:
            epsilon += max(0.0, self.config.boundary_lock_contact_tolerance_m)
        inside = point_in_box(pinch_center, block_box, epsilon=epsilon)
        return ContactState.INSIDE_BLOCK if inside else ContactState.OUTSIDE_BLOCK

    def _current_block_box(self) -> Box3D:
        return Box3D(center=self._state.block_center, size=self.config.block_size)

    def _clamp_candidate(self, candidate_center: Vec3) -> ClampResult:
        return clamp_segment_to_track(
            self._state.block_center,
            candidate_center,
            self.track_region,
            epsilon=self.config.track_epsilon,
            iterations=self.config.binary_search_iterations,
            surface_threshold=self.config.blocked_feedback_threshold,
        )

    def _should_enter_boundary_lock(self, blocked_info: BlockedInfo | None) -> bool:
        return bool(
            self.config.boundary_lock_enabled
            and self.config.boundary_lock_surface_mode == "primary"
            and blocked_info is not None
            and blocked_info.primary_blocked_surface is not None
        )

    def _enter_boundary_lock(
        self,
        block_center: Vec3,
        pinch_center: Vec3,
        blocked_info: BlockedInfo,
    ) -> None:
        surface = blocked_info.primary_blocked_surface
        self._state.boundary_lock_active = True
        self._state.boundary_lock_surface = surface
        self._state.boundary_lock_position = block_center
        self._state.boundary_lock_entry_pinch_center = pinch_center
        self._state.boundary_lock_escape_direction = self._escape_direction_for_surface(surface)
        self._state.boundary_lock_blocked_info = blocked_info
        self._state.boundary_lock_escape_progress = 0.0

    def _clear_boundary_lock(self) -> None:
        self._state.boundary_lock_active = False
        self._state.boundary_lock_surface = None
        self._state.boundary_lock_position = None
        self._state.boundary_lock_entry_pinch_center = None
        self._state.boundary_lock_escape_direction = None
        self._state.boundary_lock_blocked_info = None
        self._state.boundary_lock_escape_progress = 0.0

    def _boundary_lock_escape_progress(self, current_pinch_center: Vec3 | None) -> float:
        entry = self._state.boundary_lock_entry_pinch_center
        direction = self._state.boundary_lock_escape_direction
        if current_pinch_center is None or entry is None or direction is None:
            return self._state.boundary_lock_escape_progress
        return _dot(current_pinch_center - entry, direction)

    def _escape_direction_for_surface(self, surface: Surface | None) -> Vec3 | None:
        if surface == Surface.X_POS:
            return Vec3(-1.0, 0.0, 0.0)
        if surface == Surface.X_NEG:
            return Vec3(1.0, 0.0, 0.0)
        if surface == Surface.Y_POS:
            return Vec3(0.0, -1.0, 0.0)
        if surface == Surface.Y_NEG:
            return Vec3(0.0, 1.0, 0.0)
        if surface == Surface.Z_POS:
            return Vec3(0.0, 0.0, -1.0)
        if surface == Surface.Z_NEG:
            return Vec3(0.0, 0.0, 1.0)
        return None

    def _classify_detach_state(self) -> DetachState:
        if self._state.previous_stop_reason == StopReason.PINCH_INSUFFICIENT:
            return DetachState.ACTIVE_RELEASE
        if self._state.previous_stop_reason == StopReason.TRACK_BLOCKED:
            return DetachState.FORCED_DETACH
        return DetachState.UNEXPECTED_DETACH

    def _increment_detach_counts(self, detach_state: DetachState) -> DetachCounts:
        counts = self._state.detach_counts
        active_release_count = counts.active_release_count
        forced_detach_count = counts.forced_detach_count
        unexpected_detach_count = counts.unexpected_detach_count
        total_detach_count = counts.total_detach_count

        if detach_state == DetachState.ACTIVE_RELEASE:
            active_release_count += 1
            total_detach_count += 1
        elif detach_state == DetachState.FORCED_DETACH:
            forced_detach_count += 1
            total_detach_count += 1
        elif detach_state == DetachState.UNEXPECTED_DETACH:
            unexpected_detach_count += 1
            total_detach_count += 1

        return DetachCounts(
            active_release_count=active_release_count,
            forced_detach_count=forced_detach_count,
            unexpected_detach_count=unexpected_detach_count,
            total_detach_count=total_detach_count,
        )

    def _surface_to_track_state(self, surface: Surface | None) -> TrackState:
        if surface == Surface.X_POS:
            return TrackState.BLOCKED_X_POS
        if surface == Surface.X_NEG:
            return TrackState.BLOCKED_X_NEG
        if surface == Surface.Y_POS:
            return TrackState.BLOCKED_Y_POS
        if surface == Surface.Y_NEG:
            return TrackState.BLOCKED_Y_NEG
        if surface == Surface.Z_POS:
            return TrackState.BLOCKED_Z_POS
        if surface == Surface.Z_NEG:
            return TrackState.BLOCKED_Z_NEG
        return TrackState.INSIDE_TRACK

    def _commit_frame(self, frame_input: FrameInput, frame_output: FrameOutput) -> None:
        self._state.block_center = frame_output.block_state.center
        self._state.detach_counts = frame_output.detach_counts

        if frame_output.feedback_state.tracking_valid:
            self._state.previous_contact_state = frame_output.contact_state
            self._state.previous_motion_state = frame_output.block_state.motion_state
            self._state.previous_stop_reason = frame_output.feedback_state.stop_reason
            self._state.previous_tracker_valid = True

            if frame_input.pinch_center_task is not None:
                self._state.previous_pinch_center = frame_input.pinch_center_task

            if frame_output.pinch_state != PinchState.PINCH_UNKNOWN:
                self._state.stable_pinch_state = frame_output.pinch_state
        else:
            self._state.previous_tracker_valid = False


def _dot(left: Vec3, right: Vec3) -> float:
    return left.x * right.x + left.y * right.y + left.z * right.z
