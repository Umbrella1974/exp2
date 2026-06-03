"""Replay runner that feeds DashboardSnapshot into the debug GUI.

The GUI never reads raw JSONL directly. This module owns replay input loading
and reuses LiveTrialRunner so parser/adapter/controller semantics stay shared.
"""

from __future__ import annotations

import json
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
from data_models import Box3D, TrackRegion, Vec3
from debug_view_model import DebugSceneView, scene_view_from_trial_config
from latest_frame_buffer import LatestFrameBuffer
from latest_snapshot_store import LatestSnapshotStore
from live_raw_stream import LiveRawFrame
from live_trial_runner import LiveTrialRunner, LiveTrialRunnerConfig
from raw_frame_source import JsonlRawFrameSource
from task_coordinate_system import TaskCoordinateSystem
from timing_diagnostics import TimingDiagnostics


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
    timestamp_scale: float = 0.001
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    no_frame_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ReplayDebugInputs:
    """Resolved replay input paths and payloads."""

    raw_jsonl: Path
    calibration_json: Path
    trial_config_json: Path
    calibration_payload: dict[str, Any]
    trial_config: dict[str, Any]
    session_meta: dict[str, Any]
    scene: DebugSceneView


@dataclass(frozen=True)
class ReplayDebugResult:
    """Replay debug run result."""

    summary: dict[str, Any]
    scene: DebugSceneView
    last_snapshot: Any | None
    snapshot_count: int
    timing_diagnostics: TimingDiagnostics


def load_replay_debug_inputs(config: ReplayDebugConfig) -> ReplayDebugInputs:
    """Resolve and validate replay input files."""

    raw_path, calibration_path, trial_config_path, session_meta_path = _resolve_input_paths(config)
    calibration_payload = _read_json(calibration_path, "calibration")
    trial_config = _read_json(trial_config_path, "trial config")
    session_meta = _read_optional_json(session_meta_path) if session_meta_path is not None else {}
    scene = scene_view_from_trial_config(trial_config)
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
        scene=scene,
    )


