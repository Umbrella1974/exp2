"""Stage 5C integrated live calibration + trial session runner."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from threading import Event
from typing import Any, Callable

from block_controller import BlockController
from calibration_io import (
    FORMAL_CALIBRATION_TYPE,
    FormalCalibration,
    build_task_coordinate_system_from_calibration,
    calibration_to_dict,
    save_calibration,
)
from calibration_live_runner import CalibrationLiveConfig, CalibrationSegmentSpec, run_live_table_calibration
from config import EngineConfig
from dashboard_snapshot import build_compact_status_line, build_dashboard_snapshot
from device_frame_models import DeviceAdapterConfig
from latest_frame_buffer import LatestFrameBuffer, LatestFramePump
from live_raw_stream import LiveRawFrame, LiveRawStreamServer
from live_session_state import LiveSessionPhase, LiveSessionStatus
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
from trial_controller import ExperimentInputSample, TrialController, TrialState


InputFn = Callable[[str], str]


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
    anchor_current_pinch_debug: bool = False
    anchor_timeout_seconds: float = 10.0
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    timestamp_scale: float = 0.001
    socket_timeout: float | None = None


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
    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_log_path = out_dir / "raw_frames.jsonl"
    summary_path = out_dir / "summary.json"
    calibration_out_path = out_dir / "calibration.json"
    session_dir = config.session_dir or _default_session_dir(out_dir)

    stop_event = Event()
    raw_handle = raw_log_path.open("w", encoding="utf-8")
    latest_buffer = LatestFrameBuffer()
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
    phase = LiveSessionPhase.WAITING_FOR_STREAM
    phase_at_stop = phase.name
    session_finalized = False
    trial_controller_started = False
    summary: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []
    processed_count = 0
    tracker_invalid_count = 0
    hand_invalid_count = 0
    pinch_valid_count = 0
    slip_active_count = 0
    blocked_count = 0
    latency_ms: list[float] = []
    logical_counts: Counter[str] = Counter()

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
    trial: TrialController | None = None
    map_config: MapConfig | None = None
    map_anchor = _empty_map_anchor()
    map_warnings: list[str] = []
    engine_config: EngineConfig | None = None

    try:
        set_status(LiveSessionPhase.WAITING_FOR_STREAM, f"Listening on {config.host}:{config.port}.")
        pump.start()
        _wait_for_valid_tracker(latest_buffer, adapter_config, adapter, set_status)
        set_status(LiveSessionPhase.READY_FOR_CALIBRATION, "Stream is healthy; ready for calibration.")

        while True:
            calibration_result = _run_integrated_calibration(
                latest_buffer,
                config,
                set_status,
                input_fn,
            )
            if calibration_result.calibration is not None and not calibration_result.errors:
                calibration = _mark_integrated_calibration(calibration_result.calibration)
                warnings.extend(calibration_result.warnings)
                break

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
        set_status(LiveSessionPhase.CALIBRATION_REVIEW, "Calibration completed; reviewing quality.")
        _print_calibration_review(calibration, warnings, enabled=config.display_mode == "text")
        if calibration.warnings and not config.allow_calibration_warnings:
            errors.extend(calibration.warnings)
            run_stop_reason = "calibration_failed"
            phase_at_stop = LiveSessionPhase.CALIBRATION_FAILED.name
            set_status(LiveSessionPhase.CALIBRATION_FAILED, "Calibration warnings are not allowed.")
            raise _SessionAbort
        if config.confirm_calibration and not _ask_yes_no(input_fn, "Continue with this calibration? [y/N] ", default=False):
            run_stop_reason = "calibration_rejected"
            phase_at_stop = LiveSessionPhase.CALIBRATION_REVIEW.name
            raise _SessionAbort

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

        def factory() -> BlockController:
            assert engine_config is not None
            return BlockController(engine_config, track_region, block_center)

        trial = TrialController(factory, task_system, engine_config)
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
        set_status(LiveSessionPhase.READY_FOR_TRIAL, "Press Enter to start trial.", map_id=map_config.map_id)
        _prompt(input_fn, "Press Enter to start trial...")
        set_status(LiveSessionPhase.TRIAL_RUNNING, "Trial running.", map_id=map_config.map_id)

        run_started = time.monotonic()
        next_tick = time.monotonic()
        previous_print_frame = 0
        while True:
            if config.duration_seconds is not None and time.monotonic() - run_started >= config.duration_seconds:
                run_stop_reason = "duration_reached"
                break
            if config.max_frames is not None and processed_count >= config.max_frames:
                run_stop_reason = "max_frames"
                break
            if _user_requested_quit():
                run_stop_reason = "user_quit"
                break

            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.002, next_tick - now))
                continue
            next_tick = now + (1.0 / config.control_rate_hz)
            live_frame = latest_buffer.get_latest()
            if live_frame is None:
                continue

            start_process = time.monotonic()
            processed = _parse_and_adapt(live_frame, adapter_config, adapter)
            if processed["device_frame"] is not None:
                session_recorder.record_device_frame(live_frame.frame_index, processed["device_frame"])
            if not processed["parse_ok"] or not processed["adapter_ok"] or processed["sample"] is None:
                continue

            sample = processed["sample"]
            if not trial_controller_started:
                trial.start_trial(sample.time, config.trial_id)
                trial_controller_started = True
                session_recorder.record_events(live_frame.frame_index, sample.time, trial.event_history[-2:])
            result = trial.update(sample)
            processing_latency = (time.monotonic() - start_process) * 1000.0
            latency_ms.append(processing_latency)
            processed_count += 1
            output = result.frame_output
            hand_valid = bool(processed["hand_valid"])
            snapshot = build_dashboard_snapshot(
                frame_index=live_frame.frame_index,
                trial_result=result,
                sample=sample,
                hand_valid=hand_valid,
                map_id=map_config.map_id,
                calibration_id=calibration.calibration_id,
                processing_latency_ms=processing_latency,
                hardware_haptic_active=False,
            )
            logical_counts[snapshot.logical_haptic_label] += 1
            if not snapshot.tracker_valid:
                tracker_invalid_count += 1
            if not snapshot.hand_valid:
                hand_invalid_count += 1
            if snapshot.pinch_valid:
                pinch_valid_count += 1
            if snapshot.slip_active:
                slip_active_count += 1
            if snapshot.stop_reason == "TRACK_BLOCKED" or snapshot.blocked_force_active:
                blocked_count += 1

            session_recorder.record_raw_frame(live_frame.frame_index, live_frame.raw_frame)
            session_recorder.record_processed_frame(
                live_frame.frame_index,
                live_frame.raw_frame,
                processed["device_frame"],
                sample,
                output,
                haptic_state=result.haptic_feedback_state,
                extra={
                    "input_source": "live_integrated_session",
                    "trial_time": result.time_since_prompt,
                },
            )
            session_recorder.record_events(live_frame.frame_index, sample.time, result.events)
            session_recorder.record_haptic(
                live_frame.frame_index,
                sample.time,
                result.haptic_feedback_state,
                details={
                    "mode": "live_integrated_session",
                    "logical_haptic_label": snapshot.logical_haptic_label,
                    "feedback_label": snapshot.feedback_label,
                    "hardware_haptic_active": False,
                    "sent_to_hardware": False,
                },
            )
            if config.display_mode == "text" and config.print_every > 0:
                if processed_count - previous_print_frame >= config.print_every:
                    print(build_compact_status_line(phase.name, snapshot))
                    previous_print_frame = processed_count
            if result.trial_state in {
                TrialState.ENDED_BY_SUBJECT,
                TrialState.FAILED_TIMEOUT,
                TrialState.FAILED_TOO_MANY_DETACHES,
            }:
                run_stop_reason = result.trial_state.name.lower()
                break

        set_status(LiveSessionPhase.TRIAL_ENDED, f"Trial ended: {run_stop_reason}.", map_id=map_config.map_id)
        phase_at_stop = LiveSessionPhase.TRIAL_ENDED.name
    except _SessionAbort:
        pass
    except KeyboardInterrupt:
        run_stop_reason = "keyboard_interrupt"
        phase_at_stop = phase.name
        set_status(LiveSessionPhase.SAVING, "KeyboardInterrupt received; saving session.")
    except Exception as exc:
        run_stop_reason = "error"
        errors.append(str(exc))
        phase_at_stop = phase.name
        set_status(LiveSessionPhase.ERROR, str(exc))
    finally:
        set_status(LiveSessionPhase.SAVING, "Saving outputs.")
        stop_event.set()
        pump.stop(run_stop_reason)
        pump.join(timeout=1.0)
        raw_handle.close()
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
            logical_counts=logical_counts,
            latency_ms=latency_ms,
            calibration=calibration,
            calibration_warnings=warnings,
            map_warnings=map_warnings,
            map_anchor=map_anchor,
            trial_controller_started=trial_controller_started,
            errors=errors,
        )
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
    config = _config_from_args(args)
    result = run_live_integrated_session(config)
    print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    if result.summary.get("run_stop_reason") in {"completed", "max_frames", "duration_reached", "user_quit"}:
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
) -> None:
    set_status(LiveSessionPhase.WAITING_FOR_VALID_TRACKER, "Waiting for valid tracker frames.")
    while True:
        frame = buffer.get_frame(timeout=0.1)
        if frame is None:
            continue
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
        trial_timeout_seconds=1e9,
        max_detach_count=1_000_000_000,
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
            "haptic_hardware_enabled": False,
            "warnings": list(warnings) + list(map_warnings),
        }
    )
    return payload


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
    logical_counts: Counter[str],
    latency_ms: list[float],
    calibration: FormalCalibration | None,
    calibration_warnings: list[str],
    map_warnings: list[str],
    map_anchor: dict[str, Any],
    trial_controller_started: bool,
    errors: list[str],
) -> dict[str, Any]:
    stats = pump.stats_snapshot()
    buffer_stats = buffer.stats_snapshot()
    total_received = stats.get("total_received_frames")
    if total_received is None:
        total_received = buffer_stats.put_count
    return {
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
        "map_warnings": list(map_warnings),
        "map_anchor_mode": str(map_anchor.get("mode", "none")),
        "map_anchor": dict(map_anchor),
        "total_received_frames": int(total_received or 0),
        "total_processed_frames": int(total_processed_frames),
        "dropped_or_overwritten_frame_count": int(
            stats.get("dropped_frame_count", 0) or 0
        )
        + int(buffer_stats.overwritten_frame_count),
        "overwritten_frame_count": int(buffer_stats.overwritten_frame_count),
        "source_dropped_frame_count": int(stats.get("dropped_frame_count", 0) or 0),
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
    """Internal control-flow signal for expected pre-trial aborts."""


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
        timestamp_scale=config.timestamp_scale,
    )


def _metadata_value(sample: Any, key: str) -> Any:
    if sample is None:
        return None
    metadata = getattr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return None
    return metadata.get(key)


def _parse_vec3(value: str) -> list[float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3 or not all(math.isfinite(part) for part in parts):
        raise argparse.ArgumentTypeError("expected three finite comma-separated floats")
    return [float(part) for part in parts]


def _user_requested_quit() -> bool:
    try:
        import msvcrt

        if msvcrt.kbhit():
            key = msvcrt.getwch()
            return key.lower() == "q"
        return False
    except ImportError:
        return False


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
    parser.add_argument("--anchor-current-pinch-debug", action="store_true")
    parser.add_argument("--anchor-timeout-seconds", default=10.0, type=float)
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--socket-timeout", default=None, type=float)
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
    if args.task_z_half_extent <= 0.0:
        parser.error("--task-z-half-extent must be > 0.")
    if args.anchor_timeout_seconds <= 0.0:
        parser.error("--anchor-timeout-seconds must be > 0.")
    return args


def _config_from_args(args: argparse.Namespace) -> LiveIntegratedSessionConfig:
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
        anchor_current_pinch_debug=args.anchor_current_pinch_debug,
        anchor_timeout_seconds=args.anchor_timeout_seconds,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        timestamp_scale=args.timestamp_scale,
        socket_timeout=args.socket_timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
