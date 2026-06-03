"""Tests for the reusable Stage 5C live trial loop."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from latest_frame_buffer import LatestFrameBufferStats
from live_raw_stream import LiveRawFrame
from live_trial_runner import LiveTrialRunner, LiveTrialRunnerConfig
from session_recorder import SessionRecorder
from task_coordinate_system import TaskCoordinateSystem
from timing_diagnostics import TimingDiagnostics


def test_step_once_processes_one_frame_and_builds_snapshot(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    snapshot = runner.step_once()

    assert snapshot is not None
    assert snapshot.frame_index == 0
    assert runner.trial_started is True
    assert runner.stats_snapshot().total_processed_frames == 1


def test_run_until_done_stops_at_max_frames(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.02)],
        max_frames=1,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.stats.total_processed_frames == 1
    assert result.summary["trial_controller_started"] is True
    assert result.summary["trial_outcome"] == "MAX_FRAMES_REACHED"
    assert result.summary["end_reason"] == "max_frames"


def test_parse_error_increments_stats_without_crashing(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0, raw_frame="bad")])

    snapshot = runner.step_once()

    assert snapshot is None
    assert runner.stats_snapshot().parse_error_count == 1
    assert runner.stats_snapshot().total_processed_frames == 0


def test_adapter_error_increments_stats_without_crashing(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])
    runner.adapter = _RaisingAdapter()

    snapshot = runner.step_once()

    assert snapshot is None
    assert runner.stats_snapshot().adapter_error_count == 1
    assert runner.stats_snapshot().total_processed_frames == 0


def test_no_new_frame_timeout_exits(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [],
        control_rate_hz=1000.0,
        no_frame_timeout_seconds=0.01,
        max_frames=None,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "no_new_frame_timeout"
    assert result.stats.no_new_frame_count > 0


def test_request_stop_is_safe_before_run(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)], max_frames=None)

    runner.request_stop("manual_stop")
    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "manual_stop"
    assert result.stats.total_processed_frames == 0


def test_snapshot_callback_is_called(tmp_path: Path) -> None:
    snapshots = []
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        snapshot_callback=snapshots.append,
    )

    runner.run_until_done()

    assert len(snapshots) == 1
    assert snapshots[0].frame_index == 0


def test_snapshot_callback_exception_is_counted(tmp_path: Path) -> None:
    def failing_callback(snapshot: Any) -> None:
        del snapshot
        raise RuntimeError("display boom")

    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        snapshot_callback=failing_callback,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.stats.callback_error_count == 1
    assert any("snapshot_callback failed" in warning for warning in result.summary["warnings"])


def test_timing_instrumentation_records_processing_without_gui_latency(tmp_path: Path) -> None:
    diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        timing_diagnostics=diagnostics,
    )

    result = runner.run_until_done()
    row = diagnostics.rows_snapshot()[0]

    assert result.summary["trial_outcome"] == "MAX_FRAMES_REACHED"
    assert row["parse_duration_ms"] is not None
    assert row["adapter_duration_ms"] is not None
    assert row["trial_update_duration_ms"] is not None
    assert row["snapshot_published_monotonic_ms"] is not None
    assert row["gui_render_monotonic_ms"] is None
    assert row["snapshot_publish_to_gui_render_latency_ms"] is None


def test_operator_command_timing_uses_last_consumed_frame_without_snapshot(tmp_path: Path) -> None:
    diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0, raw_frame="bad")],
        max_frames=None,
        operator_command_checker=lambda: "q" if runner.stats_snapshot().parse_error_count else None,
        timing_diagnostics=diagnostics,
    )

    result = runner.run_until_done()
    row = diagnostics.rows_snapshot()[0]

    assert result.summary["trial_outcome"] == "ABORTED_BY_OPERATOR"
    assert row["frame_index"] == 0
    assert row["event_type"] == "operator_command"
    assert row["operator_command_to_trial_stop_latency_ms"] is not None


def test_operator_manual_complete_stops_runner_and_records_event(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.0), _live_frame(2, 0.0)],
        max_frames=None,
        operator_command_checker=lambda: "e" if runner.last_snapshot is not None else None,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "operator_manual_complete"
    assert result.summary["trial_outcome"] == "MANUAL_COMPLETED"
    assert result.summary["end_reason"] == "operator_manual_complete"
    assert result.summary["operator_command"] == "e"
    assert result.summary["manual_completed"] is True
    events = (tmp_path / "session" / "events.csv").read_text(encoding="utf-8")
    assert "operator_manual_complete" in events


def test_subject_end_is_completed_but_not_operator_manual_complete(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0, subject_end=True)],
        max_frames=None,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "ended_by_subject"
    assert result.summary["trial_outcome"] == "MANUAL_COMPLETED"
    assert result.summary["end_reason"] == "subject_end"
    assert result.summary["operator_command"] is None
    assert result.summary["manual_completed"] is False


def test_operator_abort_keeps_user_quit_compatibility_and_records_outcome(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.0)],
        max_frames=None,
        operator_command_checker=lambda: "q" if runner.last_snapshot is not None else None,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "user_quit"
    assert result.summary["trial_outcome"] == "ABORTED_BY_OPERATOR"
    assert result.summary["end_reason"] == "operator_abort"
    assert result.summary["operator_command"] == "q"
    assert result.summary["operator_aborted"] is True
    events = (tmp_path / "session" / "events.csv").read_text(encoding="utf-8")
    assert "operator_abort" in events


def test_protective_timeout_records_failed_timeout(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.0)],
        max_frames=None,
        trial_timeout_seconds=0.001,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "failed_timeout"
    assert result.summary["trial_outcome"] == "FAILED_TIMEOUT"
    assert result.summary["end_reason"] == "trial_timeout"


def test_detach_limit_records_failed_too_many_detaches(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0), _live_frame(1, 0.5)],
        max_frames=None,
        max_detach_count=0,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "failed_too_many_detaches"
    assert result.summary["trial_outcome"] == "FAILED_TOO_MANY_DETACHES"
    assert result.summary["end_reason"] == "too_many_detaches"
    assert result.summary["detach_count"] == 1


def test_target_entry_is_diagnostic_not_auto_success(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)], max_frames=1)

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.summary["trial_outcome"] == "MAX_FRAMES_REACHED"
    assert result.summary["block_center_in_target_at_end"] is True
    assert result.summary["first_target_entry_time"] is not None
    assert result.summary["contact_state_at_end"] == "INSIDE_BLOCK"
    assert result.summary["detach_state_at_end"] == "NONE"


def test_slip_event_does_not_cause_failure(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [
            _live_frame(0, 0.0),
            _live_frame(1, 0.05, pinch_distance=0.2),
        ],
        max_frames=2,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.summary["trial_outcome"] == "MAX_FRAMES_REACHED"
    assert result.summary["slip_active_frame_count"] == 1
    assert result.summary["slip_active_at_end"] is True
    assert result.summary["slip_reason_at_end"] == "PINCH_INSUFFICIENT"


def test_blocked_event_does_not_cause_failure(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.7), _live_frame(1, 1.1)],
        max_frames=2,
        block_initial_center_task=Vec3(0.7, 0.0, 0.0),
        block_size=Vec3(1.0, 1.0, 1.0),
        track_region=TrackRegion(
            boxes=(
                Box3D(
                    center=Vec3(0.0, 0.0, 0.0),
                    size=Vec3(2.0, 2.0, 2.0),
                ),
            )
        ),
        engine_overrides={"max_hand_delta_per_frame": 0.5},
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.summary["trial_outcome"] == "MAX_FRAMES_REACHED"
    assert result.summary["blocked_frame_count"] == 1
    assert result.summary["blocked_force_active_at_end"] is True
    assert result.summary["stop_reason_at_end"] == "TRACK_BLOCKED"


def test_logical_haptic_label_counts_are_reported(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    result = runner.run_until_done()

    assert result.stats.logical_haptic_label_counts["NONE"] == 1


def test_session_recorder_can_finalize_runner_summary(tmp_path: Path) -> None:
    runner, recorder, _ = _make_runner(tmp_path, [_live_frame(0, 0.0)])

    result = runner.run_until_done()
    recorder.finalize(result.summary)

    trial_summary = json.loads((tmp_path / "session" / "trial_summary.json").read_text(encoding="utf-8"))
    assert trial_summary["total_processed_frames"] == 1
    assert (tmp_path / "session" / "processed_frames.csv").exists()


def test_source_stats_getter_exception_does_not_break_summary(tmp_path: Path) -> None:
    def bad_stats() -> dict[str, Any]:
        raise RuntimeError("stats boom")

    runner, _, _ = _make_runner(
        tmp_path,
        [_live_frame(0, 0.0)],
        source_stats_getter=bad_stats,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "max_frames"
    assert result.stats.total_received_frames == 1
    assert any("source_stats_getter failed" in warning for warning in result.summary["warnings"])


def test_source_stop_reason_getter_exception_does_not_crash_timeout_path(tmp_path: Path) -> None:
    def bad_stop_reason() -> str | None:
        raise RuntimeError("stop reason boom")

    runner, _, _ = _make_runner(
        tmp_path,
        [],
        control_rate_hz=1000.0,
        max_frames=None,
        no_frame_timeout_seconds=0.01,
        source_stop_reason_getter=bad_stop_reason,
    )

    result = runner.run_until_done()

    assert result.stats.run_stop_reason == "no_new_frame_timeout"
    assert any("source_stop_reason_getter failed" in warning for warning in result.summary["warnings"])


class _FakeLatestFrameBuffer:
    def __init__(self, frames: list[LiveRawFrame]) -> None:
        self.frames = list(frames)
        self.put_count = len(frames)
        self.consumed_count = 0

    def get_latest(
        self,
        *,
        allow_already_consumed: bool = False,
        consume: bool = True,
    ) -> LiveRawFrame | None:
        del allow_already_consumed, consume
        if not self.frames:
            return None
        self.consumed_count += 1
        return self.frames.pop(0)

    def stats_snapshot(self) -> LatestFrameBufferStats:
        return LatestFrameBufferStats(
            put_count=self.put_count,
            consumed_count=self.consumed_count,
            overwritten_frame_count=0,
            dropped_old_frame_count=0,
            last_frame_index=self.consumed_count - 1 if self.consumed_count else None,
            last_receive_time=None,
            has_unconsumed_frame=bool(self.frames),
        )


class _RaisingAdapter:
    def to_experiment_input_sample(self, device_frame: Any) -> None:
        del device_frame
        raise RuntimeError("adapter boom")


def _make_runner(
    tmp_path: Path,
    frames: list[LiveRawFrame],
    *,
    snapshot_callback: Any = None,
    source_stop_reason_getter: Any = None,
    source_stats_getter: Any = None,
    control_rate_hz: float = 1000.0,
    no_frame_timeout_seconds: float = 0.05,
    max_frames: int | None = 1,
    operator_command_checker: Any = None,
    trial_timeout_seconds: float = 1e9,
    max_detach_count: int = 1_000_000,
    block_initial_center_task: Vec3 | None = None,
    block_size: Vec3 | None = None,
    track_region: TrackRegion | None = None,
    engine_overrides: dict[str, Any] | None = None,
    timing_diagnostics: TimingDiagnostics | None = None,
) -> tuple[LiveTrialRunner, SessionRecorder, _FakeLatestFrameBuffer]:
    buffer = _FakeLatestFrameBuffer(frames)
    block_initial_center_task = block_initial_center_task or Vec3(0.0, 0.0, 0.0)
    block_size = block_size or Vec3(0.2, 0.2, 0.2)
    engine_kwargs = {
        "block_size_x": block_size.x,
        "block_size_y": block_size.y,
        "block_size_z": block_size.z,
        "trial_timeout_seconds": trial_timeout_seconds,
        "max_detach_count": max_detach_count,
    }
    if engine_overrides is not None:
        engine_kwargs.update(engine_overrides)
    recorder = SessionRecorder(tmp_path / "session", overwrite=True)
    recorder.start_session(
        session_meta={"mode": "test_live_trial_runner"},
        calibration=None,
        trial_config={
            "mode": "test_live_trial_runner",
            "target_region": {"min": [-0.1, -0.1, -0.1], "max": [0.1, 0.1, 0.1]},
        },
    )
    runner = LiveTrialRunner(
        latest_frame_buffer=buffer,
        task_coordinate_system=_task_system(),
        track_region=track_region or _track_region(),
        block_initial_center_task=block_initial_center_task,
        block_size=block_size,
        engine_config=EngineConfig(**engine_kwargs),
        session_recorder=recorder,
        config=LiveTrialRunnerConfig(
            trial_id="trial_test",
            control_rate_hz=control_rate_hz,
            max_frames=max_frames,
            no_frame_timeout_seconds=no_frame_timeout_seconds,
        ),
        map_id="test_map",
        calibration_id="test_calibration",
        trial_config={
            "mode": "test_live_trial_runner",
            "target_region": {"min": [-0.1, -0.1, -0.1], "max": [0.1, 0.1, 0.1]},
        },
        snapshot_callback=snapshot_callback,
        source_stop_reason_getter=source_stop_reason_getter,
        source_stats_getter=source_stats_getter or (lambda: {"total_received_frames": buffer.put_count}),
        operator_command_checker=operator_command_checker,
        timing_diagnostics=timing_diagnostics,
    )
    return runner, recorder, buffer


def _task_system() -> TaskCoordinateSystem:
    return TaskCoordinateSystem.build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )


def _track_region() -> TrackRegion:
    return TrackRegion(
        boxes=(
            Box3D(
                center=Vec3(0.25, 0.0, 0.0),
                size=Vec3(1.0, 1.0, 1.0),
            ),
        )
    )


def _live_frame(
    frame_index: int,
    x_position: float,
    *,
    raw_frame: Any | None = None,
    pinch_distance: float = 0.01,
    subject_end: bool = False,
) -> LiveRawFrame:
    raw = (
        raw_frame
        if raw_frame is not None
        else _raw_frame(
            float(frame_index),
            [x_position, 0.0, 0.0],
            pinch_distance=pinch_distance,
            subject_end=subject_end,
        )
    )
    return LiveRawFrame(
        frame_index=frame_index,
        raw_frame=raw,
        receive_time_monotonic=time.monotonic(),
        receive_wall_time=time.time(),
        byte_length=len(json.dumps(raw, default=str)),
    )


def _raw_frame(
    seconds: float,
    position: list[float],
    *,
    pinch_distance: float = 0.01,
    subject_end: bool = False,
) -> dict[str, Any]:
    half_pinch = float(pinch_distance) / 2.0
    return {
        "timestamp": seconds * 1000.0,
        "frame": int(seconds),
        "subject_end": subject_end,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "side": "left",
                "nodes": [
                    {"id": 4, "position": [-half_pinch, 0.0, 0.0]},
                    {"id": 9, "position": [half_pinch, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": position,
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }
