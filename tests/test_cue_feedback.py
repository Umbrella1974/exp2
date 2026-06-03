"""Tests for non-hardware cue command generation and sinks."""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cue_config import CueConfig
from cue_feedback import (
    CueCommand,
    CueCommandResult,
    CueRuntime,
    CueSinkConfig,
    LoggingCueSink,
    NullCueSink,
    write_cue_log_csv,
)
from data_models import (
    BlockMotionState,
    BlockState,
    ContactState,
    DetachState,
    FeedbackState,
    HapticFeedbackState,
    SlipReason,
    StopReason,
    Surface,
    TrackState,
    Vec3,
)


@dataclass(frozen=True)
class _Event:
    event_type: str
    details: dict[str, Any]


@dataclass(frozen=True)
class _Output:
    contact_state: ContactState
    block_state: BlockState
    feedback_state: FeedbackState


@dataclass(frozen=True)
class _Result:
    trial_id: str
    frame_output: _Output
    haptic_feedback_state: HapticFeedbackState
    events: tuple[_Event, ...]
    time_since_prompt: float


@dataclass(frozen=True)
class _Snapshot:
    tracker_valid: bool = True
    hand_valid: bool = True
    pinch_valid: bool = True
    pinch_center_task: list[float] | None = None


@dataclass(frozen=True)
class _Sample:
    time: float


def test_cue_command_serialization_and_logging_sink() -> None:
    command = _command()
    result = CueCommandResult(command)

    LoggingCueSink().submit(result)
    payload = result.to_dict()

    assert command.to_dict()["cue_type"] == "contact_enter"
    assert payload["cue_modality"] == "logging"
    assert payload["success"] is True
    assert payload["display_status"] == "not_applicable"


def test_null_sink_has_no_hardware_dependency() -> None:
    result = CueCommandResult(_command())

    NullCueSink().submit(result)

    assert result.to_dict()["cue_modality"] == "none"
    assert result.to_dict()["is_hardware_haptic"] is False


def test_contact_enter_exit_and_slip_generate_edge_only_cues() -> None:
    runtime = _runtime()

    enter = runtime.process_frame(
        frame_index=1,
        source_frame_id=101,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    repeated = runtime.process_frame(
        frame_index=2,
        source_frame_id=102,
        sample=_Sample(1.1),
        trial_result=_result(),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    slip = runtime.process_frame(
        frame_index=3,
        source_frame_id=103,
        sample=_Sample(1.2),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.PINCH_INSUFFICIENT,
            events=(_Event("slip_start", {"slip_reason": "PINCH_INSUFFICIENT"}),),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.1, 0.0, 0.0]),
    )
    exit_commands = runtime.process_frame(
        frame_index=4,
        source_frame_id=104,
        sample=_Sample(1.3),
        trial_result=_result(
            contact_state=ContactState.OUTSIDE_BLOCK,
            events=(_Event("contact_exit", {"detach_state": "ACTIVE_RELEASE"}),),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.3, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert [command.cue_type for command in enter] == ["contact_enter"]
    assert repeated == ()
    assert [command.cue_type for command in slip] == ["slip_pinch_insufficient"]
    assert [command.cue_type for command in exit_commands] == ["contact_exit"]
    assert exit_commands[0].detach_state == "ACTIVE_RELEASE"


def test_blocked_directional_prefers_corrective_direction_over_generic_slip() -> None:
    runtime = _runtime()

    commands = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.TRACK_BLOCKED,
            blocked_force_active=True,
            primary_surface=Surface.X_POS,
            track_state=TrackState.BLOCKED_X_POS,
            events=(
                _Event("slip_start", {"slip_reason": "TRACK_BLOCKED"}),
                _Event("blocked_force_start", {}),
            ),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.2, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert [command.cue_type for command in commands] == ["blocked_directional"]
    assert commands[0].primary_blocked_surface == "X_POS"
    assert commands[0].direction == "X_NEG"
    assert commands[0].message == "MOVE X_NEG"


def test_directional_disabled_falls_back_to_generic_track_blocked_slip() -> None:
    runtime = _runtime(
        CueConfig(enable_blocked_directional_cue=False)
    )

    commands = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.TRACK_BLOCKED,
            blocked_force_active=True,
            primary_surface=Surface.Y_NEG,
            track_state=TrackState.BLOCKED_Y_NEG,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert [command.cue_type for command in commands] == ["slip_track_blocked"]
    assert runtime.summary()["suppressed_cue_type_counts"]["blocked_directional"] == 1


def test_invalid_and_terminal_frames_do_not_generate_cues() -> None:
    runtime = _runtime()

    invalid = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(tracker_valid=False, pinch_center_task=[0.0, 0.0, 0.0]),
    )
    terminal = runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
        terminal_frame=True,
    )
    runtime.end_trial()

    assert invalid == ()
    assert terminal == ()
    assert runtime.summary()["cue_count"] == 0


