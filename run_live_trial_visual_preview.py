"""MVP live visual preview trial runner.

This script is a small pilot entrypoint for collecting real MANUS/Vive data
with visual feedback. It intentionally avoids formal trial sequencing, real
haptic hardware, and core controller changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from block_controller import BlockController
from calibration_io import (
    build_task_coordinate_system_from_calibration,
    calibration_to_dict,
    load_calibration,
    validate_calibration,
)
from config import EngineConfig
from dashboard_snapshot import DashboardSnapshot, build_dashboard_snapshot
from device_frame_models import DeviceAdapterConfig
from live_raw_stream import LiveRawFrame, LiveRawStreamServer
from live_visual_display import NullLiveVisualDisplay, build_compact_status_line, create_live_visual_display
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
from simulated_live_source import RawJsonlSimulatedLiveSource
from trial_controller import ExperimentInputSample, TrialController, TrialState


LIVE_VISUAL_METRICS_HEADER = [
    "frame_index",
    "raw_timestamp",
    "receive_time_monotonic",
    "process_start_time_monotonic",
    "process_end_time_monotonic",
    "processing_latency_ms",
    "inter_frame_interval_ms",
    "parse_ok",
    "adapter_ok",
    "tracker_valid",
    "hand_valid",
    "pinch_valid",
    "pinch_distance",
    "sync_delta_ms",
    "queue_size",
    "dropped_frame_count",
    "error_message",
]


@dataclass(frozen=True)
class LiveTrialVisualPreviewConfig:
    """Configuration for the MVP live visual trial preview."""

    calibration_json: Path
    map_config: Path
    host: str = "127.0.0.1"
    port: int = 8888
    out_dir: Path = Path("data/live_trial_preview")
    session_dir: Path | None = None
    write_session: bool = True
    overwrite_session: bool = False
    max_frames: int | None = None
    duration_seconds: float | None = None
    print_every: int = 30
    show_visual: bool = True
    visual_mode: str = "matplotlib"
    visual_history: int = 300
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    timestamp_scale: float = 0.001
    trial_id: str = "live_visual_preview"
    subject_id: str | None = None
    notes: str | None = None
    pinch_grab_threshold: float | None = None
    pinch_release_threshold: float | None = None
    slip_motion_threshold: float | None = None
    ignore_task_z: bool = False
    task_z_half_extent: float = 5.0
    socket_timeout: float | None = None
    max_queue_size: int = 300
    raw_jsonl: Path | None = None
    simulate_live: bool = False
    replay_real_time: bool = False
    speed: float = 1.0


@dataclass(frozen=True)
class LiveTrialVisualPreviewResult:
    """Outputs from one MVP live visual preview run."""

    summary: dict[str, Any]
    metrics: list[dict[str, Any]]
    snapshots: list[DashboardSnapshot]


def run_live_trial_visual_preview(
    config: LiveTrialVisualPreviewConfig,
    *,
    source: Any | None = None,
    display: Any | None = None,
) -> LiveTrialVisualPreviewResult:
    """Run the live visual preview with a real or injected live raw source."""

    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "live_metrics.csv"
    raw_path = out_dir / "raw_frames.jsonl"
    summary_path = out_dir / "summary.json"

    calibration = load_calibration(config.calibration_json)
    calibration_validation = validate_calibration(calibration)
    if calibration_validation.errors:
        raise ValueError(
            "calibration validation failed: " + "; ".join(calibration_validation.errors)
        )
    task_system = build_task_coordinate_system_from_calibration(calibration)

    map_config = _map_config_for_live_preview(load_map_config(config.map_config), config)
    map_validation = validate_map_config(map_config)
    if map_validation.errors:
        raise ValueError("map validation failed: " + "; ".join(map_validation.errors))
    track_region, block_center, block_size = compile_map_to_track_region(map_config)
    engine_config = _engine_config(config, block_size)
    scene_payload = _scene_payload(config, map_config, map_validation.warnings, engine_config)

    def block_factory() -> BlockController:
        return BlockController(engine_config, track_region, block_center)

    adapter_config = DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    trial = TrialController(block_factory, task_system, engine_config)

    own_source = source is None
    live_source = source or _build_live_source(config)
    visual_display = display
    if visual_display is None:
        visual_display = create_live_visual_display(
            show_visual=config.show_visual,
            visual_mode=config.visual_mode,
            map_config=map_config,
            visual_history=config.visual_history,
            print_every=config.print_every,
        )

    raw_handle = raw_path.open("w", encoding="utf-8")
    _write_metrics_header(metrics_path)
    session_recorder = None
    session_dir = config.session_dir or _default_session_dir(out_dir)
    if config.write_session:
        session_recorder = SessionRecorder(session_dir, overwrite=config.overwrite_session)
        session_recorder.start_session(
            session_meta=_session_meta(config, session_dir, calibration),
            calibration=calibration_to_dict(calibration),
            trial_config=scene_payload,
        )

    metrics: list[dict[str, Any]] = []
    snapshots: list[DashboardSnapshot] = []
    run_started = time.monotonic()
    previous_receive_time: float | None = None
    run_stop_reason = "completed"
    raw_parser_error_count = 0
    adapter_error_count = 0
    tracker_invalid_count = 0
    hand_invalid_count = 0
    pinch_valid_count = 0
    large_delta_count = 0
    slip_active_count = 0
    blocked_count = 0
    processing_latencies: list[float] = []
    receive_times: list[float] = []
    logical_counts: Counter[str] = Counter()
    trial_started = False

    try:
        if hasattr(live_source, "start"):
            live_source.start()

        while True:
            if config.duration_seconds is not None and time.monotonic() - run_started >= config.duration_seconds:
                run_stop_reason = "duration_reached"
                break
            if config.max_frames is not None and len(metrics) >= config.max_frames:
                run_stop_reason = "max_frames"
                break

            live_frame = live_source.get_frame(timeout=0.1)
            if live_frame is None:
                source_reason = _source_stop_reason(live_source)
                if source_reason in {"client_disconnected", "server_stopped", "socket_error", "eof"}:
                    run_stop_reason = source_reason
                    break
                if _source_is_stopped(live_source):
                    run_stop_reason = source_reason or "source_stopped"
                    break
                continue

            process_start = time.monotonic()
            receive_times.append(float(live_frame.receive_time_monotonic))
            raw_handle.write(json.dumps(live_frame.raw_frame, ensure_ascii=False))
            raw_handle.write("\n")
            raw_handle.flush()
            if session_recorder is not None:
                session_recorder.record_raw_frame(live_frame.frame_index, live_frame.raw_frame)

            row, parse_ok, adapter_ok, device_frame, sample, error_message = _parse_and_adapt(
                live_frame,
                adapter_config,
                adapter,
                live_source,
                previous_receive_time,
                process_start,
            )
            previous_receive_time = live_frame.receive_time_monotonic

            snapshot = None
            trial_result = None
            if parse_ok and device_frame is not None and session_recorder is not None:
                session_recorder.record_device_frame(live_frame.frame_index, device_frame)
            if parse_ok and adapter_ok and sample is not None and device_frame is not None:
                if not trial_started:
                    trial.start_trial(sample.time, config.trial_id)
                    trial_started = True
                    if session_recorder is not None:
                        session_recorder.record_events(
                            live_frame.frame_index,
                            sample.time,
                            trial.event_history[-2:],
                        )
                trial_result = trial.update(sample)
                process_end = time.monotonic()
                processing_latency_ms = (process_end - process_start) * 1000.0
                snapshot = build_dashboard_snapshot(
                    frame_index=live_frame.frame_index,
                    trial_result=trial_result,
                    sample=sample,
                    hand_valid=_hand_valid(device_frame),
                    map_id=map_config.map_id,
                    calibration_id=calibration.calibration_id,
                    processing_latency_ms=processing_latency_ms,
                    hardware_haptic_active=False,
                )
                snapshots.append(snapshot)
                logical_counts[snapshot.logical_haptic_label] += 1
                if snapshot.tracker_valid is False:
                    tracker_invalid_count += 1
                if snapshot.hand_valid is False:
                    hand_invalid_count += 1
                if snapshot.pinch_valid:
                    pinch_valid_count += 1
                if snapshot.large_delta:
                    large_delta_count += 1
                if snapshot.slip_active:
                    slip_active_count += 1
                if snapshot.stop_reason == "TRACK_BLOCKED" or snapshot.blocked_force_active:
                    blocked_count += 1
                processing_latencies.append(processing_latency_ms)

                row.update(
                    {
                        "process_end_time_monotonic": process_end,
                        "processing_latency_ms": processing_latency_ms,
                        "tracker_valid": snapshot.tracker_valid,
                        "hand_valid": snapshot.hand_valid,
                        "pinch_valid": snapshot.pinch_valid,
                        "pinch_distance": snapshot.pinch_distance,
                    }
                )
                if session_recorder is not None:
                    session_recorder.record_processed_frame(
                        live_frame.frame_index,
                        live_frame.raw_frame,
                        device_frame,
                        sample,
                        trial_result.frame_output,
                        haptic_state=trial_result.haptic_feedback_state,
                        extra={"input_source": "live_visual_preview", "trial_time": trial_result.time_since_prompt},
                    )
                    session_recorder.record_events(
                        live_frame.frame_index,
                        sample.time,
                        trial_result.events,
                    )
                    session_recorder.record_haptic(
                        live_frame.frame_index,
                        sample.time,
                        trial_result.haptic_feedback_state,
                        details={
                            "mode": "live_visual_preview",
                            "logical_haptic_label": snapshot.logical_haptic_label,
                            "feedback_label": snapshot.feedback_label,
                            "hardware_haptic_active": False,
                            "sent_to_hardware": False,
                        },
                    )
                visual_display.update(snapshot)
                if config.print_every > 0 and len(metrics) % config.print_every == 0:
                    print(f"[LIVE PREVIEW] {build_compact_status_line(snapshot)}")
                if trial_result.trial_state in {
                    TrialState.ENDED_BY_SUBJECT,
                    TrialState.FAILED_TIMEOUT,
                    TrialState.FAILED_TOO_MANY_DETACHES,
                }:
                    run_stop_reason = trial_result.trial_state.name.lower()
                    metrics.append(row)
                    _append_metric_row(metrics_path, row)
                    break
            else:
                process_end = time.monotonic()
                row.update(
                    {
                        "process_end_time_monotonic": process_end,
                        "processing_latency_ms": (process_end - process_start) * 1000.0,
                    }
                )
                if not parse_ok:
                    raw_parser_error_count += 1
                elif not adapter_ok:
                    adapter_error_count += 1
                row["error_message"] = error_message

            metrics.append(row)
            _append_metric_row(metrics_path, row)
    except KeyboardInterrupt:
        run_stop_reason = "keyboard_interrupt"
    finally:
        raw_handle.close()
        try:
            visual_display.close()
        except Exception:
            pass
        if own_source:
            _stop_source(live_source, run_stop_reason or "server_stopped")
            if hasattr(live_source, "join"):
                live_source.join(timeout=1.0)

    stats = _source_stats(live_source)
    total_received = int(stats.get("total_received_frames") or len(metrics))
    summary = {
        "mode": "live_visual_preview",
        "is_live_trial": True,
        "is_formal_experiment": False,
        "trial_controller_started": trial_started,
        "trial_id": config.trial_id,
        "subject_id": config.subject_id,
        "calibration_type": calibration.calibration_type,
        "calibration_id": calibration.calibration_id,
        "scene_type": "map_config",
        "map_id": map_config.map_id,
        "haptic_hardware_enabled": False,
        "run_stop_reason": run_stop_reason,
        "session_dir": str(session_dir) if config.write_session else None,
        "total_received_frames": total_received,
        "total_processed_frames": len(metrics),
        "parse_error_count": int(stats.get("parse_error_count", 0)) + raw_parser_error_count,
        "adapter_error_count": adapter_error_count,
        "tracker_invalid_frame_count": tracker_invalid_count,
        "hand_invalid_frame_count": hand_invalid_count,
        "pinch_valid_frame_count": pinch_valid_count,
        "large_delta_frame_count": large_delta_count,
        "slip_active_frame_count": slip_active_count,
        "blocked_frame_count": blocked_count,
        "logical_haptic_label_counts": dict(logical_counts),
        "dropped_frame_count": int(stats.get("dropped_frame_count", 0)),
        "mean_processing_latency_ms": mean(processing_latencies) if processing_latencies else None,
        "max_processing_latency_ms": max(processing_latencies) if processing_latencies else None,
        "mean_receive_fps": _mean_receive_fps(receive_times),
        "engine_config": _engine_config_payload(engine_config),
        "calibration_validation_warnings": list(calibration_validation.warnings),
        "map_validation_warnings": list(map_validation.warnings),
        "warnings": [
            "MVP live visual preview: not a formal experiment runner.",
            "Logical haptic feedback is displayed and recorded; hardware haptic is disabled.",
            *_task_z_warnings(config),
            *calibration_validation.warnings,
            *map_validation.warnings,
        ],
        "task_z_mode": "ignore_expanded" if config.ignore_task_z else "map_config",
        "task_z_half_extent": config.task_z_half_extent if config.ignore_task_z else None,
    }
    _write_json(summary_path, summary)
    if session_recorder is not None:
        session_recorder.finalize(summary)
        _write_json(session_dir / "live_visual_summary.json", summary)
    return LiveTrialVisualPreviewResult(
        summary=summary,
        metrics=metrics,
        snapshots=snapshots,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = _parse_args(argv)
    config = LiveTrialVisualPreviewConfig(
        calibration_json=Path(args.calibration_json),
        map_config=Path(args.map_config),
        host=args.host,
        port=args.port,
        out_dir=Path(args.out_dir),
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        write_session=args.write_session,
        overwrite_session=args.overwrite_session,
        max_frames=args.max_frames,
        duration_seconds=args.duration_seconds,
        print_every=args.print_every,
        show_visual=args.show_visual,
        visual_mode=args.visual_mode,
        visual_history=args.visual_history,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        timestamp_scale=args.timestamp_scale,
        trial_id=args.trial_id,
        subject_id=args.subject_id,
        notes=args.notes,
        pinch_grab_threshold=args.pinch_grab_threshold,
        pinch_release_threshold=args.pinch_release_threshold,
        slip_motion_threshold=args.slip_motion_threshold,
        ignore_task_z=args.ignore_task_z,
        task_z_half_extent=args.task_z_half_extent,
        socket_timeout=args.socket_timeout,
        max_queue_size=args.max_queue_size,
        raw_jsonl=Path(args.raw_jsonl) if args.raw_jsonl is not None else None,
        simulate_live=args.simulate_live,
        replay_real_time=args.replay_real_time,
        speed=args.speed,
    )
    result = run_live_trial_visual_preview(config)
    print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    if result.summary.get("run_stop_reason") == "keyboard_interrupt":
        print("[LIVE PREVIEW] interrupted by user")
        return 130
    return 0


def _parse_and_adapt(
    live_frame: LiveRawFrame,
    adapter_config: DeviceAdapterConfig,
    adapter: ManusViveExperimentAdapter,
    source: Any,
    previous_receive_time: float | None,
    process_start: float,
) -> tuple[dict[str, Any], bool, bool, Any | None, ExperimentInputSample | None, str]:
    raw = live_frame.raw_frame
    device_frame = None
    sample = None
    parse_ok = False
    adapter_ok = False
    error_message = ""
    try:
        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        parse_ok = True
        sample = adapter.to_experiment_input_sample(device_frame)
        adapter_ok = True
    except Exception as exc:
        error_message = str(exc)

    inter_frame_interval_ms = (
        (live_frame.receive_time_monotonic - previous_receive_time) * 1000.0
        if previous_receive_time is not None
        else ""
    )
    hand_valid = _hand_valid(device_frame)
    row = {
        "frame_index": live_frame.frame_index,
        "raw_timestamp": raw.get("timestamp", ""),
        "receive_time_monotonic": live_frame.receive_time_monotonic,
        "process_start_time_monotonic": process_start,
        "process_end_time_monotonic": "",
        "processing_latency_ms": "",
        "inter_frame_interval_ms": inter_frame_interval_ms,
        "parse_ok": parse_ok,
        "adapter_ok": adapter_ok,
        "tracker_valid": getattr(sample, "tracker_valid", "") if sample is not None else "",
        "hand_valid": hand_valid if device_frame is not None else "",
        "pinch_valid": _metadata_value(sample, "pinch_valid"),
        "pinch_distance": getattr(sample, "pinch_distance", "") if sample is not None else "",
        "sync_delta_ms": getattr(device_frame, "sync_delta_ms", "") if device_frame is not None else "",
        "queue_size": _source_queue_size(source),
        "dropped_frame_count": _source_dropped_count(source),
        "error_message": error_message,
    }
    return row, parse_ok, adapter_ok, device_frame, sample, error_message


def _build_live_source(config: LiveTrialVisualPreviewConfig) -> Any:
    if config.raw_jsonl is not None:
        return RawJsonlSimulatedLiveSource(
            config.raw_jsonl,
            timestamp_scale=config.timestamp_scale,
            real_time=config.replay_real_time,
            speed=config.speed,
            max_frames=config.max_frames,
        )
    return LiveRawStreamServer(
        host=config.host,
        port=config.port,
        socket_timeout=config.socket_timeout,
        max_queue_size=config.max_queue_size,
    )


def _default_session_dir(out_dir: Path) -> Path:
    base = out_dir / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = out_dir / f"{base.name}_{index:03d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate a unique session directory.")


def _engine_config(config: LiveTrialVisualPreviewConfig, block_size: Any) -> EngineConfig:
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


def _map_config_for_live_preview(map_config: MapConfig, config: LiveTrialVisualPreviewConfig) -> MapConfig:
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
        track_boxes=[
            _expand_box_task_z(box, z_min=z_min, z_max=z_max)
            for box in map_config.track_boxes
        ],
        target_region=(
            _expand_box_task_z(map_config.target_region, z_min=z_min, z_max=z_max)
            if map_config.target_region is not None
            else None
        ),
        metadata={
            **map_config.metadata,
            "live_preview_task_z_mode": "ignore_expanded",
            "live_preview_task_z_half_extent": float(half_extent),
        },
    )


def _expand_box_task_z(box: MapBoxSpec, *, z_min: float, z_max: float) -> MapBoxSpec:
    minimum = list(box.min)
    maximum = list(box.max)
    minimum[2] = float(z_min)
    maximum[2] = float(z_max)
    return replace(
        box,
        min=minimum,
        max=maximum,
        metadata={
            **box.metadata,
            "live_preview_task_z_mode": "ignore_expanded",
        },
    )


def _scene_payload(
    config: LiveTrialVisualPreviewConfig,
    map_config: Any,
    map_warnings: list[str],
    engine_config: EngineConfig,
) -> dict[str, Any]:
    payload = map_config_to_trial_config(map_config)
    payload.update(
        {
            "mode": "live_visual_preview",
            "scene_type": "map_config",
            "map_config_path": str(config.map_config),
            "haptic_hardware_enabled": False,
            "pinch_threshold": {
                "grab": engine_config.pinch_grab_threshold,
                "release": engine_config.pinch_release_threshold,
            },
            "task_z_mode": "ignore_expanded" if config.ignore_task_z else "map_config",
            "task_z_half_extent": config.task_z_half_extent if config.ignore_task_z else None,
            "slip_motion_threshold": engine_config.slip_motion_threshold,
            "trial_timeout_seconds": engine_config.trial_timeout_seconds,
            "max_detach_count": engine_config.max_detach_count,
            "warnings": [*_task_z_warnings(config), *map_warnings],
        }
    )
    return payload


def _task_z_warnings(config: LiveTrialVisualPreviewConfig) -> list[str]:
    if not config.ignore_task_z:
        return []
    return [
        "Live preview is running with --ignore-task-z: map/track/block z ranges are expanded for MVP x-y testing."
    ]


def _session_meta(
    config: LiveTrialVisualPreviewConfig,
    session_dir: Path,
    calibration: Any,
) -> dict[str, Any]:
    payload = {
        "session_id": session_dir.name,
        "mode": "live_visual_preview",
        "trial_id": config.trial_id,
        "is_live_trial": True,
        "is_formal_experiment": False,
        "calibration_type": calibration.calibration_type,
        "calibration_id": calibration.calibration_id,
        "scene_type": "map_config",
        "haptic_hardware_enabled": False,
        "warnings": [
            "MVP live visual preview: not a formal experiment runner.",
            "Hardware haptic is disabled; only logical haptic feedback is recorded.",
        ],
    }
    if config.subject_id is not None:
        payload["subject_id"] = config.subject_id
    if config.notes is not None:
        payload["notes"] = config.notes
    return payload


def _write_metrics_header(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_VISUAL_METRICS_HEADER)
        writer.writeheader()


def _append_metric_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_VISUAL_METRICS_HEADER)
        writer.writerow({key: _csv_value(row.get(key, "")) for key in LIVE_VISUAL_METRICS_HEADER})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _metadata_value(sample: Any, key: str) -> Any:
    if sample is None:
        return ""
    metadata = getattr(sample, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return ""
    return metadata.get(key, "")


def _hand_valid(device_frame: Any) -> bool:
    hand = getattr(device_frame, "hand", None) if device_frame is not None else None
    return bool(getattr(hand, "valid", False))


def _engine_config_payload(engine_config: EngineConfig) -> dict[str, Any]:
    return {
        "block_size": [
            engine_config.block_size_x,
            engine_config.block_size_y,
            engine_config.block_size_z,
        ],
        "pinch_grab_threshold": engine_config.pinch_grab_threshold,
        "pinch_release_threshold": engine_config.pinch_release_threshold,
        "slip_motion_threshold": engine_config.slip_motion_threshold,
        "trial_timeout_seconds": engine_config.trial_timeout_seconds,
        "max_detach_count": engine_config.max_detach_count,
    }


def _mean_receive_fps(receive_times: list[float]) -> float | None:
    if len(receive_times) < 2:
        return None
    elapsed = receive_times[-1] - receive_times[0]
    if elapsed <= 0.0:
        return None
    return float((len(receive_times) - 1) / elapsed)


def _source_stats(source: Any) -> dict[str, Any]:
    if hasattr(source, "stats_snapshot"):
        snapshot = source.stats_snapshot()
        if isinstance(snapshot, dict):
            return dict(snapshot)
        if hasattr(snapshot, "__dict__"):
            return dict(snapshot.__dict__)
    return {
        "total_received_frames": getattr(source, "total_received_frames", None),
        "parse_error_count": getattr(source, "parse_error_count", 0),
        "bad_json_line_count": getattr(source, "bad_json_line_count", 0),
        "dropped_frame_count": getattr(source, "dropped_frame_count", 0),
        "stop_reason": getattr(source, "stop_reason", None),
    }


def _source_queue_size(source: Any) -> int | str:
    if hasattr(source, "queue_size"):
        return source.queue_size()
    return ""


def _source_dropped_count(source: Any) -> int:
    return int(getattr(source, "dropped_frame_count", 0))


def _source_stop_reason(source: Any) -> str | None:
    value = getattr(source, "stop_reason", None)
    if callable(value):
        return value()
    return value


def _source_is_stopped(source: Any) -> bool:
    if hasattr(source, "stop_event"):
        return bool(source.stop_event.is_set())
    return bool(getattr(source, "stopped", False))


def _stop_source(source: Any, reason: str) -> None:
    if hasattr(source, "stop"):
        source.stop(reason)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MVP live visual trial preview.")
    parser.add_argument("--calibration-json", required=True)
    parser.add_argument("--map-config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8888, type=int)
    parser.add_argument("--out-dir", default="data/live_trial_preview")
    parser.add_argument("--session-dir", default=None)
    parser.set_defaults(write_session=True)
    parser.add_argument("--write-session", dest="write_session", action="store_true")
    parser.add_argument("--no-write-session", dest="write_session", action="store_false")
    parser.add_argument("--overwrite-session", action="store_true")
    parser.add_argument("--max-frames", default=None, type=int)
    parser.add_argument("--duration-seconds", default=None, type=float)
    parser.add_argument("--print-every", default=30, type=int)
    parser.set_defaults(show_visual=True)
    parser.add_argument("--show-visual", dest="show_visual", action="store_true")
    parser.add_argument("--no-show-visual", dest="show_visual", action="store_false")
    parser.add_argument("--visual-mode", choices=("matplotlib", "text"), default="matplotlib")
    parser.add_argument("--visual-history", default=300, type=int)
    parser.add_argument("--thumb-node", default=4, type=int)
    parser.add_argument("--index-node", default=9, type=int)
    parser.add_argument("--tracker-index", default=0, type=int)
    parser.add_argument("--skeleton-index", default=0, type=int)
    parser.add_argument("--timestamp-scale", default=0.001, type=float)
    parser.add_argument("--trial-id", default="live_visual_preview")
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--pinch-grab-threshold", default=None, type=float)
    parser.add_argument("--pinch-release-threshold", default=None, type=float)
    parser.add_argument("--slip-motion-threshold", default=None, type=float)
    parser.add_argument("--ignore-task-z", action="store_true")
    parser.add_argument("--task-z-half-extent", default=5.0, type=float)
    parser.add_argument("--socket-timeout", default=None, type=float)
    parser.add_argument("--max-queue-size", default=300, type=int)
    parser.add_argument("--raw-jsonl", default=None)
    parser.add_argument("--simulate-live", action="store_true")
    parser.add_argument("--replay-real-time", action="store_true")
    parser.add_argument("--speed", default=1.0, type=float)
    args = parser.parse_args(argv)
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error("--max-frames must be > 0.")
    if args.duration_seconds is not None and args.duration_seconds <= 0:
        parser.error("--duration-seconds must be > 0.")
    if args.print_every < 0:
        parser.error("--print-every must be >= 0.")
    if args.visual_history <= 0:
        parser.error("--visual-history must be > 0.")
    if args.max_queue_size <= 0:
        parser.error("--max-queue-size must be > 0.")
    if args.raw_jsonl is not None and not args.simulate_live:
        parser.error("--raw-jsonl requires --simulate-live.")
    if args.simulate_live and args.raw_jsonl is None:
        parser.error("--simulate-live requires --raw-jsonl.")
    if args.speed <= 0.0:
        parser.error("--speed must be > 0.")
    if args.pinch_grab_threshold is not None and args.pinch_grab_threshold <= 0:
        parser.error("--pinch-grab-threshold must be > 0.")
    if args.pinch_release_threshold is not None and args.pinch_release_threshold <= 0:
        parser.error("--pinch-release-threshold must be > 0.")
    if args.slip_motion_threshold is not None and args.slip_motion_threshold < 0:
        parser.error("--slip-motion-threshold must be >= 0.")
    if args.task_z_half_extent <= 0.0:
        parser.error("--task-z-half-extent must be > 0.")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