def run_replay_debug(
    config: ReplayDebugConfig,
    *,
    snapshot_store: LatestSnapshotStore | None = None,
    snapshot_callback: SnapshotCallback | None = None,
    timing_diagnostics: TimingDiagnostics | None = None,
) -> ReplayDebugResult:
    """Replay raw frames through LiveTrialRunner and publish snapshots."""

    _validate_config(config)
    inputs = load_replay_debug_inputs(config)
    task_system = _task_system_from_calibration_payload(inputs.calibration_payload)
    track_region = _track_region_from_trial_config(inputs.trial_config)
    block_center = _block_center_from_trial_config(inputs.trial_config)
    block_size = _block_size_from_trial_config(inputs.trial_config)
    engine_config = _engine_config_from_trial_config(inputs.trial_config, block_size, inputs.session_meta)
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
            trial_id=str(inputs.trial_config.get("trial_id", inputs.session_meta.get("trial_id", "replay_debug"))),
            max_frames=None,
            no_frame_timeout_seconds=config.no_frame_timeout_seconds,
            timestamp_scale=config.timestamp_scale,
            thumb_node=config.thumb_node,
            index_node=config.index_node,
            tracker_index=config.tracker_index,
            skeleton_index=config.skeleton_index,
            pinch_position_mode=_pinch_position_mode_for_replay(inputs.trial_config, inputs.session_meta),
            timing_phase="REPLAY",
        ),
        map_id=str(inputs.trial_config.get("map_id", "")),
        calibration_id=str(inputs.calibration_payload.get("calibration_id", "")),
        trial_config={"mode": "replay_debug_gui", **inputs.trial_config},
        snapshot_callback=on_snapshot,
        source_stats_getter=lambda: {"total_received_frames": replay_state["raw_seen"]},
        timing_diagnostics=timing_diagnostics,
    )

    previous_raw_time: float | None = None
    source = JsonlRawFrameSource(inputs.raw_jsonl)
    try:
        for raw_index, raw in enumerate(source):
            if config.max_frames is not None and raw_index >= config.max_frames:
                break
            current_raw_time = _raw_time_seconds(raw, config.timestamp_scale)
            _sleep_for_replay_timing(config, previous_raw_time, current_raw_time)
            previous_raw_time = current_raw_time
            replay_state["raw_seen"] += 1
            latest_buffer.put(_live_frame_from_raw(raw_index, raw))
            runner.step_once()
            if runner.stats_snapshot().run_stop_reason != "running":
                break
    finally:
        source.close()

    if replay_state["raw_seen"] == 0:
        raise ValueError(f"raw_jsonl did not contain any frames: {inputs.raw_jsonl}")
    if runner.stats_snapshot().run_stop_reason == "running":
        runner.request_stop("eof")
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
            "snapshot_count": replay_state["snapshots"],
            "gui_scene": inputs.scene.to_dict(),
        }
    )
    summary.update(timing_diagnostics.summary())
    timing_path: Path | None = None
    if config.out_dir is not None:
        config.out_dir.mkdir(parents=True, exist_ok=True)
        timing_path = timing_diagnostics.write_csv(config.out_dir / "timing_diagnostics.csv")
        summary["timing_diagnostics_path"] = str(timing_path)
        (config.out_dir / "replay_debug_summary.json").write_text(
            json.dumps(_json_safe(summary), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    else:
        summary["timing_diagnostics_path"] = None
    return ReplayDebugResult(
        summary=summary,
        scene=inputs.scene,
        last_snapshot=runner.last_snapshot,
        snapshot_count=replay_state["snapshots"],
        timing_diagnostics=timing_diagnostics,
    )


def _pinch_position_mode_for_replay(trial_config: dict[str, Any], session_meta: dict[str, Any]) -> str:
    explicit = trial_config.get("pinch_position_mode", session_meta.get("pinch_position_mode"))
    if explicit is not None:
        return str(explicit)
    mode = str(trial_config.get("mode", session_meta.get("mode", "")))
    if mode == "live_integrated_session":
        return "nodes_world"
    return "tracker_plus_local"


def _resolve_input_paths(config: ReplayDebugConfig) -> tuple[Path, Path, Path, Path | None]:
    if config.session_dir is not None:
        session_dir = Path(config.session_dir)
        raw = session_dir / "raw_frames.jsonl"
        calibration = session_dir / "calibration.json"
        trial_config = session_dir / "trial_config.json"
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
        raise FileNotFoundError("Missing replay input: " + "; ".join(missing))
    assert raw is not None and calibration is not None and trial_config is not None
    if meta is not None and not meta.exists():
        meta = None
    return raw, calibration, trial_config, meta


def _validate_config(config: ReplayDebugConfig) -> None:
    if config.session_dir is not None and any(
        value is not None for value in (config.raw_jsonl, config.calibration_json, config.trial_config_json)
    ):
        raise ValueError("--session-dir cannot be combined with explicit replay input files.")
    if config.session_dir is None:
        if config.raw_jsonl is None or config.calibration_json is None or config.trial_config_json is None:
            raise ValueError("raw_jsonl, calibration_json, and trial_config_json are required without session_dir.")
    if config.replay_timing not in {"raw", "fixed", "fast"}:
        raise ValueError("replay_timing must be raw, fixed, or fast.")
    if config.replay_fps <= 0.0:
        raise ValueError("replay_fps must be > 0.")
    if config.replay_speed <= 0.0:
        raise ValueError("replay_speed must be > 0.")


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
) -> EngineConfig:
    defaults = EngineConfig()
    pinch_threshold = trial_config.get("pinch_threshold")
    if not isinstance(pinch_threshold, dict):
        pinch_threshold = {}
    mode = str(session_meta.get("mode", trial_config.get("mode", "")))
    default_max_delta = 10.0 if mode.startswith("offline_") else defaults.max_hand_delta_per_frame
    return EngineConfig(
        block_size_x=block_size.x,
        block_size_y=block_size.y,
        block_size_z=block_size.z,
        pinch_grab_threshold=float(pinch_threshold.get("grab", defaults.pinch_grab_threshold)),
        pinch_release_threshold=float(pinch_threshold.get("release", defaults.pinch_release_threshold)),
        slip_motion_threshold=float(trial_config.get("slip_motion_threshold", defaults.slip_motion_threshold)),
        trial_timeout_seconds=float(trial_config.get("trial_timeout_seconds", 1e9)),
        max_detach_count=int(trial_config.get("max_detach_count", 1_000_000_000)),
        max_hand_delta_per_frame=float(trial_config.get("max_hand_delta_per_frame", default_max_delta)),
    )


def _sleep_for_replay_timing(
    config: ReplayDebugConfig,
    previous_raw_time: float | None,
    current_raw_time: float | None,
) -> None:
    if config.replay_timing == "fast":
        return
    if config.replay_timing == "fixed":
        time.sleep(1.0 / config.replay_fps)
        return
    if previous_raw_time is None or current_raw_time is None:
        return
    delay = max(0.0, current_raw_time - previous_raw_time) / config.replay_speed
    if delay > 0.0:
        time.sleep(delay)


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