def test_none_sink_generates_no_candidates_or_suppressed_counts() -> None:
    runtime = CueRuntime(
        trial_id="trial_test",
        sink_config=CueSinkConfig(cue_sink="none", mode="live", is_live_cue_timing=True),
    )

    commands = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert commands == ()
    assert runtime.summary()["cue_count"] == 0
    assert runtime.summary()["suppressed_cue_count"] == 0


def test_blocked_surface_change_emits_state_signature_changed() -> None:
    runtime = _runtime()
    first = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(
            blocked_force_active=True,
            primary_surface=Surface.X_POS,
            track_state=TrackState.BLOCKED_X_POS,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    second = runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(
            blocked_force_active=True,
            primary_surface=Surface.Y_POS,
            track_state=TrackState.BLOCKED_Y_POS,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert first[0].trigger_reason == "edge_start"
    assert second[0].trigger_reason == "state_signature_changed"
    assert second[0].direction == "Y_NEG"


def test_rate_limit_counts_suppressed_candidate_without_log_row() -> None:
    runtime = _runtime(CueConfig(min_cue_interval_ms=1000.0))

    runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),), trial_time=0.0),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(
            contact_state=ContactState.OUTSIDE_BLOCK,
            events=(_Event("contact_exit", {}),),
            trial_time=0.1,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.3, 0.0, 0.0]),
    )
    runtime.process_frame(
        frame_index=3,
        source_frame_id=3,
        sample=_Sample(1.2),
        trial_result=_result(events=(_Event("contact_enter", {}),), trial_time=0.2),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert runtime.summary()["cue_count"] == 2
    assert runtime.summary()["suppressed_cue_type_counts"]["contact_enter"] == 1


def test_write_cue_log_supports_header_only_empty_log(tmp_path: Path) -> None:
    path = write_cue_log_csv(tmp_path / "cue_log.csv", [])

    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == []


def test_gui_priority_logs_lower_cue_but_displays_only_highest() -> None:
    runtime = CueRuntime(
        trial_id="trial_test",
        sink_config=CueSinkConfig(cue_sink="gui_text", mode="live", is_live_cue_timing=True),
    )

    commands = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.PINCH_INSUFFICIENT,
            events=(
                _Event("contact_enter", {}),
                _Event("slip_start", {"slip_reason": "PINCH_INSUFFICIENT"}),
            ),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    records = runtime.records_snapshot()
    active = runtime.gui_cue_store.get_active()
    runtime.end_trial()

    assert [command.cue_type for command in commands] == [
        "contact_enter",
        "slip_pinch_insufficient",
    ]
    assert records[0]["display_status"] == "not_displayed_lower_priority"
    assert active is not None
    assert active.cue_type == "slip_pinch_insufficient"


def test_gui_close_marks_pending_and_future_cue_falls_back_to_console() -> None:
    output: list[str] = []
    runtime = CueRuntime(
        trial_id="trial_test",
        sink_config=CueSinkConfig(cue_sink="gui_text", mode="live", is_live_cue_timing=True),
        console_output_fn=output.append,
    )
    runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )

    runtime.handle_gui_closed()
    runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(
            contact_state=ContactState.OUTSIDE_BLOCK,
            events=(_Event("contact_exit", {}),),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.3, 0.0, 0.0]),
    )
    deadline = time.monotonic() + 1.0
    while not output and time.monotonic() < deadline:
        time.sleep(0.001)
    runtime.end_trial()
    records = runtime.records_snapshot()

    assert records[0]["display_status"] == "not_displayed"
    assert records[0]["not_displayed_reason"] == "gui_closed_before_render"
    assert records[1]["cue_modality"] == "console_text"
    assert records[1]["fallback_reason"] == "gui_closed"
    assert records[1]["display_status"] == "displayed"
    assert output == ["[CUE] CONTACT EXIT"]


def test_new_contact_enter_displays_after_contact_exit_state_clears() -> None:
    runtime = CueRuntime(
        trial_id="trial_test",
        sink_config=CueSinkConfig(cue_sink="gui_text", mode="live", is_live_cue_timing=True),
    )
    runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(
            contact_state=ContactState.OUTSIDE_BLOCK,
            events=(_Event("contact_exit", {}),),
        ),
        snapshot=_Snapshot(pinch_center_task=[0.3, 0.0, 0.0]),
    )
    commands = runtime.process_frame(
        frame_index=3,
        source_frame_id=3,
        sample=_Sample(1.2),
        trial_result=_result(events=(_Event("contact_enter", {}),)),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    active = runtime.gui_cue_store.get_active()
    runtime.end_trial()

    assert [command.cue_type for command in commands] == ["contact_enter"]
    assert active is not None
    assert active.cue_type == "contact_enter"
    assert runtime.records_snapshot()[2]["display_status"] == "not_displayed"
    assert runtime.records_snapshot()[2]["not_displayed_reason"] == "trial_ended_before_render"


def test_parse_error_clear_does_not_recue_same_signature() -> None:
    runtime = _runtime()
    first = runtime.process_frame(
        frame_index=1,
        source_frame_id=1,
        sample=_Sample(1.0),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.PINCH_INSUFFICIENT,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )

    runtime.handle_input_error()
    second = runtime.process_frame(
        frame_index=2,
        source_frame_id=2,
        sample=_Sample(1.1),
        trial_result=_result(
            slip_active=True,
            slip_reason=SlipReason.PINCH_INSUFFICIENT,
        ),
        snapshot=_Snapshot(pinch_center_task=[0.0, 0.0, 0.0]),
    )
    runtime.end_trial()

    assert len(first) == 1
    assert second == ()


def _runtime(config: CueConfig | None = None) -> CueRuntime:
    return CueRuntime(
        trial_id="trial_test",
        cue_config=config,
        sink_config=CueSinkConfig(cue_sink="logging", mode="live", is_live_cue_timing=True),
    )


def _result(
    *,
    contact_state: ContactState = ContactState.INSIDE_BLOCK,
    slip_active: bool = False,
    slip_reason: SlipReason | None = None,
    blocked_force_active: bool = False,
    primary_surface: Surface | None = None,
    track_state: TrackState = TrackState.INSIDE_TRACK,
    events: tuple[_Event, ...] = (),
    trial_time: float = 0.0,
) -> _Result:
    stop_reason = StopReason.TRACK_BLOCKED if blocked_force_active else StopReason.NONE
    return _Result(
        trial_id="trial_test",
        frame_output=_Output(
            contact_state=contact_state,
            block_state=BlockState(
                center=Vec3(0.0, 0.0, 0.0),
                size=Vec3(0.2, 0.2, 0.2),
                visible=contact_state == ContactState.OUTSIDE_BLOCK,
                motion_state=BlockMotionState.CONTACT_HIDDEN,
            ),
            feedback_state=FeedbackState(
                tracking_valid=True,
                recovery_frame=False,
                stop_reason=stop_reason,
                track_state=track_state,
                detach_state=DetachState.NONE,
            ),
        ),
        haptic_feedback_state=HapticFeedbackState(
            slip_active=slip_active,
            slip_reason=slip_reason,
            blocked_force_active=blocked_force_active,
            force_vector_task=Vec3(-0.1, 0.0, 0.0) if blocked_force_active else None,
            force_magnitude=0.1 if blocked_force_active else 0.0,
            primary_blocked_surface=primary_surface,
            primary_blocked_amount=0.1 if blocked_force_active else 0.0,
        ),
        events=events,
        time_since_prompt=trial_time,
    )


def _command() -> CueCommand:
    return CueCommand(
        cue_sequence_index=0,
        cue_id="trial_test:cue:000000",
        trial_id="trial_test",
        cue_type="contact_enter",
        requested_cue_sink="logging",
        mode="live",
        is_live_cue_timing=True,
        trigger_reason="edge_start",
        source_frame_index=1,
        source_frame_id=10,
        source_sample_time=1.0,
        source_trial_time=0.0,
        source_event_type="contact_enter",
        source_state="INSIDE_BLOCK",
        detach_state=None,
        slip_reason=None,
        track_state="INSIDE_TRACK",
        primary_blocked_surface=None,
        direction=None,
        pattern="contact_enter",
        intensity=None,
        duration_ms=None,
        message="CONTACT",
        created_monotonic_ms=time.monotonic() * 1000.0,
    )
