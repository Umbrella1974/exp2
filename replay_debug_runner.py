"""Replay runner that feeds DashboardSnapshot into the debug GUI.

The GUI never reads raw JSONL directly. This module owns replay input loading
and reuses LiveTrialRunner so parser/adapter/controller semantics stay shared.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from calibration_io import (
    FORMAL_CALIBRATION_TYPE,
    build_task_coordinate_system_from_calibration,
    calibration_from_dict,
)
from config import EngineConfig
from cue_config import CueConfig, default_cue_config, load_cue_config
from cue_feedback import CUE_SINK_CHOICES, CueRuntime, CueSinkConfig
from data_models import Box3D, TrackRegion, Vec3
from debug_view_model import DebugSceneView, scene_view_from_trial_config
from haptic_runtime import disabled_haptic_summary
from latest_frame_buffer import LatestFrameBuffer
from latest_snapshot_store import LatestSnapshotStore
from live_raw_stream import LiveRawFrame
from live_trial_runner import LiveTrialRunner, LiveTrialRunnerConfig
from raw_frame_source import JsonlRawFrameSource
from task_coordinate_system import TaskCoordinateSystem
from timing_diagnostics import TimingDiagnostics
from visual_profile import resolve_visual_profile
from pinch_threshold_calibration import (
    effective_pinch_threshold_payload,
    threshold_values_from_payload,
    validate_pinch_threshold_values,
)


SnapshotCallback = Callable[[Any], None]


@dataclass(frozen=True)
class ReplayDebugConfig:
    """Configuration for replay debug runner."""

    session_dir: Path | None = None
    raw_jsonl: Path | None = None
    calibration_json: Path | None = None
    trial_config_json: Path | None = None
    out_dir: Path | None = None
    max_frames: int | None = None
    replay_timing: str = "raw"
    replay_fps: float = 60.0
    replay_speed: float = 1.0
    gui_fps: float = 30.0
    timestamp_scale: float = 0.001
    thumb_node: int | None = None
    index_node: int | None = None
    tracker_index: int | None = None
    skeleton_index: int | None = None
    pinch_position_mode: str | None = None
    pinch_grab_threshold: float | None = None
    pinch_release_threshold: float | None = None
    no_frame_timeout_seconds: float = 5.0
    cue_sink: str = "logging"
    cue_config_path: Path | None = None
    cue_config: CueConfig | None = None
    visual_profile: str = "debug_all"
    status_panel: str = "auto"
    show_axes: str = "auto"
    show_grid: str = "auto"


@dataclass(frozen=True)
class ReplayDebugInputs:
    """Resolved replay input paths and payloads."""

    raw_jsonl: Path
    calibration_json: Path
    trial_config_json: Path
    calibration_payload: dict[str, Any]
    trial_config: dict[str, Any]
    session_meta: dict[str, Any]
    pinch_threshold_calibration: dict[str, Any]
    scene: DebugSceneView
    cue_config: CueConfig
    cue_config_path: Path | None


@dataclass(frozen=True)
class ReplayDebugResult:
    """Replay debug run result."""

    summary: dict[str, Any]
    scene: DebugSceneView
    last_snapshot: Any | None
    snapshot_count: int
    timing_diagnostics: TimingDiagnostics
    cue_records: tuple[dict[str, Any], ...]
    cue_runtime: CueRuntime


def load_replay_debug_inputs(config: ReplayDebugConfig) -> ReplayDebugInputs:
    """Resolve and validate replay input files."""

    raw_path, calibration_path, trial_config_path, session_meta_path = _resolve_input_paths(config)
    calibration_payload = _read_json(calibration_path, "calibration")
    trial_config = _read_json(trial_config_path, "trial config")
    session_meta = _read_optional_json(session_meta_path) if session_meta_path is not None else {}
    pinch_threshold_calibration = _read_optional_json(
        Path(config.session_dir) / "pinch_threshold_calibration.json"
    ) if config.session_dir is not None else {}
    scene = scene_view_from_trial_config(trial_config)
    cue_config, cue_config_path = _resolve_replay_cue_config(config)
    if not scene.track_boxes:
        raise ValueError("trial_config does not contain usable track geometry for replay.")
    _task_system_from_calibration_payload(calibration_payload)
    _track_region_from_trial_config(trial_config)
    _block_center_from_trial_config(trial_config)
    _block_size_from_trial_config(trial_config)
    return ReplayDebugInputs(
        raw_jsonl=raw_path,
        calibration_json=calibration_path,
        trial_config_json=trial_config_path,
        calibration_payload=calibration_payload,
        trial_config=trial_config,
        session_meta=session_meta,
        pinch_threshold_calibration=pinch_threshold_calibration,
        scene=scene,
        cue_config=cue_config,
        cue_config_path=cue_config_path,
    )


def run_replay_debug(
    config: ReplayDebugConfig,
    *,
    snapshot_store: LatestSnapshotStore | None = None,
    snapshot_callback: SnapshotCallback | None = None,
    timing_diagnostics: TimingDiagnostics | None = None,
    cue_runtime: CueRuntime | None = None,
    stop_event: threading.Event | None = None,
) -> ReplayDebugResult:
    """Replay raw frames through LiveTrialRunner and publish snapshots."""

    _validate_config(config)
    inputs = load_replay_debug_inputs(config)
    task_system = _task_system_from_calibration_payload(inputs.calibration_payload)
    track_region = _track_region_from_trial_config(inputs.trial_config)
    block_center = _block_center_from_trial_config(inputs.trial_config)
    block_size = _block_size_from_trial_config(inputs.trial_config)
    adapter_config_payload = _resolve_replay_adapter_config(config, inputs.trial_config, inputs.session_meta)
    pinch_threshold_summary = _resolve_replay_pinch_threshold_summary(
        config,
        inputs.trial_config,
        inputs.pinch_threshold_calibration,
    )
    engine_config = _engine_config_from_trial_config(
        inputs.trial_config,
        block_size,
        inputs.session_meta,
        pinch_threshold_summary=pinch_threshold_summary,
    )
    trial_id = str(inputs.trial_config.get("trial_id", inputs.session_meta.get("trial_id", "replay_debug")))
    cue_log_path = (
        config.out_dir / "cue_log.csv"
        if config.out_dir is not None and config.cue_sink != "none"
        else None
    )
    cue_runtime = cue_runtime or CueRuntime(
        trial_id=trial_id,
        cue_config=inputs.cue_config,
        sink_config=CueSinkConfig(
            cue_sink=config.cue_sink,
            mode="replay",
            is_live_cue_timing=False,
        ),
    )
    timing_diagnostics = timing_diagnostics or TimingDiagnostics(mode="replay", is_live_latency=False)
    latest_buffer = LatestFrameBuffer(
        frame_published_callback=lambda frame, published, overwritten: timing_diagnostics.record_frame_published(
            frame,
            phase="REPLAY",
            monotonic_time=published,
            overwritten_frame=overwritten,
        ),
        frame_consumed_callback=lambda frame, consumed: timing_diagnostics.record_frame_consumed(
            frame,
            phase="REPLAY",
            monotonic_time=consumed,
        ),
    )
    replay_state = {"raw_seen": 0, "snapshots": 0}

    def on_snapshot(snapshot: Any) -> None:
        replay_state["snapshots"] += 1
        if snapshot_store is not None:
            snapshot_store.publish(snapshot)
        if snapshot_callback is not None:
            snapshot_callback(snapshot)

    runner = LiveTrialRunner(
        latest_frame_buffer=latest_buffer,
        task_coordinate_system=task_system,
        track_region=track_region,
        block_initial_center_task=block_center,
        block_size=block_size,
        engine_config=engine_config,
        session_recorder=None,
        config=LiveTrialRunnerConfig(
            trial_id=trial_id,
            max_frames=None,
            no_frame_timeout_seconds=config.no_frame_timeout_seconds,
            timestamp_scale=config.timestamp_scale,
            thumb_node=adapter_config_payload["thumb_node"],
            index_node=adapter_config_payload["index_node"],
            tracker_index=adapter_config_payload["tracker_index"],
            skeleton_index=adapter_config_payload["skeleton_index"],
            pinch_position_mode=adapter_config_payload["pinch_position_mode"],
            timing_phase="REPLAY",
        ),
        map_id=str(inputs.trial_config.get("map_id", "")),
        calibration_id=str(inputs.calibration_payload.get("calibration_id", "")),
        trial_config={"mode": "replay_debug_gui", **inputs.trial_config},
        snapshot_callback=on_snapshot,
        source_stats_getter=lambda: {"total_received_frames": replay_state["raw_seen"]},
        timing_diagnostics=timing_diagnostics,
        cue_runtime=cue_runtime,
        cue_log_path=str(cue_log_path) if cue_log_path is not None else None,
    )

    previous_raw_time: float | None = None
    replay_stop_requested = False
    source = JsonlRawFrameSource(inputs.raw_jsonl)
    try:
        for raw_index, raw in enumerate(source):
            if stop_event is not None and stop_event.is_set():
                replay_stop_requested = True
                break
            if config.max_frames is not None and raw_index >= config.max_frames:
                break
            current_raw_time = _raw_time_seconds(raw, config.timestamp_scale)
            if _sleep_for_replay_timing(
                config,
                previous_raw_time,
                current_raw_time,
                stop_event=stop_event,
            ):
                replay_stop_requested = True
                break
            previous_raw_time = current_raw_time
            replay_state["raw_seen"] += 1
            latest_buffer.put(_live_frame_from_raw(raw_index, raw))
            runner.step_once()
            if runner.stats_snapshot().run_stop_reason != "running":
                break
    finally:
        source.close()

    if replay_state["raw_seen"] == 0 and not replay_stop_requested:
        raise ValueError(f"raw_jsonl did not contain any frames: {inputs.raw_jsonl}")
    if runner.stats_snapshot().run_stop_reason == "running":
        runner.request_stop("replay_stop_requested" if replay_stop_requested else "eof")
    cue_runtime.end_trial()
    summary = runner.build_summary()
    summary.update(
        {
            "mode": "replay_debug_gui",
            "raw_jsonl": str(inputs.raw_jsonl),
            "calibration_json": str(inputs.calibration_json),
            "trial_config_json": str(inputs.trial_config_json),
            "session_dir": str(config.session_dir) if config.session_dir is not None else None,
            "replay_timing": config.replay_timing,
            "replay_fps": config.replay_fps if config.replay_timing == "fixed" else None,
            "replay_speed": config.replay_speed,
            "gui_fps": config.gui_fps,
            "snapshot_count": replay_state["snapshots"],
            "gui_scene": inputs.scene.to_dict(),
            "requested_cue_config_path": (
                str(config.cue_config_path) if config.cue_config_path is not None else None
            ),
            **disabled_haptic_summary(),
            **adapter_config_payload,
            **pinch_threshold_summary,
            **resolve_visual_profile(
                config.visual_profile,
                status_panel=config.status_panel,
                show_axes=config.show_axes,
                show_grid=config.show_grid,
            ).to_dict(),
        }
    )
    summary.update(timing_diagnostics.summary())
    summary.update(cue_runtime.summary(cue_log_path=cue_log_path))
    timing_path: Path | None = None
    if config.out_dir is not None:
        config.out_dir.mkdir(parents=True, exist_ok=True)
        timing_path = timing_diagnostics.write_csv(config.out_dir / "timing_diagnostics.csv")
        summary["timing_diagnostics_path"] = str(timing_path)
        _write_replay_cue_outputs(
            config.out_dir,
            cue_runtime=cue_runtime,
            cue_config=inputs.cue_config,
            summary=summary,
        )
    else:
        summary["timing_diagnostics_path"] = None
    return ReplayDebugResult(
        summary=summary,
        scene=inputs.scene,
        last_snapshot=runner.last_snapshot,
        snapshot_count=replay_state["snapshots"],
        timing_diagnostics=timing_diagnostics,
        cue_records=cue_runtime.records_snapshot(),
        cue_runtime=cue_runtime,
    )


def finalize_replay_debug_outputs(result: ReplayDebugResult, out_dir: str | Path) -> None:
    """Rewrite final replay cue/summary outputs after GUI display has exited."""

    output = Path(out_dir)
    _write_replay_cue_outputs(
        output,
        cue_runtime=result.cue_runtime,
        cue_config=result.cue_runtime.cue_config,
        summary=result.summary,
    )


def _pinch_position_mode_for_replay(trial_config: dict[str, Any], session_meta: dict[str, Any]) -> str:
    explicit = trial_config.get("pinch_position_mode", session_meta.get("pinch_position_mode"))
    if explicit is not None:
        return str(explicit)
    mode = str(trial_config.get("mode", session_meta.get("mode", "")))
    if mode == "live_integrated_session":
        return "nodes_world"
    return "tracker_plus_local"


def _resolve_replay_adapter_config(
    config: ReplayDebugConfig,
    trial_config: dict[str, Any],
    session_meta: dict[str, Any],
) -> dict[str, Any]:
    pinch_node_config = trial_config.get("pinch_node_config")
    if not isinstance(pinch_node_config, dict):
        pinch_node_config = {}

    thumb_node = _first_int(
        config.thumb_node,
        pinch_node_config.get("thumb_node"),
        trial_config.get("thumb_node"),
        session_meta.get("thumb_node"),
        4,
    )
    index_node = _first_int(
        config.index_node,
        pinch_node_config.get("secondary_node"),
        trial_config.get("index_node"),
        session_meta.get("index_node"),
        9,
    )
    tracker_index = _first_int(
        config.tracker_index,
        pinch_node_config.get("tracker_index"),
        trial_config.get("tracker_index"),
        session_meta.get("tracker_index"),
        0,
    )
    skeleton_index = _first_int(
        config.skeleton_index,
        pinch_node_config.get("skeleton_index"),
        trial_config.get("skeleton_index"),
        session_meta.get("skeleton_index"),
        0,
    )
    pinch_position_mode = (
        config.pinch_position_mode
        or pinch_node_config.get("pinch_position_mode")
        or _pinch_position_mode_for_replay(trial_config, session_meta)
    )
    pinch_position_mode = str(pinch_position_mode)
    if pinch_position_mode not in {"nodes_world", "tracker_plus_local"}:
        raise ValueError("pinch_position_mode must be nodes_world or tracker_plus_local.")
    return {
        "thumb_node": thumb_node,
        "index_node": index_node,
        "tracker_index": tracker_index,
        "skeleton_index": skeleton_index,
        "pinch_position_mode": pinch_position_mode,
        "pinch_node_config": {
            "thumb_node": thumb_node,
            "secondary_node": index_node,
            "secondary_node_role": "index_node_cli_arg",
            "tracker_index": tracker_index,
            "skeleton_index": skeleton_index,
            "pinch_position_mode": pinch_position_mode,
        },
    }


def _resolve_replay_pinch_threshold_summary(
    config: ReplayDebugConfig,
    trial_config: dict[str, Any],
    pinch_threshold_calibration: dict[str, Any],
) -> dict[str, Any]:
    defaults = EngineConfig()
    source = "default"
    grab = defaults.pinch_grab_threshold
    release = defaults.pinch_release_threshold
    if pinch_threshold_calibration:
        grab, release = threshold_values_from_payload(pinch_threshold_calibration)
        source = "calibrated"
    else:
        session_thresholds = _threshold_values_from_session_config(trial_config)
        if session_thresholds is not None:
            grab, release = session_thresholds
            source = str(trial_config.get("pinch_threshold_source", "session"))

    if config.pinch_grab_threshold is not None:
        grab = float(config.pinch_grab_threshold)
        source = "cli"
    if config.pinch_release_threshold is not None:
        release = float(config.pinch_release_threshold)
        source = "cli"
    validate_pinch_threshold_values(grab, release)
    return effective_pinch_threshold_payload(
        pinch_on_threshold_m=grab,
        pinch_off_threshold_m=release,
        source=source,
    )


def _threshold_values_from_session_config(trial_config: dict[str, Any]) -> tuple[float, float] | None:
    if "pinch_on_threshold_m" in trial_config or "pinch_off_threshold_m" in trial_config:
        return threshold_values_from_payload(trial_config)
    effective = trial_config.get("effective_pinch_threshold")
    if isinstance(effective, dict):
        return threshold_values_from_payload({"effective_pinch_threshold": effective})
    threshold = trial_config.get("pinch_threshold")
    if isinstance(threshold, dict):
        return threshold_values_from_payload({"pinch_threshold": threshold})
    return None


def _first_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError("adapter node/index values must be integers.")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("adapter node/index values must be integers.")
        return int(value)
    raise ValueError("missing adapter node/index value.")


def _resolve_replay_cue_config(config: ReplayDebugConfig) -> tuple[CueConfig, Path | None]:
    if config.cue_config is not None:
        return config.cue_config, config.cue_config_path
    if config.cue_config_path is not None:
        return load_cue_config(config.cue_config_path), config.cue_config_path
    if config.session_dir is not None:
        session_cue_config = Path(config.session_dir) / "cue_config.json"
        if session_cue_config.exists():
            return load_cue_config(session_cue_config), session_cue_config
    return default_cue_config(), None


def _resolve_input_paths(config: ReplayDebugConfig) -> tuple[Path, Path, Path, Path | None]:
    if config.session_dir is not None:
        session_dir = Path(config.session_dir)
        raw = Path(config.raw_jsonl) if config.raw_jsonl is not None else session_dir / "raw_frames.jsonl"
        calibration = (
            Path(config.calibration_json)
            if config.calibration_json is not None
            else session_dir / "calibration.json"
        )
        trial_config = (
            Path(config.trial_config_json)
            if config.trial_config_json is not None
            else session_dir / "trial_config.json"
        )
        meta = session_dir / "session_meta.json"
    else:
        raw = Path(config.raw_jsonl) if config.raw_jsonl is not None else None
        calibration = Path(config.calibration_json) if config.calibration_json is not None else None
        trial_config = Path(config.trial_config_json) if config.trial_config_json is not None else None
        meta = None
    missing = []
    for label, path in (
        ("raw_jsonl", raw),
        ("calibration_json", calibration),
        ("trial_config_json", trial_config),
    ):
        if path is None:
            missing.append(label)
        elif not path.exists():
            missing.append(f"{label} not found: {path}")
    if missing:
        message = "Missing replay input: " + "; ".join(missing)
        if any(item.startswith("trial_config_json") for item in missing):
            message += (
                ". Older sessions without trial_config.json must provide the original "
                "trial config or MapConfig JSON with --trial-config-json."
            )
        raise FileNotFoundError(message)
    assert raw is not None and calibration is not None and trial_config is not None
    if meta is not None and not meta.exists():
        meta = None
    return raw, calibration, trial_config, meta


def _validate_config(config: ReplayDebugConfig) -> None:
    if config.session_dir is None:
        if config.raw_jsonl is None or config.calibration_json is None or config.trial_config_json is None:
            raise ValueError("raw_jsonl, calibration_json, and trial_config_json are required without session_dir.")
    if config.replay_timing not in {"raw", "fixed", "fast"}:
        raise ValueError("replay_timing must be raw, fixed, or fast.")
    if config.replay_fps <= 0.0:
        raise ValueError("replay_fps must be > 0.")
    if config.replay_speed <= 0.0:
        raise ValueError("replay_speed must be > 0.")
    if config.gui_fps <= 0.0:
        raise ValueError("gui_fps must be > 0.")
    if config.cue_sink not in CUE_SINK_CHOICES:
        raise ValueError("cue_sink must be one of: " + ", ".join(CUE_SINK_CHOICES))
    resolve_visual_profile(
        config.visual_profile,
        status_panel=config.status_panel,
        show_axes=config.show_axes,
        show_grid=config.show_grid,
    )


def _task_system_from_calibration_payload(payload: dict[str, Any]) -> TaskCoordinateSystem:
    if payload.get("calibration_type") == FORMAL_CALIBRATION_TYPE:
        return build_task_coordinate_system_from_calibration(calibration_from_dict(payload))
    task_payload = payload.get("task_coordinate_system")
    if isinstance(task_payload, dict):
        return TaskCoordinateSystem(
            origin_world=_required_vec3(task_payload.get("origin_world"), "task_coordinate_system.origin_world"),
            x_axis_world=_required_vec3(task_payload.get("x_axis_world"), "task_coordinate_system.x_axis_world"),
            y_axis_world=_required_vec3(task_payload.get("y_axis_world"), "task_coordinate_system.y_axis_world"),
            z_axis_world=_required_vec3(task_payload.get("z_axis_world"), "task_coordinate_system.z_axis_world"),
        )
    auto_payload = payload.get("calibration_auto")
    if isinstance(auto_payload, dict):
        return _task_system_from_axes_payload(auto_payload, prefix="calibration_auto")
    return _task_system_from_axes_payload(payload, prefix="calibration")


def _task_system_from_axes_payload(payload: dict[str, Any], *, prefix: str) -> TaskCoordinateSystem:
    return TaskCoordinateSystem(
        origin_world=_required_vec3(payload.get("origin_world"), f"{prefix}.origin_world"),
        x_axis_world=_required_vec3(payload.get("x_axis_world"), f"{prefix}.x_axis_world"),
        y_axis_world=_required_vec3(payload.get("y_axis_world"), f"{prefix}.y_axis_world"),
        z_axis_world=_required_vec3(payload.get("z_axis_world"), f"{prefix}.z_axis_world"),
    )


def _track_region_from_trial_config(trial_config: dict[str, Any]) -> TrackRegion:
    boxes: list[Box3D] = []
    for index, box_payload in enumerate(trial_config.get("track_boxes", []) or []):
        if isinstance(box_payload, dict):
            boxes.append(_box3d_from_min_max(box_payload, f"track_boxes[{index}]"))
    if not boxes:
        bounds = _track_bounds_payload(trial_config)
        if bounds is not None:
            boxes.append(_box3d_from_min_max(bounds, "track_bounds"))
    if not boxes:
        raise ValueError("trial_config must contain track_boxes or track_bounds_task/scene_auto.track_bounds.")
    return TrackRegion(boxes=tuple(boxes))


def _track_bounds_payload(trial_config: dict[str, Any]) -> dict[str, Any] | None:
    for value in (
        trial_config.get("track_bounds_task"),
        trial_config.get("track_bounds"),
        trial_config.get("bounds"),
        _nested_get(trial_config, ("scene_auto", "track_bounds")),
    ):
        if isinstance(value, dict) and "min" in value and "max" in value:
            return value
    return None


def _box3d_from_min_max(payload: dict[str, Any], label: str) -> Box3D:
    minimum = _required_vec3(payload.get("min"), f"{label}.min")
    maximum = _required_vec3(payload.get("max"), f"{label}.max")
    size = [maximum[index] - minimum[index] for index in range(3)]
    if any(component <= 0.0 for component in size):
        raise ValueError(f"{label}.min must be < max on every axis.")
    center = [(minimum[index] + maximum[index]) * 0.5 for index in range(3)]
    return Box3D(center=_vec3(center), size=_vec3(size))


def _block_center_from_trial_config(trial_config: dict[str, Any]) -> Vec3:
    value = trial_config.get("block_initial_center_task")
    if value is None:
        value = _nested_get(trial_config, ("scene_auto", "block_center_task"))
    return _vec3(_required_vec3(value, "block_initial_center_task"))


def _block_size_from_trial_config(trial_config: dict[str, Any]) -> Vec3:
    value = trial_config.get("block_size")
    if isinstance(value, int | float):
        return Vec3(float(value), float(value), float(value))
    return _vec3(_required_vec3(value, "block_size"))


def _engine_config_from_trial_config(
    trial_config: dict[str, Any],
    block_size: Vec3,
    session_meta: dict[str, Any],
    *,
    pinch_threshold_summary: dict[str, Any] | None = None,
) -> EngineConfig:
    defaults = EngineConfig()
    if pinch_threshold_summary is None:
        pinch_threshold_summary = _resolve_replay_pinch_threshold_summary(
            ReplayDebugConfig(),
            trial_config,
            {},
        )
    mode = str(session_meta.get("mode", trial_config.get("mode", "")))
    default_max_delta = 10.0 if mode.startswith("offline_") else defaults.max_hand_delta_per_frame
    return EngineConfig(
        block_size_x=block_size.x,
        block_size_y=block_size.y,
        block_size_z=block_size.z,
        pinch_grab_threshold=float(pinch_threshold_summary["pinch_on_threshold_m"]),
        pinch_release_threshold=float(pinch_threshold_summary["pinch_off_threshold_m"]),
        slip_motion_threshold=float(trial_config.get("slip_motion_threshold", defaults.slip_motion_threshold)),
        trial_timeout_seconds=float(trial_config.get("trial_timeout_seconds", 1e9)),
        max_detach_count=int(trial_config.get("max_detach_count", 1_000_000_000)),
        max_hand_delta_per_frame=float(trial_config.get("max_hand_delta_per_frame", default_max_delta)),
    )


def _sleep_for_replay_timing(
    config: ReplayDebugConfig,
    previous_raw_time: float | None,
    current_raw_time: float | None,
    *,
    stop_event: threading.Event | None = None,
) -> bool:
    if config.replay_timing == "fast":
        return bool(stop_event is not None and stop_event.is_set())
    if config.replay_timing == "fixed":
        return _interruptible_sleep(1.0 / config.replay_fps, stop_event)
    if previous_raw_time is None or current_raw_time is None:
        return bool(stop_event is not None and stop_event.is_set())
    delay = max(0.0, current_raw_time - previous_raw_time) / config.replay_speed
    return _interruptible_sleep(delay, stop_event)


def _interruptible_sleep(delay: float, stop_event: threading.Event | None) -> bool:
    if delay <= 0.0:
        return bool(stop_event is not None and stop_event.is_set())
    if stop_event is None:
        time.sleep(delay)
        return False
    return stop_event.wait(delay)


def _live_frame_from_raw(frame_index: int, raw: dict[str, Any]) -> LiveRawFrame:
    return LiveRawFrame(
        frame_index=frame_index,
        raw_frame=raw,
        receive_time_monotonic=time.monotonic(),
        receive_wall_time=time.time(),
        byte_length=len(json.dumps(raw, default=str)),
    )


def _raw_time_seconds(raw: dict[str, Any], timestamp_scale: float) -> float | None:
    try:
        return float(raw.get("timestamp")) * float(timestamp_scale)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} file must contain a JSON object: {path}")
    return payload


def _read_optional_json(path: Path) -> dict[str, Any]:
    try:
        return _read_json(path, "session meta")
    except FileNotFoundError:
        return {}


def _write_replay_cue_outputs(
    out_dir: Path,
    *,
    cue_runtime: CueRuntime,
    cue_config: CueConfig,
    summary: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cue_log_path = out_dir / "cue_log.csv" if cue_runtime.cue_enabled else None
    if cue_log_path is not None:
        cue_runtime.write_log(cue_log_path)
    summary.update(cue_runtime.summary(cue_log_path=cue_log_path))
    (out_dir / "cue_config.json").write_text(
        json.dumps(cue_config.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "replay_debug_summary.json").write_text(
        json.dumps(_json_safe(summary), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _required_vec3(value: Any, label: str) -> list[float]:
    if all(hasattr(value, attr) for attr in ("x", "y", "z")):
        return [float(value.x), float(value.y), float(value.z)]
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        items = list(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be a 3D vector.") from exc
    if len(items) != 3:
        raise ValueError(f"{label} must be a 3D vector.")
    try:
        vector = [float(items[0]), float(items[1]), float(items[2])]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numeric values.") from exc
    return vector


def _vec3(value: list[float]) -> Vec3:
    return Vec3(float(value[0]), float(value[1]), float(value[2]))


def _nested_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
