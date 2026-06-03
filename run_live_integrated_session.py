"""Stage 5C integrated live calibration + trial session runner."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from threading import Event, Thread
from typing import Any, Callable

from calibration_io import (
    FORMAL_CALIBRATION_TYPE,
    FormalCalibration,
    build_task_coordinate_system_from_calibration,
    calibration_to_dict,
    save_calibration,
)
from calibration_live_runner import CalibrationLiveConfig, CalibrationSegmentSpec, run_live_table_calibration
from config import EngineConfig
from cue_config import CueConfig, default_cue_config, load_cue_config
from cue_feedback import CUE_SINK_CHOICES, CueRuntime, CueSinkConfig
from dashboard_snapshot import build_compact_status_line
from device_frame_models import DeviceAdapterConfig
from latest_frame_buffer import LatestFrameBuffer, LatestFramePump
from live_raw_stream import LiveRawFrame, LiveRawStreamServer
from live_session_state import LiveSessionPhase, LiveSessionStatus
from live_trial_runner import LiveTrialRunner, LiveTrialRunnerConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from map_config import (
    MapBoxSpec,
    MapConfig,
    compile_map_to_track_region,
    load_map_config,
    map_config_to_trial_config,
    validate_map_config,
)
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from session_recorder import SessionRecorder
from termination_config import TerminationConfig, default_termination_config, load_termination_config
from timing_diagnostics import TimingDiagnostics
from trial_controller import ExperimentInputSample
from visual_profile import (
    DISPLAY_CONTROL_CHOICES,
    VISUAL_PROFILE_CHOICES,
    resolve_visual_profile,
)


InputFn = Callable[[str], str]


class LiveGuiDependencyError(RuntimeError):
    """Raised when --gui is requested but optional GUI dependencies are unavailable."""


@dataclass(frozen=True)
class LiveIntegratedSessionConfig:
    """Configuration for one integrated live session."""

    map_config: Path
    host: str = "127.0.0.1"
    port: int = 8888
    out_dir: Path = Path("data/live_integrated_session")
    session_dir: Path | None = None
    overwrite_session: bool = False
    subject_id: str | None = None
    trial_id: str = "live_integrated_trial"
    notes: str | None = None
    strict_map_validation: bool = False
    calibration_id: str | None = None
    point_source: str = "tracker_position_world"
    sample_duration_seconds: float = 5.0
    min_samples: int = 10
    min_line_length: float = 0.10
    up_hint: list[float] | None = None
    confirm_calibration: bool = True
    allow_calibration_warnings: bool = True
    recalibrate: bool = False
    control_rate_hz: float = 60.0
    max_frames: int | None = None
    duration_seconds: float | None = None
    pinch_grab_threshold: float | None = None
    pinch_release_threshold: float | None = None
    slip_motion_threshold: float | None = None
    ignore_task_z: bool = False
    task_z_half_extent: float = 5.0
    display_mode: str = "text"
    print_every: int = 30
    gui: bool = False
    gui_fps: float = 30.0
    cue_sink: str = "logging"
    cue_config: CueConfig = field(default_factory=default_cue_config)
    cue_config_path: Path | None = None
    visual_profile: str = "debug_all"
    status_panel: str = "auto"
    show_axes: str = "auto"
    show_grid: str = "auto"
    termination_config: TerminationConfig = field(default_factory=default_termination_config)
    termination_config_path: Path | None = None
    anchor_current_pinch_debug: bool = False
    anchor_timeout_seconds: float = 10.0
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    pinch_position_mode: str = "nodes_world"
    timestamp_scale: float = 0.001
    socket_timeout: float | None = None
    stream_wait_timeout_seconds: float = 60.0
    valid_tracker_timeout_seconds: float = 60.0
    valid_pinch_timeout_seconds: float = 60.0
    no_frame_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class LiveIntegratedSessionResult:
    """Result of one integrated live session run."""

    summary: dict[str, Any]
    statuses: list[LiveSessionStatus]
    calibration: FormalCalibration | None = None
    session_dir: Path | None = None


def run_live_integrated_session(
    config: LiveIntegratedSessionConfig,
    *,
    source: Any | None = None,
    input_fn: InputFn | None = None,
) -> LiveIntegratedSessionResult:
    """Run a complete live calibration + trial session."""

    input_fn = input_fn or input
    visual_settings = resolve_visual_profile(
        config.visual_profile,
        status_panel=config.status_panel,
        show_axes=config.show_axes,
        show_grid=config.show_grid,
    )
    if config.cue_sink not in CUE_SINK_CHOICES:
        raise ValueError("cue_sink must be one of: " + ", ".join(CUE_SINK_CHOICES))
    if config.cue_sink == "gui_text" and not config.gui:
        raise ValueError("--cue-sink gui_text requires --gui.")
    if config.gui:
        _preflight_live_gui_dependencies()
    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = out_dir / "raw_frames.jsonl"
    summary_path = out_dir / "summary.json"
    calibration_out_path = out_dir / "calibration.json"
    session_dir = config.session_dir or _default_session_dir(out_dir)
    timing_diagnostics_path = session_dir / "timing_diagnostics.csv"

    stop_event = Event()
    raw_handle = raw_log_path.open("w", encoding="utf-8")
    phase = LiveSessionPhase.WAITING_FOR_STREAM
    timing_diagnostics = TimingDiagnostics(mode="live", is_live_latency=True)
    latest_buffer = LatestFrameBuffer(
        frame_published_callback=lambda frame, published, overwritten: timing_diagnostics.record_frame_published(
            frame,
            phase=phase.name,
            monotonic_time=published,
            overwritten_frame=overwritten,
        ),
        frame_consumed_callback=lambda frame, consumed: timing_diagnostics.record_frame_consumed(
            frame,
            phase=phase.name,
            monotonic_time=consumed,
        ),
    )
    live_source = source or LiveRawStreamServer(
        host=config.host,
        port=config.port,
        max_queue_size=1,
        socket_timeout=config.socket_timeout,
        stop_event=stop_event,
    )
    pump = LatestFramePump(
        live_source,
        latest_buffer,
        raw_frame_callback=lambda frame: _write_live_raw_frame(raw_handle, frame),
        stop_event=stop_event,
    )
    statuses: list[LiveSessionStatus] = []
    status_printer = _StatusPrinter(enabled=config.display_mode == "text")
    calibration: FormalCalibration | None = None
    session_recorder: SessionRecorder | None = None
    run_stop_reason = "completed"
    phase_at_stop = phase.name
    session_finalized = False
    trial_controller_started = False
    summary: dict[str, Any] = {}
    trial_runner_summary: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    processed_count = 0
    tracker_invalid_count = 0
    hand_invalid_count = 0
    pinch_valid_count = 0
    slip_active_count = 0
    blocked_count = 0
    no_new_frame_count = 0
    max_no_new_frame_gap_seconds = 0.0
    latency_ms: list[float] = []
    logical_counts: Counter[str] = Counter()
    calibration_live_metrics_summary: dict[str, Any] = {}
    calibration_segment_time_mode: str | None = None
    gui_snapshot_store: Any | None = None
    gui_diagnostics_path: Path | None = None
    gui_requested_stop = False
    cue_runtime: CueRuntime | None = None
    cue_log_path: Path | None = None

    def set_status(
        new_phase: LiveSessionPhase,
        message: str,
        *,
        frame_index: int | None = None,
        tracker_valid: bool = False,
        hand_valid: bool = False,
        pinch_valid: bool = False,
        map_id: str | None = None,
    ) -> None:
        nonlocal phase
        phase = new_phase
        status = LiveSessionStatus(
            phase=new_phase,
            message=message,
            frame_index=frame_index,
            tracker_valid=tracker_valid,
            hand_valid=hand_valid,
            pinch_valid=pinch_valid,
            calibration_id=calibration.calibration_id if calibration is not None else config.calibration_id,
            map_id=map_id,
            trial_id=config.trial_id,
            warnings=list(warnings),
            errors=list(errors),
        )
        statuses.append(status)
        status_printer.emit(status)

    adapter_config = _adapter_config(config)
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    live_trial_runner: LiveTrialRunner | None = None
    map_config: MapConfig | None = None
    map_anchor = _empty_map_anchor()
    map_warnings: list[str] = []
    engine_config: EngineConfig | None = None

    try:
        set_status(LiveSessionPhase.WAITING_FOR_STREAM, f"Listening on {config.host}:{config.port}.")
        pump.start()
        _wait_for_valid_tracker(latest_buffer, adapter_config, adapter, set_status, pump, stop_event, config)
        set_status(LiveSessionPhase.READY_FOR_CALIBRATION, "Stream is healthy; ready for calibration.")

        while True:
            calibration_result = _run_integrated_calibration(
                latest_buffer,
                config,
                set_status,
                input_fn,
            )
            calibration_live_metrics_summary = dict(calibration_result.live_metrics_summary)
            calibration_segment_time_mode = _calibration_segment_time_mode(calibration_result.segment_summaries)
            if calibration_result.calibration is not None and not calibration_result.errors:
                calibration = _mark_integrated_calibration(calibration_result.calibration)
                review_warnings = [*warnings, *calibration_result.warnings]
                set_status(LiveSessionPhase.CALIBRATION_REVIEW, "Calibration completed; reviewing quality.")
                _print_calibration_review(calibration, review_warnings, enabled=config.display_mode == "text")
                if calibration.warnings and not config.allow_calibration_warnings:
                    errors.extend(calibration.warnings)
                    run_stop_reason = "calibration_failed"
                    phase_at_stop = LiveSessionPhase.CALIBRATION_FAILED.name
                    set_status(LiveSessionPhase.CALIBRATION_FAILED, "Calibration warnings are not allowed.")
                    raise _SessionAbort
                if config.confirm_calibration and not _ask_yes_no(
                    input_fn,
                    "Continue with this calibration? [y/N] ",
                    default=False,
                ):
                    calibration = None
                    set_status(
                        LiveSessionPhase.READY_FOR_CALIBRATION,
                        "Calibration rejected; restarting calibration.",
                    )
                    continue
                warnings.extend(calibration_result.warnings)
                break

            pretrial_stop_reason = _pretrial_stop_reason(pump)
            if pretrial_stop_reason is not None:
                errors.extend(calibration_result.errors)
                run_stop_reason = pretrial_stop_reason
                phase_at_stop = phase.name
                set_status(LiveSessionPhase.ERROR, f"Live source stopped before trial: {pretrial_stop_reason}.")
                raise _SessionAbort

            errors.extend(calibration_result.errors)
            set_status(
                LiveSessionPhase.CALIBRATION_FAILED,
                "Calibration failed: " + "; ".join(calibration_result.errors),
            )
            if not _ask_yes_no(input_fn, "Retry calibration? [y/N] ", default=False):
                run_stop_reason = "calibration_failed"
                phase_at_stop = LiveSessionPhase.CALIBRATION_FAILED.name
                raise _SessionAbort

        assert calibration is not None
        save_calibration(calibration, calibration_out_path)
        task_system = build_task_coordinate_system_from_calibration(calibration)
        set_status(LiveSessionPhase.READY_FOR_TRIAL, "Loading map and preparing trial.")
        map_config = load_map_config(config.map_config)
        map_validation = validate_map_config(map_config)
        map_warnings = list(map_validation.warnings)
        if map_validation.errors or (config.strict_map_validation and map_warnings):
            errors.extend(map_validation.errors or map_warnings)
            run_stop_reason = "map_validation_failed"
            phase_at_stop = LiveSessionPhase.ERROR.name
            set_status(LiveSessionPhase.ERROR, "Map validation failed: " + "; ".join(errors))
            raise _SessionAbort

        if config.anchor_current_pinch_debug:
            map_config, map_anchor = _anchor_map_to_current_pinch_debug(
                map_config,
                latest_buffer,
                adapter_config,
                adapter,
                task_system,
                config.anchor_timeout_seconds,
            )
            warnings.append("Map was translated to current pinch for debugging; this is not a formal calibrated trial.")
        map_config = _map_config_for_live_session(map_config, config)
        track_region, block_center, block_size = compile_map_to_track_region(map_config)
        engine_config = _engine_config(config, block_size)
        trial_config = _trial_config_payload(
            config,
            map_config,
            calibration,
            engine_config,
            map_anchor,
            warnings,
            map_warnings,
        )
        session_recorder = SessionRecorder(session_dir, overwrite=config.overwrite_session)
        session_recorder.start_session(
            session_meta=_session_meta(config, session_dir, calibration, map_config, map_anchor, warnings),
            calibration=calibration_to_dict(calibration),
            trial_config=trial_config,
        )
        _write_json(session_dir / "termination_config.json", config.termination_config.to_dict())
        _write_json(session_dir / "cue_config.json", config.cue_config.to_dict())
        cue_log_path = session_dir / "cue_log.csv"
        cue_runtime = CueRuntime(
            trial_id=config.trial_id,
            cue_config=config.cue_config,
            sink_config=CueSinkConfig(
                cue_sink=config.cue_sink,
                mode="live",
                is_live_cue_timing=True,
            ),
        )
        set_status(LiveSessionPhase.READY_FOR_TRIAL, "Press Enter to start trial.", map_id=map_config.map_id)
        _prompt(input_fn, "Press Enter to start trial...")
        set_status(LiveSessionPhase.TRIAL_RUNNING, "Trial running.", map_id=map_config.map_id)
        _print_operator_commands(enabled=config.display_mode == "text", gui_enabled=config.gui)
        if config.gui:
            gui_snapshot_store = _make_latest_snapshot_store()
            gui_diagnostics_path = (
                session_dir / "gui_diagnostics.csv"
                if session_recorder is not None
                else out_dir / "gui_diagnostics.csv"
            )

        display_counter = {"processed": 0}

        def display_snapshot(snapshot: Any) -> None:
            if config.display_mode != "text" or config.print_every <= 0:
                return
            display_counter["processed"] += 1
            if display_counter["processed"] % config.print_every == 0:
                print(build_compact_status_line(phase.name, snapshot))

        live_trial_runner = LiveTrialRunner(
            latest_frame_buffer=latest_buffer,
            task_coordinate_system=task_system,
            track_region=track_region,
            block_initial_center_task=block_center,
            block_size=block_size,
            engine_config=engine_config,
            session_recorder=session_recorder,
            config=_live_trial_runner_config(config),
            map_config_payload=map_config_to_trial_config(map_config),
            trial_config=trial_config,
            map_id=map_config.map_id,
            calibration_id=calibration.calibration_id,
            display_callback=display_snapshot,
            snapshot_callback=gui_snapshot_store.publish if gui_snapshot_store is not None else None,
            source_stop_reason_getter=lambda: _source_stop_reason_from_pump(pump),
            source_stats_getter=lambda: pump.stats_snapshot(),
            operator_command_checker=_read_operator_command,
            timing_diagnostics=timing_diagnostics,
            cue_runtime=cue_runtime,
            cue_log_path=str(cue_log_path),
        )
        if gui_snapshot_store is not None:
            live_trial_result = _run_live_trial_with_gui(
                live_trial_runner,
                snapshot_store=gui_snapshot_store,
                trial_config=trial_config,
                gui_fps=config.gui_fps,
                log_path=gui_diagnostics_path,
                runtime_stats_getter=lambda: _live_gui_runtime_stats(
                    snapshot_store=gui_snapshot_store,
                    live_trial_runner=live_trial_runner,
                    pump=pump,
                ),
                timing_diagnostics=timing_diagnostics,
                cue_runtime=cue_runtime,
                visual_settings=visual_settings,
            )
        else:
            live_trial_result = live_trial_runner.run_until_done()
        trial_runner_summary = dict(live_trial_result.summary)
        trial_stats = live_trial_result.stats
        run_stop_reason = trial_stats.run_stop_reason
        trial_controller_started = live_trial_runner.trial_started
        processed_count = trial_stats.total_processed_frames
        tracker_invalid_count = trial_stats.tracker_invalid_frame_count
        hand_invalid_count = trial_stats.hand_invalid_frame_count
        pinch_valid_count = trial_stats.pinch_valid_frame_count
        slip_active_count = trial_stats.slip_active_frame_count
        blocked_count = trial_stats.blocked_frame_count
        no_new_frame_count = trial_stats.no_new_frame_count
        max_no_new_frame_gap_seconds = trial_stats.max_no_new_frame_gap_seconds
        logical_counts = Counter(trial_stats.logical_haptic_label_counts)
        latency_ms = []

        set_status(LiveSessionPhase.TRIAL_ENDED, f"Trial ended: {run_stop_reason}.", map_id=map_config.map_id)
        phase_at_stop = LiveSessionPhase.TRIAL_ENDED.name
    except _SessionAbort as abort:
        if abort.run_stop_reason is not None:
            run_stop_reason = abort.run_stop_reason
        if abort.phase_at_stop is not None:
            phase_at_stop = abort.phase_at_stop
        if abort.message:
            errors.append(abort.message)
    except KeyboardInterrupt:
        run_stop_reason = "keyboard_interrupt"
        phase_at_stop = phase.name
        if live_trial_runner is not None:
            live_trial_runner.request_stop("keyboard_interrupt")
            trial_runner_summary = dict(live_trial_runner.build_summary())
            trial_controller_started = live_trial_runner.trial_started
        set_status(LiveSessionPhase.SAVING, "KeyboardInterrupt received; saving session.")
    except Exception as exc:
        run_stop_reason = "error"
        errors.append(str(exc))
        phase_at_stop = phase.name
        set_status(LiveSessionPhase.ERROR, str(exc))
    finally:
        set_status(LiveSessionPhase.SAVING, "Saving outputs.")
        source_stop_reason_at_stop = _source_stop_reason_from_pump(pump)
        pump_stop_reason_at_stop = pump.stop_reason
        stop_event.set()
        pump.stop(run_stop_reason)
        pump.join(timeout=1.0)
        raw_handle.close()
        timing_path_written: Path | None = None
        cue_path_written: Path | None = None
        if cue_runtime is not None:
            cue_runtime.end_session()
            if cue_log_path is not None:
                try:
                    cue_path_written = cue_runtime.write_log(cue_log_path)
                except Exception as exc:
                    errors.append(f"cue log write failed: {exc}")
        if session_recorder is not None:
            try:
                timing_path_written = timing_diagnostics.write_csv(timing_diagnostics_path)
            except Exception as exc:
                errors.append(f"timing diagnostics write failed: {exc}")
        summary = _build_summary(
            config,
            pump,
            latest_buffer,
            run_stop_reason=run_stop_reason,
            phase_at_stop=phase_at_stop,
            session_finalized=False,
            session_dir=session_dir if session_recorder is not None else None,
            total_processed_frames=processed_count,
            tracker_invalid_count=tracker_invalid_count,
            hand_invalid_count=hand_invalid_count,
            pinch_valid_count=pinch_valid_count,
            slip_active_count=slip_active_count,
            blocked_count=blocked_count,
            no_new_frame_count=no_new_frame_count,
            max_no_new_frame_gap_seconds=max_no_new_frame_gap_seconds,
            logical_counts=logical_counts,
            latency_ms=latency_ms,
            calibration=calibration,
            calibration_warnings=warnings,
            map_warnings=map_warnings,
            map_anchor=map_anchor,
            calibration_live_metrics_summary=calibration_live_metrics_summary,
            calibration_segment_time_mode=calibration_segment_time_mode,
            source_stop_reason=source_stop_reason_at_stop,
            pump_stop_reason=pump_stop_reason_at_stop,
            trial_controller_started=trial_controller_started,
            errors=errors,
            gui_snapshot_store=gui_snapshot_store,
            gui_diagnostics_path=gui_diagnostics_path,
            gui_requested_stop=gui_requested_stop,
        )
        summary.update(timing_diagnostics.summary())
        summary["timing_diagnostics_path"] = (
            str(timing_path_written) if timing_path_written is not None else None
        )
        if cue_runtime is not None:
            summary.update(cue_runtime.summary(cue_log_path=cue_path_written))
        if trial_runner_summary:
            _merge_live_trial_runner_summary(summary, trial_runner_summary)
            if cue_runtime is not None:
                summary.update(cue_runtime.summary(cue_log_path=cue_path_written))
        if session_recorder is not None:
            try:
                summary["session_finalized"] = True
                session_recorder.finalize(summary)
                session_finalized = True
            except Exception as exc:
                summary["session_finalized"] = False
                errors.append(f"session finalize failed: {exc}")
                summary["errors"] = list(errors)
        summary["phase_at_stop"] = phase_at_stop
        summary["session_finalized"] = session_finalized
        summary["final_phase"] = LiveSessionPhase.STOPPED.name
        _write_json(summary_path, summary)
        set_status(LiveSessionPhase.STOPPED, f"Stopped: {run_stop_reason}.")

    return LiveIntegratedSessionResult(
        summary=summary,
        statuses=statuses,
        calibration=calibration,
        session_dir=session_dir if session_recorder is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = _parse_args(argv)
    try:
        config = _config_from_args(args)
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    try:
        result = run_live_integrated_session(config)
    except LiveGuiDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    if result.summary.get("run_stop_reason") in {
        "completed",
        "max_frames",
        "duration_reached",
        "operator_manual_complete",
        "user_quit",
    }:
        return 0
    if result.summary.get("run_stop_reason") == "keyboard_interrupt":
        return 130
    return 1


def _run_integrated_calibration(
    buffer: LatestFrameBuffer,
    config: LiveIntegratedSessionConfig,
    set_status: Callable[..., None],
    input_fn: InputFn,
) -> Any:
    cal_config = CalibrationLiveConfig(
        calibration_id=config.calibration_id or "",
        point_source=config.point_source,
        sample_duration_seconds=config.sample_duration_seconds,
        min_samples=config.min_samples,
        min_line_length=config.min_line_length,
        up_hint=list(config.up_hint or [0.0, 0.0, 1.0]),
        timestamp_scale=config.timestamp_scale,
        output_path=config.out_dir / "calibration.json",
        notes=config.notes,
        require_enter_between_segments=True,
        auto_advance=False,
        collection_mode="live_stream",
        thumb_node=config.thumb_node,
        index_node=config.index_node,
        tracker_index=config.tracker_index,
        skeleton_index=config.skeleton_index,
        pinch_position_mode=config.pinch_position_mode,
        print_every=config.print_every,
    )

    def before_segment(segment: CalibrationSegmentSpec) -> None:
        phase = {
            "origin": LiveSessionPhase.CALIBRATING_ORIGIN,
            "long_axis_line": LiveSessionPhase.CALIBRATING_LONG_LINE,
            "width_axis_line": LiveSessionPhase.CALIBRATING_WIDTH_LINE,
            "diagonal_line": LiveSessionPhase.CALIBRATING_DIAGONAL_LINE,
        }[segment.label]
        set_status(phase, segment.prompt)
        _prompt(input_fn, f"[{segment.label}] {segment.prompt}\nPress Enter to start...")

    def progress(summary: dict[str, Any]) -> None:
        if config.display_mode != "text":
            return
        print(
            f"[CAL] {summary['label']} elapsed={float(summary['duration_seconds']):.2f}s "
            f"valid={summary['valid_sample_count']} tracker={summary['tracker_valid_count']} "
            f"hand={summary['hand_valid_count']}"
        )

    return run_live_table_calibration(
        buffer,
        cal_config,
        before_segment_callback=before_segment,
        progress_callback=progress if config.print_every > 0 else None,
    )


def _wait_for_valid_tracker(
    buffer: LatestFrameBuffer,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    set_status: Callable[..., None],
    pump: LatestFramePump,
    stop_event: Event,
    config: LiveIntegratedSessionConfig,
) -> None:
    stream_started = time.monotonic()
    stream_deadline = stream_started + float(config.stream_wait_timeout_seconds)
    tracker_deadline: float | None = None
    saw_stream_frame = False
    while True:
        if stop_event.is_set():
            raise _SessionAbort(
                run_stop_reason="source_stopped_before_trial",
                phase_at_stop=LiveSessionPhase.WAITING_FOR_STREAM.name,
                message="Stop event was set before a valid tracker was available.",
            )
        stop_reason = _source_stop_reason_from_pump(pump)
        mapped_stop_reason = _pretrial_stop_reason(pump)
        if mapped_stop_reason is not None:
            phase_name = (
                LiveSessionPhase.WAITING_FOR_VALID_TRACKER.name
                if saw_stream_frame
                else LiveSessionPhase.WAITING_FOR_STREAM.name
            )
            set_status(LiveSessionPhase.ERROR, f"Live source stopped before trial: {stop_reason}.")
            raise _SessionAbort(
                run_stop_reason=mapped_stop_reason,
                phase_at_stop=phase_name,
                message=f"Live source stopped before trial: {stop_reason}.",
            )

        now = time.monotonic()
        if not saw_stream_frame and now >= stream_deadline:
            set_status(LiveSessionPhase.ERROR, "Timed out waiting for the first live raw frame.")
            raise _SessionAbort(
                run_stop_reason="stream_wait_timeout",
                phase_at_stop=LiveSessionPhase.WAITING_FOR_STREAM.name,
                message="Timed out waiting for the first live raw frame.",
            )
        if saw_stream_frame and tracker_deadline is not None and now >= tracker_deadline:
            set_status(LiveSessionPhase.ERROR, "Timed out waiting for tracker_valid=True.")
            raise _SessionAbort(
                run_stop_reason="valid_tracker_timeout",
                phase_at_stop=LiveSessionPhase.WAITING_FOR_VALID_TRACKER.name,
                message="Timed out waiting for tracker_valid=True.",
            )

        frame = buffer.get_frame(timeout=0.1)
        if frame is None:
            continue
        if not saw_stream_frame:
            saw_stream_frame = True
            tracker_deadline = time.monotonic() + float(config.valid_tracker_timeout_seconds)
            set_status(LiveSessionPhase.WAITING_FOR_VALID_TRACKER, "Live frames received; waiting for valid tracker.")
        processed = _parse_and_adapt(frame, adapter_config, adapter)
        sample = processed.get("sample")
        tracker_valid = bool(getattr(sample, "tracker_valid", False))
        hand_valid = bool(processed.get("hand_valid", False))
        pinch_valid = bool(_metadata_value(sample, "pinch_valid"))
        if tracker_valid:
            set_status(
                LiveSessionPhase.WAITING_FOR_VALID_PINCH,
                "Valid tracker found.",
                frame_index=frame.frame_index,
                tracker_valid=tracker_valid,
                hand_valid=hand_valid,
                pinch_valid=pinch_valid,
            )
            return


def _parse_and_adapt(
    live_frame: LiveRawFrame,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
) -> dict[str, Any]:
    device_frame = None
    sample = None
    parse_ok = False
    adapter_ok = False
    error_message = ""
    try:
        device_frame = parse_raw_manus_vive_frame(live_frame.raw_frame, adapter_config)
        parse_ok = True
        sample = adapter.to_experiment_input_sample(device_frame)
        adapter_ok = True
    except Exception as exc:
        error_message = str(exc)
    hand = getattr(device_frame, "hand", None) if device_frame is not None else None
    return {
        "parse_ok": parse_ok,
        "adapter_ok": adapter_ok,
        "device_frame": device_frame,
        "sample": sample,
        "hand_valid": bool(getattr(hand, "valid", False)),
        "error_message": error_message,
    }


def _mark_integrated_calibration(calibration: FormalCalibration) -> FormalCalibration:
    return replace(
        calibration,
        metadata={
            **calibration.metadata,
            "collection_mode": "live_stream_integrated",
            "integrated_live_session": True,
        },
    )


def _map_config_for_live_session(map_config: MapConfig, config: LiveIntegratedSessionConfig) -> MapConfig:
    if not config.ignore_task_z:
        return map_config
    return _expand_map_config_task_z(map_config, half_extent=config.task_z_half_extent)


def _expand_map_config_task_z(map_config: MapConfig, *, half_extent: float) -> MapConfig:
    center_z = float(map_config.block_initial_center_task[2])
    z_min = center_z - float(half_extent)
    z_max = center_z + float(half_extent)
    block_size = list(map_config.block_size)
    block_size[2] = max(float(block_size[2]), float(half_extent) * 2.0)
    return replace(
        map_config,
        block_size=block_size,
        track_boxes=[_expand_box_task_z(box, z_min=z_min, z_max=z_max) for box in map_config.track_boxes],
        target_region=(
            _expand_box_task_z(map_config.target_region, z_min=z_min, z_max=z_max)
            if map_config.target_region is not None
            else None
        ),
        metadata={
            **map_config.metadata,
            "live_integrated_task_z_mode": "ignore_expanded",
            "live_integrated_task_z_half_extent": float(half_extent),
        },
    )


def _expand_box_task_z(box: MapBoxSpec, *, z_min: float, z_max: float) -> MapBoxSpec:
    minimum = list(box.min)
    maximum = list(box.max)
    minimum[2] = float(z_min)
    maximum[2] = float(z_max)
    return replace(box, min=minimum, max=maximum)


def _anchor_map_to_current_pinch_debug(
    map_config: MapConfig,
    buffer: LatestFrameBuffer,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    task_system: Any,
    timeout_seconds: float,
) -> tuple[MapConfig, dict[str, Any]]:
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        frame = buffer.get_frame(timeout=0.1)
        if frame is None:
            continue
        processed = _parse_and_adapt(frame, adapter_config, adapter)
        sample = processed.get("sample")
        if sample is None or not getattr(sample, "tracker_valid", False) or sample.pinch_center_world is None:
            continue
        anchor_task_array = task_system.world_to_task(sample.pinch_center_world)
        anchor_task = [float(anchor_task_array[0]), float(anchor_task_array[1]), float(anchor_task_array[2])]
        original = list(map_config.block_initial_center_task)
        translation = [anchor_task[index] - original[index] for index in range(3)]
        anchored = _translate_map_config(map_config, translation)
        info = {
            "mode": "current_pinch_debug",
            "anchor_task": anchor_task,
            "translation_task": translation,
            "frame_index": int(frame.frame_index),
            "warning": "Map was translated to current pinch for debugging; this is not a formal calibrated trial.",
        }
        return replace(anchored, metadata={**anchored.metadata, "map_anchor": info}), info
    raise TimeoutError("timed out waiting for debug anchor pinch.")


def _empty_map_anchor() -> dict[str, Any]:
    return {
        "mode": "none",
        "anchor_task": None,
        "translation_task": None,
        "frame_index": None,
    }


def _translate_map_config(map_config: MapConfig, translation: list[float]) -> MapConfig:
    return replace(
        map_config,
        block_initial_center_task=_translate_point(map_config.block_initial_center_task, translation),
        track_boxes=[_translate_box(box, translation) for box in map_config.track_boxes],
        target_region=(
            _translate_box(map_config.target_region, translation)
            if map_config.target_region is not None
            else None
        ),
    )


def _translate_box(box: MapBoxSpec, translation: list[float]) -> MapBoxSpec:
    return replace(
        box,
        min=_translate_point(box.min, translation),
        max=_translate_point(box.max, translation),
    )


def _translate_point(point: list[float], translation: list[float]) -> list[float]:
    return [float(point[index]) + float(translation[index]) for index in range(3)]


def _engine_config(config: LiveIntegratedSessionConfig, block_size: Any) -> EngineConfig:
    defaults = EngineConfig()
    termination = config.termination_config
    return EngineConfig(
        block_size_x=block_size.x,
        block_size_y=block_size.y,
        block_size_z=block_size.z,
        pinch_grab_threshold=(
            defaults.pinch_grab_threshold
            if config.pinch_grab_threshold is None
            else config.pinch_grab_threshold
        ),
        pinch_release_threshold=(
            defaults.pinch_release_threshold
            if config.pinch_release_threshold is None
            else config.pinch_release_threshold
        ),
        slip_motion_threshold=(
            defaults.slip_motion_threshold
            if config.slip_motion_threshold is None
            else config.slip_motion_threshold
        ),
        trial_timeout_seconds=(
            termination.max_trial_duration_seconds
            if termination.timeout_enabled
            else 1e9
        ),
        max_detach_count=(
            termination.max_detach_count
            if termination.detach_limit_enabled
            else 1_000_000_000
        ),
    )


def _trial_config_payload(
    config: LiveIntegratedSessionConfig,
    map_config: MapConfig,
    calibration: FormalCalibration,
    engine_config: EngineConfig,
    map_anchor: dict[str, Any],
    warnings: list[str],
    map_warnings: list[str],
) -> dict[str, Any]:
    payload = map_config_to_trial_config(map_config)
    payload.update(
        {
            "mode": "live_integrated_session",
            "scene_type": "map_config",
            "map_config_path": str(config.map_config),
            "trial_id": config.trial_id,
            "calibration_id": calibration.calibration_id,
            "calibration_type": calibration.calibration_type,
            "task_coordinate_system": calibration_to_dict(calibration)["task_coordinate_system"],
            "map_anchor": map_anchor,
            "map_anchor_mode": map_anchor["mode"],
            "pinch_threshold": {
                "grab": engine_config.pinch_grab_threshold,
                "release": engine_config.pinch_release_threshold,
            },
            "slip_motion_threshold": engine_config.slip_motion_threshold,
            "control_rate_hz": config.control_rate_hz,
            "ignore_task_z": config.ignore_task_z,
            "task_z_half_extent": config.task_z_half_extent if config.ignore_task_z else None,
            "pinch_position_mode": config.pinch_position_mode,
            "termination_config": config.termination_config.to_dict(),
            "termination_config_path": (
                str(config.termination_config_path) if config.termination_config_path is not None else None
            ),
            "timing_enabled": True,
            "timing_mode": "live",
            "timing_is_live_latency": True,
            "haptic_hardware_enabled": False,
            "cue_sink": config.cue_sink,
            "cue_enabled": config.cue_sink != "none",
            "cue_mode": "live",
            "is_live_cue_timing": True,
            "effective_cue_config": config.cue_config.to_dict(),
            "requested_cue_config_path": (
                str(config.cue_config_path) if config.cue_config_path is not None else None
            ),
            **_visual_settings_payload(config),
            "warnings": list(warnings) + list(map_warnings),
        }
    )
    return payload


def _visual_settings_payload(config: LiveIntegratedSessionConfig) -> dict[str, Any]:
    return resolve_visual_profile(
        config.visual_profile,
        status_panel=config.status_panel,
        show_axes=config.show_axes,
        show_grid=config.show_grid,
    ).to_dict()


def _session_meta(
    config: LiveIntegratedSessionConfig,
    session_dir: Path,
    calibration: FormalCalibration,
    map_config: MapConfig,
    map_anchor: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    payload = {
        "session_id": session_dir.name,
        "mode": "live_integrated_session",
        "trial_id": config.trial_id,
        "subject_id": config.subject_id,
        "notes": config.notes,
        "is_live_trial": True,
        "is_formal_experiment": False,
        "calibration_type": FORMAL_CALIBRATION_TYPE,
        "is_formal_calibration": True,
        "calibration_id": calibration.calibration_id,
        "calibration_collection_mode": "live_stream_integrated",
        "map_id": map_config.map_id,
        "scene_type": "map_config",
        "map_anchor_mode": map_anchor["mode"],
        "haptic_hardware_enabled": False,
        "pinch_position_mode": config.pinch_position_mode,
        "termination_config": config.termination_config.to_dict(),
        "termination_config_path": (
            str(config.termination_config_path) if config.termination_config_path is not None else None
        ),
        "timing_enabled": True,
        "timing_mode": "live",
        "timing_is_live_latency": True,
        "cue_sink": config.cue_sink,
        "cue_enabled": config.cue_sink != "none",
        "cue_mode": "live",
        "is_live_cue_timing": True,
        "effective_cue_config": config.cue_config.to_dict(),
        "requested_cue_config_path": (
            str(config.cue_config_path) if config.cue_config_path is not None else None
        ),
        **_visual_settings_payload(config),
        "warnings": list(warnings),
    }
    if map_anchor["mode"] == "current_pinch_debug":
        warning = "Map was translated to current pinch for debugging; this is not a formal calibrated trial."
        if warning not in payload["warnings"]:
            payload["warnings"] = [*payload["warnings"], warning]
    return payload


def _build_summary(
    config: LiveIntegratedSessionConfig,
    pump: LatestFramePump,
    buffer: LatestFrameBuffer,
    *,
    run_stop_reason: str,
    phase_at_stop: str,
    session_finalized: bool,
    session_dir: Path | None,
    total_processed_frames: int,
    tracker_invalid_count: int,
    hand_invalid_count: int,
    pinch_valid_count: int,
    slip_active_count: int,
    blocked_count: int,
    no_new_frame_count: int,
    max_no_new_frame_gap_seconds: float,
    logical_counts: Counter[str],
    latency_ms: list[float],
    calibration: FormalCalibration | None,
    calibration_warnings: list[str],
    map_warnings: list[str],
    map_anchor: dict[str, Any],
    calibration_live_metrics_summary: dict[str, Any],
    calibration_segment_time_mode: str | None,
    source_stop_reason: str | None,
    pump_stop_reason: str | None,
    trial_controller_started: bool,
    errors: list[str],
    gui_snapshot_store: Any | None,
    gui_diagnostics_path: Path | None,
    gui_requested_stop: bool,
) -> dict[str, Any]:
    stats = pump.stats_snapshot()
    buffer_stats = buffer.stats_snapshot()
    total_received = stats.get("total_received_frames")
    if total_received is None:
        total_received = buffer_stats.put_count
    gui_summary = _live_gui_summary(config, gui_snapshot_store, gui_diagnostics_path, gui_requested_stop)
    summary = {
        "mode": "live_integrated_session",
        "is_live_trial": True,
        "is_formal_experiment": False,
        "run_stop_reason": run_stop_reason,
        "phase_at_stop": phase_at_stop,
        "session_finalized": session_finalized,
        "trial_controller_started": trial_controller_started,
        "trial_id": config.trial_id,
        "subject_id": config.subject_id,
        "session_dir": str(session_dir) if session_dir is not None else None,
        "calibration_type": calibration.calibration_type if calibration is not None else None,
        "calibration_id": calibration.calibration_id if calibration is not None else None,
        "calibration_quality": dict(calibration.quality) if calibration is not None else {},
        "calibration_warnings": list(calibration_warnings),
        "calibration_live_metrics_summary": dict(calibration_live_metrics_summary),
        "calibration_segment_time_mode": calibration_segment_time_mode,
        "map_warnings": list(map_warnings),
        "map_anchor_mode": str(map_anchor.get("mode", "none")),
        "map_anchor": dict(map_anchor),
        "pinch_position_mode": config.pinch_position_mode,
        "termination_config": config.termination_config.to_dict(),
        "termination_config_path": (
            str(config.termination_config_path) if config.termination_config_path is not None else None
        ),
        "max_trial_duration_seconds": config.termination_config.max_trial_duration_seconds,
        "max_detach_count": config.termination_config.max_detach_count,
        "manual_completion_enabled": config.termination_config.manual_completion_enabled,
        "timeout_enabled": config.termination_config.timeout_enabled,
        "detach_limit_enabled": config.termination_config.detach_limit_enabled,
        "stream_wait_timeout_seconds": config.stream_wait_timeout_seconds,
        "valid_tracker_timeout_seconds": config.valid_tracker_timeout_seconds,
        "valid_pinch_timeout_seconds": config.valid_pinch_timeout_seconds,
        "no_frame_timeout_seconds": config.no_frame_timeout_seconds,
        "cue_enabled": config.cue_sink != "none",
        "cue_sink": config.cue_sink,
        "cue_mode": "live",
        "is_live_cue_timing": True,
        "cue_log_path": None,
        "cue_count": 0,
        "cue_type_counts": {},
        "suppressed_cue_count": 0,
        "suppressed_cue_type_counts": {},
        "suppressed_cue_reason_counts": {},
        "effective_cue_config": config.cue_config.to_dict(),
        "requested_cue_config_path": (
            str(config.cue_config_path) if config.cue_config_path is not None else None
        ),
        "cue_warnings": [],
        **_visual_settings_payload(config),
        "total_received_frames": int(total_received or 0),
        "total_processed_frames": int(total_processed_frames),
        "source_stop_reason": source_stop_reason,
        "pump_stop_reason": pump_stop_reason,
        "dropped_or_overwritten_frame_count": int(
            stats.get("dropped_frame_count", 0) or 0
        )
        + int(buffer_stats.overwritten_frame_count),
        "overwritten_frame_count": int(buffer_stats.overwritten_frame_count),
        "latest_buffer_overwritten_frame_count": int(buffer_stats.overwritten_frame_count),
        "latest_buffer_last_frame_index": buffer_stats.last_frame_index,
        "latest_buffer_put_count": int(buffer_stats.put_count),
        "latest_buffer_consumed_count": int(buffer_stats.consumed_count),
        "source_dropped_frame_count": int(stats.get("dropped_frame_count", 0) or 0),
        "no_new_frame_count": int(no_new_frame_count),
        "max_no_new_frame_gap_seconds": float(max_no_new_frame_gap_seconds),
        "tracker_invalid_frame_count": int(tracker_invalid_count),
        "hand_invalid_frame_count": int(hand_invalid_count),
        "pinch_valid_frame_count": int(pinch_valid_count),
        "slip_active_frame_count": int(slip_active_count),
        "blocked_frame_count": int(blocked_count),
        "logical_haptic_label_counts": dict(logical_counts),
        "mean_processing_latency_ms": mean(latency_ms) if latency_ms else None,
        "max_processing_latency_ms": max(latency_ms) if latency_ms else None,
        "errors": list(errors),
        "warnings": [
            "Stage 5C integrated live session is not a complete formal experiment runner.",
            *calibration_warnings,
            *map_warnings,
        ],
    }
    summary.update(gui_summary)
    return summary


class _StatusPrinter:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._previous_phase: LiveSessionPhase | None = None

    def emit(self, status: LiveSessionStatus) -> None:
        if not self.enabled:
            return
        if status.phase != self._previous_phase:
            print(f"[PHASE] {status.phase.name}: {status.message}")
            self._previous_phase = status.phase


class _SessionAbort(Exception):
    """Internal control-flow signal for expected safe aborts."""

    def __init__(
        self,
        *,
        run_stop_reason: str | None = None,
        phase_at_stop: str | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(message or "")
        self.run_stop_reason = run_stop_reason
        self.phase_at_stop = phase_at_stop
        self.message = message


def _print_calibration_review(
    calibration: FormalCalibration,
    warnings: list[str],
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    print("[CALIBRATION] quality summary:")
    quality = calibration.quality
    for key in (
        "plane_fit_rmse_m",
        "long_line_length_m",
        "width_line_length_m",
        "diagonal_line_length_m",
        "long_width_angle_degrees",
        "width_line_angle_to_y_axis_degrees",
        "calibration_quality_status",
    ):
        if key in quality:
            print(f"  {key}: {quality[key]}")
    if warnings:
        print("[CALIBRATION] warnings:")
        for warning in warnings:
            print(f"  - {warning}")


def _prompt(input_fn: InputFn, message: str) -> str:
    return input_fn(message)


def _ask_yes_no(input_fn: InputFn, message: str, *, default: bool) -> bool:
    answer = _prompt(input_fn, message).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _write_live_raw_frame(handle: Any, frame: LiveRawFrame) -> None:
    handle.write(json.dumps(frame.raw_frame, ensure_ascii=False))
    handle.write("\n")
    handle.flush()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _preflight_live_gui_dependencies() -> None:
    try:
        from debug_gui import GuiDependencyError, preflight_gui_dependencies
    except ImportError as exc:
        raise LiveGuiDependencyError("Missing GUI dependencies. Install with: pip install PySide6 pyqtgraph") from exc

    try:
        preflight_gui_dependencies()
    except GuiDependencyError as exc:
        raise LiveGuiDependencyError(str(exc)) from exc


def _make_latest_snapshot_store() -> Any:
    from latest_snapshot_store import LatestSnapshotStore

    return LatestSnapshotStore()


def _run_live_trial_with_gui(
    live_trial_runner: LiveTrialRunner,
    *,
    snapshot_store: Any,
    trial_config: dict[str, Any],
    gui_fps: float,
    log_path: Path | None,
    runtime_stats_getter: Callable[[], dict[str, Any]],
    timing_diagnostics: TimingDiagnostics,
    cue_runtime: CueRuntime,
    visual_settings: Any,
) -> Any:
    result_holder: dict[str, Any] = {}

    def trial_worker() -> None:
        try:
            result_holder["result"] = live_trial_runner.run_until_done()
        except BaseException as exc:
            result_holder["error"] = exc

    thread = Thread(target=trial_worker, name="LiveTrialRunnerGUIWorker", daemon=False)
    thread.start()
    try:
        _run_live_debug_gui(
            snapshot_store=snapshot_store,
            trial_config=trial_config,
            gui_fps=gui_fps,
            log_path=log_path,
            runtime_stats_getter=runtime_stats_getter,
            render_callback=timing_diagnostics.record_gui_render,
            cue_store=cue_runtime.gui_cue_store,
            close_callback=cue_runtime.handle_gui_closed,
            visual_settings=visual_settings,
        )
    except KeyboardInterrupt:
        snapshot_store.mark_gui_closed()
        live_trial_runner.request_stop("keyboard_interrupt")
        thread.join()
        raise
    except Exception:
        live_trial_runner.request_stop("gui_error")
        thread.join()
        raise

    thread.join()
    if "error" in result_holder:
        error = result_holder["error"]
        if isinstance(error, BaseException):
            raise error
        raise RuntimeError(str(error))
    if "result" not in result_holder:
        raise RuntimeError("live trial worker exited without a result.")
    return result_holder["result"]


def _run_live_debug_gui(
    *,
    snapshot_store: Any,
    trial_config: dict[str, Any],
    gui_fps: float,
    log_path: Path | None,
    runtime_stats_getter: Callable[[], dict[str, Any]],
    render_callback: Callable[[Any, float], None] | None = None,
    cue_store: Any | None = None,
    close_callback: Callable[[], None] | None = None,
    visual_settings: Any | None = None,
) -> int:
    try:
        from debug_gui import GuiDependencyError, run_debug_gui
        from debug_view_model import scene_view_from_trial_config
    except ImportError as exc:
        raise LiveGuiDependencyError("Missing GUI dependencies. Install with: pip install PySide6 pyqtgraph") from exc

    try:
        visual_settings = visual_settings or resolve_visual_profile()
        return run_debug_gui(
            snapshot_store=snapshot_store,
            scene=scene_view_from_trial_config(trial_config),
            mode="live",
            gui_fps=gui_fps,
            title="Exp2 Live Debug GUI",
            runtime_stats_getter=runtime_stats_getter,
            log_path=log_path,
            render_callback=render_callback,
            cue_store=cue_store,
            close_callback=close_callback,
            visual_profile=visual_settings.visual_profile,
            status_panel=visual_settings.status_panel,
            show_axes=visual_settings.show_axes,
            show_grid=visual_settings.show_grid,
        )
    except GuiDependencyError as exc:
        raise LiveGuiDependencyError(str(exc)) from exc


def _live_gui_runtime_stats(
    *,
    snapshot_store: Any,
    live_trial_runner: LiveTrialRunner,
    pump: LatestFramePump,
) -> dict[str, Any]:
    store_stats = snapshot_store.stats_snapshot()
    runner_stats = live_trial_runner.stats_snapshot()
    pump_stats = pump.stats_snapshot()
    return {
        "mode": "live",
        "total_received_frames": runner_stats.total_received_frames,
        "parse_error_count": runner_stats.parse_error_count,
        "raw_dropped_frame_count": int(pump_stats.get("dropped_frame_count", 0) or 0),
        "overwritten_snapshot_count": store_stats.overwritten_snapshot_count,
    }


def _live_gui_summary(
    config: LiveIntegratedSessionConfig,
    snapshot_store: Any | None,
    diagnostics_path: Path | None,
    gui_requested_stop: bool,
) -> dict[str, Any]:
    if snapshot_store is None:
        return {
            "gui_enabled": bool(config.gui),
            "gui_closed": False,
            "gui_requested_stop": bool(gui_requested_stop),
            "gui_fps": float(config.gui_fps) if config.gui else None,
            "gui_diagnostics_path": str(diagnostics_path) if diagnostics_path is not None else None,
            "gui_snapshot_update_count": 0,
            "gui_overwritten_snapshot_count": 0,
            "gui_last_frame_index": None,
            "gui_close_time": None,
        }

    stats = snapshot_store.stats_snapshot()
    close_time = None
    if stats.gui_close_wall_time is not None:
        close_time = datetime.fromtimestamp(stats.gui_close_wall_time).isoformat(timespec="seconds")
    return {
        "gui_enabled": bool(config.gui),
        "gui_closed": bool(stats.gui_closed),
        "gui_requested_stop": bool(gui_requested_stop),
        "gui_fps": float(config.gui_fps),
        "gui_diagnostics_path": str(diagnostics_path) if diagnostics_path is not None else None,
        "gui_snapshot_update_count": int(stats.update_count),
        "gui_overwritten_snapshot_count": int(stats.overwritten_snapshot_count),
        "gui_last_frame_index": stats.last_frame_index,
        "gui_close_time": close_time,
    }


def _default_session_dir(out_dir: Path) -> Path:
    base = out_dir / "session"
    if not base.exists():
        return base
    stamped = out_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not stamped.exists():
        return stamped
    for index in range(1, 1000):
        candidate = out_dir / f"{stamped.name}_{index:03d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate session directory.")


def _adapter_config(config: LiveIntegratedSessionConfig) -> DeviceAdapterConfig:
    return DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        pinch_position_mode=config.pinch_position_mode,
        timestamp_scale=config.timestamp_scale,
    )


def _live_trial_runner_config(config: LiveIntegratedSessionConfig) -> LiveTrialRunnerConfig:
    return LiveTrialRunnerConfig(
        trial_id=config.trial_id,
        control_rate_hz=config.control_rate_hz,
        duration_seconds=config.duration_seconds,
        max_frames=config.max_frames,
        no_frame_timeout_seconds=config.no_frame_timeout_seconds,
        print_every=config.print_every,
        timestamp_scale=config.timestamp_scale,
        thumb_node=config.thumb_node,
        index_node=config.index_node,
        tracker_index=config.tracker_index,
        skeleton_index=config.skeleton_index,
        pinch_position_mode=config.pinch_position_mode,
        manual_completion_enabled=config.termination_config.manual_completion_enabled,
        timeout_enabled=config.termination_config.timeout_enabled,
        detach_limit_enabled=config.termination_config.detach_limit_enabled,
        haptic_hardware_enabled=False,
    )


def _merge_live_trial_runner_summary(
    summary: dict[str, Any],
    runner_summary: dict[str, Any],
) -> None:
    """Expose LiveTrialRunner stats while preserving legacy summary keys."""

    summary["live_trial_runner_summary"] = dict(runner_summary)
    legacy_keys = (
        "total_received_frames",
        "total_processed_frames",
        "parse_error_count",
        "adapter_error_count",
        "tracker_invalid_frame_count",
        "hand_invalid_frame_count",
        "pinch_valid_frame_count",
        "large_delta_frame_count",
        "slip_active_frame_count",
        "blocked_frame_count",
        "logical_haptic_label_counts",
        "no_new_frame_count",
        "max_no_new_frame_gap_seconds",
        "mean_processing_latency_ms",
        "max_processing_latency_ms",
        "callback_error_count",
        "mean_callback_latency_ms",
        "max_callback_latency_ms",
        "trial_outcome",
        "end_reason",
        "map_id",
        "calibration_id",
        "operator_command",
        "operator_command_time",
        "operator_command_monotonic_ms",
        "trial_end_monotonic_ms",
        "operator_command_to_trial_stop_latency_ms",
        "manual_completed",
        "operator_aborted",
        "trial_start_time",
        "trial_end_time",
        "trial_duration_seconds",
        "detach_count",
        "block_center_task_position_at_end",
        "pinch_task_position_at_end",
        "block_center_in_target_at_end",
        "distance_to_target_at_end",
        "contact_state_at_end",
        "block_motion_state_at_end",
        "stop_reason_at_end",
        "detach_state_at_end",
        "slip_active_at_end",
        "slip_reason_at_end",
        "blocked_force_active_at_end",
        "logical_haptic_label_at_end",
        "first_target_entry_time",
        "first_target_entry_frame_index",
        "last_snapshot_time",
        "last_frame_index",
        "cue_enabled",
        "cue_sink",
        "cue_mode",
        "is_live_cue_timing",
        "cue_log_path",
        "cue_count",
        "cue_type_counts",
        "suppressed_cue_count",
        "suppressed_cue_type_counts",
        "suppressed_cue_reason_counts",
        "effective_cue_config",
        "cue_warnings",
    )
    for key in legacy_keys:
        if key in runner_summary:
            summary[key] = runner_summary[key]

    merged_warnings = list(summary.get("warnings", []))
    for warning in runner_summary.get("warnings", []) or []:
        if warning not in merged_warnings:
            merged_warnings.append(warning)
    summary["warnings"] = merged_warnings


def _metadata_value(sample: Any, key: str) -> Any:
    if sample is None:
        return None
    metadata = getattr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    return metadata.get(key)


def _calibration_segment_time_mode(segment_summaries: list[dict[str, Any]]) -> str | None:
    modes = {
        str(summary.get("time_mode"))
        for summary in segment_summaries
        if summary.get("time_mode") not in (None, "")
    }
    if not modes:
        return None
    if len(modes) == 1:
        return next(iter(modes))
    return "mixed"


def _pretrial_stop_reason(pump: LatestFramePump) -> str | None:
    reason = _source_stop_reason_from_pump(pump)
    if reason is None:
        return None
    if _is_client_disconnected(reason):
        return "client_disconnected_before_trial"
    if reason in {"server_stopped", "socket_error", "eof", "source_stopped"}:
        return "source_stopped_before_trial"
    return None


def _source_stop_reason_from_pump(pump: LatestFramePump) -> str | None:
    reason = pump.stop_reason
    if reason is not None:
        return str(reason)
    stats = pump.stats_snapshot()
    value = stats.get("stop_reason")
    return str(value) if value is not None else None


def _is_client_disconnected(reason: str | None) -> bool:
    return reason == "client_disconnected"


def _is_source_stopped(reason: str | None, pump: LatestFramePump) -> bool:
    if reason in {"server_stopped", "socket_error", "eof", "source_stopped"}:
        return True
    stats = pump.stats_snapshot()
    return bool(stats.get("stop_event_set", False))


def _parse_vec3(value: str) -> list[float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3 or not all(math.isfinite(part) for part in parts):
        raise argparse.ArgumentTypeError("expected three finite comma-separated floats")
    return [float(part) for part in parts]


def _print_operator_commands(*, enabled: bool, gui_enabled: bool) -> None:
    if not enabled:
        return
    print("[OPERATOR] commands:")
    print("  e = end current trial as MANUAL_COMPLETED")
    print("  q = abort whole run")
    if gui_enabled:
        print("  GUI close = close display only, does not stop trial")


def _read_operator_command() -> str | None:
    try:
        import msvcrt

        if msvcrt.kbhit():
            key = msvcrt.getwch()
            value = key.lower()
            return value if value in {"e", "q"} else None
        return None
    except ImportError:
        return None


def _user_requested_quit() -> bool:
    return _read_operator_command() == "q"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Integrated live calibration + trial session.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8888, type=int)
    parser.add_argument("--out-dir", default="data/live_integrated_session")
    parser.add_argument("--session-dir", default=None)
    parser.add_argument("--overwrite-session", action="store_true")
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--trial-id", default="live_integrated_trial")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--map-config", required=True)
    parser.add_argument("--strict-map-validation", action="store_true")
    parser.add_argument("--calibration-id", default=None)
    parser.add_argument("--point-source", choices=("tracker_position_world", "pinch_center_world"), default="tracker_position_world")
    parser.add_argument("--sample-duration-seconds", default=5.0, type=float)
    parser.add_argument("--min-samples", default=10, type=int)
    parser.add_argument("--min-line-length", default=0.10, type=float)
    parser.add_argument("--up-hint", default=[0.0, 0.0, 1.0], type=_parse_vec3)
    parser.set_defaults(confirm_calibration=True, allow_calibration_warnings=True)
    parser.add_argument("--confirm-calibration", dest="confirm_calibration", action="store_true")
    parser.add_argument("--no-confirm-calibration", dest="confirm_calibration", action="store_false")
    parser.add_argument("--allow-calibration-warnings", dest="allow_calibration_warnings", action="store_true")
    parser.add_argument("--no-allow-calibration-warnings", dest="allow_calibration_warnings", action="store_false")
    parser.add_argument("--recalibrate", action="store_true")
    parser.add_argument("--control-rate-hz", default=60.0, type=float)
    parser.add_argument("--max-frames", default=None, type=int)
    parser.add_argument("--duration-seconds", default=None, type=float)
    parser.add_argument("--pinch-grab-threshold", default=None, type=float)
    parser.add_argument("--pinch-release-threshold", default=None, type=float)
    parser.add_argument("--slip-motion-threshold", default=None, type=float)
    parser.add_argument("--ignore-task-z", action="store_true")
    parser.add_argument("--task-z-half-extent", default=5.0, type=float)
    parser.add_argument("--display-mode", choices=("text", "none"), default="text")
    parser.add_argument("--print-every", default=30, type=int)
    parser.add_argument("--gui", action="store_true", help="Open the debug display during trial running.")
    parser.add_argument("--gui-fps", default=30.0, type=float)
    parser.add_argument("--cue-sink", choices=CUE_SINK_CHOICES, default="logging")
    parser.add_argument("--cue-config", default=None, help="JSON/YAML cue generation config.")
    parser.add_argument("--visual-profile", choices=VISUAL_PROFILE_CHOICES, default="debug_all")
    parser.add_argument("--status-panel", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    parser.add_argument("--show-axes", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    parser.add_argument("--show-grid", choices=DISPLAY_CONTROL_CHOICES, default="auto")
    parser.add_argument("--termination-config", default=None, help="JSON/YAML protective termination config.")
    parser.add_argument("--anchor-current-pinch-debug", action="store_true")
    parser.add_argument("--anchor-timeout-seconds", default=10.0, type=float)
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)
    parser.add_argument(
        "--pinch-position-mode",
        choices=("nodes_world", "tracker_plus_local"),
        default="nodes_world",
        help="How MANUS node positions are interpreted; live MANUS/Vive streams usually use nodes_world.",
    )
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--socket-timeout", default=None, type=float)
    parser.add_argument("--stream-wait-timeout-seconds", default=60.0, type=float)
    parser.add_argument("--valid-tracker-timeout-seconds", default=60.0, type=float)
    parser.add_argument("--valid-pinch-timeout-seconds", default=60.0, type=float)
    parser.add_argument("--no-frame-timeout-seconds", default=5.0, type=float)
    args = parser.parse_args(argv)
    if args.sample_duration_seconds <= 0.0:
        parser.error("--sample-duration-seconds must be > 0.")
    if args.min_samples <= 0:
        parser.error("--min-samples must be > 0.")
    if args.min_line_length <= 0.0:
        parser.error("--min-line-length must be > 0.")
    if args.control_rate_hz <= 0.0:
        parser.error("--control-rate-hz must be > 0.")
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be > 0.")
    if args.duration_seconds is not None and args.duration_seconds <= 0.0:
        parser.error("--duration-seconds must be > 0.")
    if args.print_every < 0:
        parser.error("--print-every must be >= 0.")
    if args.gui_fps <= 0.0:
        parser.error("--gui-fps must be > 0.")
    if args.task_z_half_extent <= 0.0:
        parser.error("--task-z-half-extent must be > 0.")
    if args.anchor_timeout_seconds <= 0.0:
        parser.error("--anchor-timeout-seconds must be > 0.")
    if args.stream_wait_timeout_seconds <= 0.0:
        parser.error("--stream-wait-timeout-seconds must be > 0.")
    if args.valid_tracker_timeout_seconds <= 0.0:
        parser.error("--valid-tracker-timeout-seconds must be > 0.")
    if args.valid_pinch_timeout_seconds <= 0.0:
        parser.error("--valid-pinch-timeout-seconds must be > 0.")
    if args.no_frame_timeout_seconds <= 0.0:
        parser.error("--no-frame-timeout-seconds must be > 0.")
    return args


def _config_from_args(args: argparse.Namespace) -> LiveIntegratedSessionConfig:
    termination_path = Path(args.termination_config) if args.termination_config is not None else None
    termination = load_termination_config(termination_path)
    cue_config_path = Path(args.cue_config) if args.cue_config is not None else None
    cue_config = load_cue_config(cue_config_path)
    if args.cue_sink == "gui_text" and not args.gui:
        raise ValueError("--cue-sink gui_text requires --gui.")
    resolve_visual_profile(
        args.visual_profile,
        status_panel=args.status_panel,
        show_axes=args.show_axes,
        show_grid=args.show_grid,
    )
    return LiveIntegratedSessionConfig(
        map_config=Path(args.map_config),
        host=args.host,
        port=args.port,
        out_dir=Path(args.out_dir),
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        overwrite_session=args.overwrite_session,
        subject_id=args.subject_id,
        trial_id=args.trial_id,
        notes=args.notes,
        strict_map_validation=args.strict_map_validation,
        calibration_id=args.calibration_id,
        point_source=args.point_source,
        sample_duration_seconds=args.sample_duration_seconds,
        min_samples=args.min_samples,
        min_line_length=args.min_line_length,
        up_hint=args.up_hint,
        confirm_calibration=args.confirm_calibration,
        allow_calibration_warnings=args.allow_calibration_warnings,
        recalibrate=args.recalibrate,
        control_rate_hz=args.control_rate_hz,
        max_frames=args.max_frames,
        duration_seconds=args.duration_seconds,
        pinch_grab_threshold=args.pinch_grab_threshold,
        pinch_release_threshold=args.pinch_release_threshold,
        slip_motion_threshold=args.slip_motion_threshold,
        ignore_task_z=args.ignore_task_z,
        task_z_half_extent=args.task_z_half_extent,
        display_mode=args.display_mode,
        print_every=args.print_every,
        gui=args.gui,
        gui_fps=args.gui_fps,
        cue_sink=args.cue_sink,
        cue_config=cue_config,
        cue_config_path=cue_config_path,
        visual_profile=args.visual_profile,
        status_panel=args.status_panel,
        show_axes=args.show_axes,
        show_grid=args.show_grid,
        termination_config=termination,
        termination_config_path=termination_path,
        anchor_current_pinch_debug=args.anchor_current_pinch_debug,
        anchor_timeout_seconds=args.anchor_timeout_seconds,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        pinch_position_mode=args.pinch_position_mode,
        timestamp_scale=args.timestamp_scale,
        socket_timeout=args.socket_timeout,
        stream_wait_timeout_seconds=args.stream_wait_timeout_seconds,
        valid_tracker_timeout_seconds=args.valid_tracker_timeout_seconds,
        valid_pinch_timeout_seconds=args.valid_pinch_timeout_seconds,
        no_frame_timeout_seconds=args.no_frame_timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
