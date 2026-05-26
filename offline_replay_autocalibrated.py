"""Offline autocalibrated replay for uncalibrated raw JSONL data.

This tool is only for post-hoc smoke testing of raw MANUS/Vive recordings that
do not have a formal task calibration. It builds a temporary task coordinate
system and a temporary scene from the recorded trajectory, then replays the raw
frames through the existing parser, adapter, TrialController, and BlockController.

The automatically estimated task x-axis comes from the first calibration window
of valid points. These outputs are diagnostic and must not be treated as formal
experiment analysis. Formal experiments still need online calibration.

When --write-session is enabled, the generated session uses post-hoc automatic
calibration and is not formal experiment data. Formal experiments still require
subject-defined calibration and formal scene configuration.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np

from block_controller import BlockController
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from map_config import (
    compile_map_to_track_region,
    load_map_config,
    map_config_to_trial_config,
    validate_map_config,
)
from raw_frame_source import JsonlRawFrameSource
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from session_recorder import SessionRecorder
from task_coordinate_system import TaskCoordinateSystem
from trial_controller import ExperimentInputSample, TrialController


OFFLINE_AUTOCALIBRATED_SESSION_WARNING = (
    "This session was generated from post-hoc auto calibration and must not be treated "
    "as a formal experimental trial."
)

MAP_CONFIG_POST_HOC_WARNING = (
    "This session uses a configured map but post-hoc auto calibration; it must not be "
    "treated as a formal experimental trial."
)

HAPTIC_EVENT_TYPES = {
    "contact_enter",
    "contact_exit",
    "slip_start",
    "slip_end",
    "blocked_force_start",
    "blocked_force_end",
}


@dataclass(frozen=True)
class OfflineReplayConfig:
    """Configuration for offline autocalibrated replay."""

    raw_jsonl: Path
    max_frames: int | None = None
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    up_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    calibration_mode: str = "initial-window"
    calibration_frames: int = 100
    scene_mode: str = "wide-track"
    block_size: float = 0.20
    track_margin: float = 0.10
    track_width: float = 0.20
    narrow_track_width: float = 0.08
    z_tolerance: float = 0.20
    out_dir: Path = Path("data/offline_replay")
    timestamp_scale: float = 0.001
    offline_trial_timeout_seconds: float = 1e9
    offline_max_detach_count: int = 1_000_000_000
    map_config: Path | None = None
    map_id_override: str | None = None
    strict_map_validation: bool = False
    write_session: bool = False
    session_dir: Path | None = None
    session_id: str | None = None
    subject_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class RawFrameRecord:
    """Raw frame plus parsed adapter output used by offline replay."""

    raw_index: int
    raw: dict[str, Any]
    device_frame: Any
    sample: ExperimentInputSample
    input_point_world: np.ndarray | None
    input_point_source: str


@dataclass(frozen=True)
class CalibrationResult:
    """Temporary task calibration result and metadata."""

    task_coordinate_system: TaskCoordinateSystem
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SceneResult:
    """Temporary scene result and metadata."""

    track_region: TrackRegion
    block_center_task: Vec3
    block_size: Vec3
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OfflineReplayResult:
    """All outputs produced by offline replay."""

    frames: list[dict[str, Any]]
    events: list[dict[str, Any]]
    calibration_payload: dict[str, Any]
    scene_payload: dict[str, Any]
    summary: dict[str, Any]


def load_samples_from_raw_jsonl(config: OfflineReplayConfig) -> list[RawFrameRecord]:
    """Load raw frames, parse them, and build initial ExperimentInputSample records."""

    adapter_config = DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    source = JsonlRawFrameSource(config.raw_jsonl)
    records: list[RawFrameRecord] = []
    try:
        for raw_index, raw in enumerate(source):
            if config.max_frames is not None and raw_index >= config.max_frames:
                break
            device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
            sample = adapter.to_experiment_input_sample(device_frame)
            point, source_name = _input_point_from_sample_or_tracker(sample, device_frame)
            records.append(
                RawFrameRecord(
                    raw_index=raw_index,
                    raw=raw,
                    device_frame=device_frame,
                    sample=sample,
                    input_point_world=point,
                    input_point_source=source_name,
                )
            )
    finally:
        source.close()
    return records


def collect_valid_trajectory_points(
    records: list[RawFrameRecord],
) -> tuple[np.ndarray, list[str], list[RawFrameRecord]]:
    """Collect valid world trajectory points and their source labels."""

    points: list[np.ndarray] = []
    sources: list[str] = []
    valid_records: list[RawFrameRecord] = []
    for record in records:
        if record.input_point_world is None:
            continue
        points.append(record.input_point_world)
        sources.append(record.input_point_source)
        valid_records.append(record)
    if not points:
        return np.empty((0, 3)), sources, valid_records
    return np.vstack(points), sources, valid_records


def build_autocalibrated_task_coordinate_system(
    trajectory_points_world: np.ndarray,
    config: OfflineReplayConfig,
) -> CalibrationResult:
    """Build a temporary task coordinate system from early valid points."""

    if len(trajectory_points_world) < 2:
        raise ValueError("Need at least two valid input points to estimate a task x-axis.")

    warnings: list[str] = []
    calibration_points = trajectory_points_world[: config.calibration_frames]
    if len(calibration_points) < config.calibration_frames:
        warnings.append(
            "valid point count is smaller than calibration_frames; using all valid points."
        )

    if config.calibration_mode == "initial-window":
        origin, x_point, method, distance, fallback_used = _initial_window_axis(
            calibration_points,
            trajectory_points_world,
        )
    elif config.calibration_mode == "pca":
        origin, x_point, method, distance, fallback_used = _pca_axis(
            calibration_points,
            trajectory_points_world,
        )
    else:
        raise ValueError("calibration_mode must be initial-window or pca.")

    task_system = TaskCoordinateSystem.build_from_origin_and_x_point(
        origin,
        x_point,
        np.asarray(config.up_axis, dtype=float),
        min_x_axis_length=1e-4,
    )
    payload = {
        "calibration_mode": config.calibration_mode,
        "calibration_frames": config.calibration_frames,
        "calibration_points_count": int(len(calibration_points)),
        "origin_world": origin.tolist(),
        "x_point_world": x_point.tolist(),
        "up_axis_world": list(config.up_axis),
        "x_axis_world": task_system.x_axis_world.tolist(),
        "y_axis_world": task_system.y_axis_world.tolist(),
        "z_axis_world": task_system.z_axis_world.tolist(),
        "x_axis_estimation_method": method,
        "x_axis_estimation_distance": float(distance),
        "whether_fallback_used": bool(fallback_used),
        "warnings": warnings,
    }
    return CalibrationResult(task_system, payload, warnings)


def build_auto_scene(
    task_points: np.ndarray,
    config: OfflineReplayConfig,
) -> SceneResult:
    """Build a temporary block/track scene from task-space trajectory range."""

    if len(task_points) == 0:
        raise ValueError("Need at least one task-space trajectory point to build a scene.")

    warnings: list[str] = []
    x_min, y_min, z_min = np.min(task_points, axis=0)
    x_max, y_max, z_max = np.max(task_points, axis=0)
    median_z = float(np.median(task_points[:, 2]))
    y_median = float(np.median(task_points[:, 1]))
    block_center = Vec3(*map(float, task_points[0]))
    block_size = Vec3(config.block_size, config.block_size, config.block_size)

    if config.scene_mode == "wide-track":
        min_corner = Vec3(
            float(x_min - config.track_margin),
            float(y_min - config.track_margin),
            float(z_min - config.track_margin),
        )
        max_corner = Vec3(
            float(x_max + config.track_margin),
            float(y_max + config.track_margin),
            float(z_max + config.track_margin),
        )
        width_used = None
    elif config.scene_mode in ("fitted-corridor", "narrow-corridor"):
        width_used = (
            config.narrow_track_width
            if config.scene_mode == "narrow-corridor"
            else config.track_width
        )
        if abs(y_median) > width_used / 2.0:
            warnings.append(
                "task_y_median is outside half corridor width; blocked behavior may dominate."
            )
        min_corner = Vec3(
            float(x_min - config.track_margin),
            float(-width_used / 2.0),
            float(median_z - config.z_tolerance),
        )
        max_corner = Vec3(
            float(x_max + config.track_margin),
            float(width_used / 2.0),
            float(median_z + config.z_tolerance),
        )
    else:
        raise ValueError("scene_mode must be wide-track, fitted-corridor, or narrow-corridor.")

    center = Vec3(
        (min_corner.x + max_corner.x) * 0.5,
        (min_corner.y + max_corner.y) * 0.5,
        (min_corner.z + max_corner.z) * 0.5,
    )
    size = Vec3(
        max(max_corner.x - min_corner.x, 1e-6),
        max(max_corner.y - min_corner.y, 1e-6),
        max(max_corner.z - min_corner.z, 1e-6),
    )
    track = TrackRegion(boxes=(Box3D(center=center, size=size),))
    payload = {
        "scene_mode": config.scene_mode,
        "block_size": config.block_size,
        "block_center_task": _vec_to_list(block_center),
        "track_bounds": {
            "min": _vec_to_list(min_corner),
            "max": _vec_to_list(max_corner),
            "center": _vec_to_list(center),
            "size": _vec_to_list(size),
        },
        "track_margin": config.track_margin,
        "track_width": config.track_width,
        "narrow_track_width": config.narrow_track_width,
        "z_tolerance": config.z_tolerance,
        "corridor_y_center": 0.0 if config.scene_mode != "wide-track" else None,
        "task_y_min": float(y_min),
        "task_y_max": float(y_max),
        "task_y_median": y_median,
        "task_trajectory_range": _trajectory_range_payload(task_points),
        "warnings": warnings,
    }
    return SceneResult(track, block_center, block_size, payload, warnings)


def build_map_config_scene(config: OfflineReplayConfig) -> SceneResult:
    """Build a scene from a MapConfig JSON file without changing controller logic."""

    if config.map_config is None:
        raise ValueError("map_config path is required to build a MapConfig scene.")

    map_path = Path(config.map_config)
    map_config = load_map_config(map_path)
    validation = validate_map_config(map_config)
    if validation.errors:
        raise ValueError("map validation failed: " + "; ".join(validation.errors))
    if validation.warnings and config.strict_map_validation:
        raise ValueError(
            "strict map validation failed due to warnings: "
            + "; ".join(validation.warnings)
        )

    track_region, block_center, block_size = compile_map_to_track_region(map_config)
    payload = map_config_to_trial_config(map_config)
    original_map_id = payload.get("map_id", "")
    map_id_overridden = config.map_id_override is not None
    if map_id_overridden:
        payload["map_id"] = config.map_id_override

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "original_map_id": original_map_id,
            "map_id_overridden": map_id_overridden,
        }
    )
    payload["metadata"] = metadata
    payload.update(
        {
            "scene_type": "map_config",
            "scene_mode": "map_config",
            "is_formal_scene": False,
            "map_config_used": True,
            "original_map_id": original_map_id,
            "map_id_overridden": map_id_overridden,
            "map_config_path": str(map_path),
            "strict_map_validation": bool(config.strict_map_validation),
            "map_validation_errors": list(validation.errors),
            "map_validation_warnings": list(validation.warnings),
            "track_box_count": len(map_config.track_boxes),
            "target_region_present": map_config.target_region is not None,
        }
    )

    warnings = [MAP_CONFIG_POST_HOC_WARNING] + list(validation.warnings)
    return SceneResult(track_region, block_center, block_size, payload, warnings)


def run_offline_replay(config: OfflineReplayConfig) -> OfflineReplayResult:
    """Run the full offline autocalibrated replay and return serializable outputs."""

    records = load_samples_from_raw_jsonl(config)
    points_world, point_sources, valid_records = collect_valid_trajectory_points(records)
    calibration = build_autocalibrated_task_coordinate_system(points_world, config)
    task_points = np.vstack(
        [calibration.task_coordinate_system.world_to_task(point) for point in points_world]
    )
    scene = (
        build_map_config_scene(config)
        if config.map_config is not None
        else build_auto_scene(task_points, config)
    )

    engine_config = EngineConfig(
        block_size_x=scene.block_size.x,
        block_size_y=scene.block_size.y,
        block_size_z=scene.block_size.z,
        trial_timeout_seconds=config.offline_trial_timeout_seconds,
        max_detach_count=config.offline_max_detach_count,
        max_hand_delta_per_frame=10.0,
    )

    def factory() -> BlockController:
        return BlockController(engine_config, scene.track_region, scene.block_center_task)

    trial = TrialController(factory, calibration.task_coordinate_system, engine_config)
    first_valid_time = valid_records[0].sample.time if valid_records else records[0].sample.time
    trial.start_trial(time=first_valid_time, trial_id="offline_autocalibrated")

    session_recorder = None
    if config.write_session:
        session_dir = config.session_dir or (config.out_dir / "session")
        session_recorder = SessionRecorder(session_dir)
        session_recorder.start_session(
            session_meta=_offline_session_meta(config, session_dir),
            calibration=_offline_session_calibration(
                calibration.payload,
                warnings=[MAP_CONFIG_POST_HOC_WARNING] if config.map_config is not None else None,
            ),
            trial_config=_offline_session_trial_config(
                scene.payload,
                engine_config,
                calibration.warnings + scene.warnings,
            ),
        )

    frames: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings = list(calibration.warnings) + list(scene.warnings)
    raw_subject_end_count = 0
    pinch_distances: list[float] = []
    valid_pinch_frames = 0
    tracker_fallback_count = 0
    invalid_input_count = 0
    tracker_invalid_count = 0
    slip_active_count = 0
    blocked_count = 0
    large_delta_count = 0
    haptic_active_count = 0

    for record in records:
        if session_recorder is not None:
            session_recorder.record_raw_frame(record.raw_index, record.raw)
            session_recorder.record_device_frame(record.raw_index, record.device_frame)

        if bool(record.raw.get("subject_end", False)):
            raw_subject_end_count += 1
        if record.sample.pinch_distance is not None:
            pinch_distances.append(float(record.sample.pinch_distance))
        if record.input_point_source == "pinch":
            valid_pinch_frames += 1
        elif record.input_point_source == "tracker_fallback":
            tracker_fallback_count += 1
        else:
            invalid_input_count += 1
        if not record.sample.tracker_valid:
            tracker_invalid_count += 1

        replay_sample = ExperimentInputSample(
            time=record.sample.time,
            pinch_distance=record.sample.pinch_distance,
            tracker_valid=record.sample.tracker_valid,
            pinch_center_world=record.sample.pinch_center_world,
            coordinate_space="world",
            subject_end=False,
            metadata=record.sample.metadata,
        )
        if replay_sample.pinch_center_world is None and record.input_point_source == "tracker_fallback":
            replay_sample = ExperimentInputSample(
                time=record.sample.time,
                pinch_distance=record.sample.pinch_distance,
                tracker_valid=True,
                pinch_center_world=record.input_point_world,
                coordinate_space="world",
                subject_end=False,
                metadata={**record.sample.metadata, "offline_point_source": "tracker_fallback"},
            )

        result = trial.update(replay_sample)
        output = result.frame_output
        if output.haptic_feedback.slip_active:
            slip_active_count += 1
        if output.haptic_feedback.slip_active or output.haptic_feedback.blocked_force_active:
            haptic_active_count += 1
        if output.feedback_state.stop_reason.name == "TRACK_BLOCKED":
            blocked_count += 1
        if output.feedback_state.stop_reason.name == "LARGE_DELTA":
            large_delta_count += 1

        for event in result.events:
            events.append(
                {
                    "time": event.time,
                    "trial_id": event.trial_id,
                    "event_type": event.event_type,
                    "trial_state": event.state.name,
                    "value": event.value,
                    "details_json": json.dumps(event.details, sort_keys=True),
                }
            )

        frames.append(_frame_row(record, replay_sample, result))

        if session_recorder is not None:
            session_recorder.record_processed_frame(
                record.raw_index,
                record.raw,
                record.device_frame,
                replay_sample,
                output,
                haptic_state=output.haptic_feedback,
                extra={
                    "input_source": record.input_point_source,
                    "trial_time": result.time_since_prompt,
                },
            )
            session_recorder.record_events(record.raw_index, replay_sample.time, result.events)
            session_recorder.record_haptic(
                record.raw_index,
                replay_sample.time,
                output.haptic_feedback,
                details={"mode": "offline_replay", "sent_to_hardware": False},
            )

    summary = {
        "total_raw_frames": len(records),
        "replayed_raw_frames": len(frames),
        "valid_input_frames": int(len(valid_records)),
        "valid_pinch_frames": int(valid_pinch_frames),
        "tracker_fallback_frame_count": int(tracker_fallback_count),
        "invalid_input_frame_count": int(invalid_input_count),
        "calibration_points_count": calibration.payload["calibration_points_count"],
        "generated_contact_enter_count": _event_count(events, "contact_enter"),
        "generated_contact_exit_count": _event_count(events, "contact_exit"),
        "slip_active_frame_count": int(slip_active_count),
        "blocked_frame_count": int(blocked_count),
        "large_delta_frame_count": int(large_delta_count),
        "tracker_invalid_frame_count": int(tracker_invalid_count),
        "haptic_active_frame_count": int(haptic_active_count),
        "haptic_event_count": _haptic_event_count(events),
        "pinch_distance_min": min(pinch_distances) if pinch_distances else None,
        "pinch_distance_mean": mean(pinch_distances) if pinch_distances else None,
        "pinch_distance_max": max(pinch_distances) if pinch_distances else None,
        "task_trajectory_range": _trajectory_range_payload(task_points),
        "scene_mode": scene.payload.get("scene_mode", config.scene_mode),
        "calibration_mode": config.calibration_mode,
        "calibration_frames": config.calibration_frames,
        "raw_subject_end_frame_count": int(raw_subject_end_count),
        "forced_subject_end_false": True,
        "offline_trial_timeout_seconds": config.offline_trial_timeout_seconds,
        "timeout_effectively_disabled_for_offline_replay": True,
        "offline_max_detach_count": config.offline_max_detach_count,
        "too_many_detaches_effectively_disabled_for_offline_replay": True,
        "task_y_min": float(np.min(task_points[:, 1])),
        "task_y_max": float(np.max(task_points[:, 1])),
        "task_y_median": float(np.median(task_points[:, 1])),
        "corridor_y_center": scene.payload.get("corridor_y_center"),
        "warnings": warnings,
    }
    if scene.payload.get("map_config_used"):
        summary.update(_map_summary_fields(scene.payload))
    if session_recorder is not None:
        session_recorder.finalize(summary)
    return OfflineReplayResult(frames, events, calibration.payload, scene.payload, summary)


def _offline_session_meta(config: OfflineReplayConfig, session_dir: Path) -> dict[str, Any]:
    warnings = [OFFLINE_AUTOCALIBRATED_SESSION_WARNING]
    if config.map_config is not None:
        warnings.append(MAP_CONFIG_POST_HOC_WARNING)
    session_meta = {
        "session_id": config.session_id or session_dir.name,
        "mode": "offline_autocalibrated",
        "trial_id": "offline_autocalibrated",
        "calibration_type": "post_hoc_auto",
        "is_formal_calibration": False,
        "scene_type": "map_config" if config.map_config is not None else "post_hoc_auto",
        "is_formal_scene": False,
        "warnings": warnings,
    }
    if config.subject_id is not None:
        session_meta["subject_id"] = config.subject_id
    if config.notes is not None:
        session_meta["notes"] = config.notes
    return session_meta


def _offline_session_calibration(
    calibration_payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "calibration_type": "post_hoc_auto",
        "is_formal_calibration": False,
        "calibration_auto": calibration_payload,
    }
    if warnings:
        payload["warnings"] = list(warnings)
    return payload


def _offline_session_trial_config(
    scene_payload: dict[str, Any],
    engine_config: EngineConfig,
    warnings: list[str],
) -> dict[str, Any]:
    if scene_payload.get("scene_type") == "map_config":
        payload = dict(scene_payload)
        payload["pinch_threshold"] = {
            "grab": engine_config.pinch_grab_threshold,
            "release": engine_config.pinch_release_threshold,
        }
        payload["trial_timeout_seconds"] = engine_config.trial_timeout_seconds
        payload["warnings"] = warnings
        return payload

    return {
        "scene_type": "post_hoc_auto",
        "is_formal_scene": False,
        "scene_auto": scene_payload,
        "block_size": scene_payload.get("block_size"),
        "block_initial_center_task": scene_payload.get("block_center_task"),
        "track_bounds_task": scene_payload.get("track_bounds"),
        "scene_mode": scene_payload.get("scene_mode"),
        "pinch_threshold": {
            "grab": engine_config.pinch_grab_threshold,
            "release": engine_config.pinch_release_threshold,
        },
        "trial_timeout_seconds": engine_config.trial_timeout_seconds,
        "warnings": warnings,
    }


def _map_summary_fields(scene_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "map_config_used": True,
        "map_id": scene_payload.get("map_id", ""),
        "original_map_id": scene_payload.get("original_map_id", ""),
        "map_id_overridden": bool(scene_payload.get("map_id_overridden")),
        "map_config_path": scene_payload.get("map_config_path", ""),
        "map_config_version": scene_payload.get("map_config_version", ""),
        "map_source_type": scene_payload.get("map_source_type", ""),
        "track_box_count": scene_payload.get("track_box_count", 0),
        "target_region_present": bool(scene_payload.get("target_region_present")),
        "strict_map_validation": bool(scene_payload.get("strict_map_validation")),
        "map_validation_errors": list(scene_payload.get("map_validation_errors", [])),
        "map_validation_warnings": list(scene_payload.get("map_validation_warnings", [])),
    }


def write_outputs(result: OfflineReplayResult, out_dir: Path) -> None:
    """Write CSV/JSON outputs and optional plots."""

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "frames.csv", result.frames)
    _write_csv(out_dir / "events.csv", result.events)
    _write_json(out_dir / "calibration_auto.json", result.calibration_payload)
    _write_json(out_dir / "scene_auto.json", result.scene_payload)

    warnings = result.summary.setdefault("warnings", [])
    try:
        _write_optional_plots(result, out_dir)
    except Exception as exc:  # pragma: no cover - best effort path
        warnings.append(f"plot generation failed: {exc}")

    _write_json(out_dir / "summary.json", result.summary)


def main() -> None:
    """CLI entrypoint for offline autocalibrated replay."""

    args = _parse_args()
    config = OfflineReplayConfig(
        raw_jsonl=Path(args.raw_jsonl),
        max_frames=args.max_frames,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        up_axis=_parse_vec3(args.up_axis),
        calibration_mode=args.calibration_mode,
        calibration_frames=args.calibration_frames,
        scene_mode=args.scene_mode,
        block_size=args.block_size,
        track_margin=args.track_margin,
        track_width=args.track_width,
        narrow_track_width=args.narrow_track_width,
        z_tolerance=args.z_tolerance,
        out_dir=Path(args.out_dir),
        offline_max_detach_count=args.offline_max_detach_count,
        map_config=Path(args.map_config) if args.map_config is not None else None,
        map_id_override=args.map_id_override,
        strict_map_validation=args.strict_map_validation,
        write_session=args.write_session,
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        session_id=args.session_id,
        subject_id=args.subject_id,
        notes=args.notes,
    )
    result = run_offline_replay(config)
    write_outputs(result, config.out_dir)
    print(json.dumps(result.summary, indent=2, sort_keys=True))


def _input_point_from_sample_or_tracker(
    sample: ExperimentInputSample,
    device_frame: Any,
) -> tuple[np.ndarray | None, str]:
    if sample.tracker_valid and sample.pinch_center_world is not None:
        return np.asarray(sample.pinch_center_world, dtype=float), "pinch"
    if (
        device_frame.tracker is not None
        and device_frame.tracker.valid
        and device_frame.tracker.pose_world is not None
    ):
        return np.asarray(device_frame.tracker.pose_world.position, dtype=float), "tracker_fallback"
    return None, "invalid"


def _initial_window_axis(
    calibration_points: np.ndarray,
    all_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, str, float, bool]:
    origin = calibration_points[0]
    x_point, distance = _farthest_point(origin, calibration_points)
    fallback_used = False
    if distance < 1e-4:
        x_point, distance = _farthest_point(origin, all_points)
        fallback_used = True
    if distance < 1e-4:
        raise ValueError("Unable to estimate x direction from concentrated trajectory points.")
    return origin, x_point, "initial_window_farthest_point", distance, fallback_used


def _pca_axis(
    calibration_points: np.ndarray,
    all_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, str, float, bool]:
    origin = calibration_points[0]
    centered = calibration_points - np.mean(calibration_points, axis=0)
    if len(calibration_points) < 3 or float(np.max(np.linalg.norm(centered, axis=1))) < 1e-4:
        x_point, distance = _farthest_point(origin, all_points)
        if distance < 1e-4:
            raise ValueError("Unable to estimate x direction from concentrated trajectory points.")
        return origin, x_point, "pca_fallback_initial_window_farthest_point", distance, True

    _, singular_values, vh = np.linalg.svd(centered, full_matrices=False)
    if len(singular_values) == 0 or singular_values[0] < 1e-6:
        x_point, distance = _farthest_point(origin, all_points)
        return origin, x_point, "pca_fallback_initial_window_farthest_point", distance, True

    axis = vh[0]
    direction_hint = calibration_points[-1] - calibration_points[0]
    if float(np.dot(axis, direction_hint)) < 0.0:
        axis = -axis
    return origin, origin + axis, "pca_first_component", float(np.linalg.norm(axis)), False


def _farthest_point(origin: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, float]:
    distances = np.linalg.norm(points - origin, axis=1)
    index = int(np.argmax(distances))
    return points[index], float(distances[index])


def _frame_row(record: RawFrameRecord, replay_sample: ExperimentInputSample, result: Any) -> dict[str, Any]:
    output = result.frame_output
    haptic = output.haptic_feedback
    return {
        "frame_index": record.raw_index,
        "time": replay_sample.time,
        "tracker_valid": replay_sample.tracker_valid,
        "hand_valid": replay_sample.metadata.get("hand_valid", False),
        "pinch_valid": replay_sample.metadata.get("pinch_valid", False),
        "pinch_distance": replay_sample.pinch_distance,
        "input_point_source": record.input_point_source,
        "pinch_center_world_x": _component(replay_sample.pinch_center_world, 0),
        "pinch_center_world_y": _component(replay_sample.pinch_center_world, 1),
        "pinch_center_world_z": _component(replay_sample.pinch_center_world, 2),
        "pinch_center_task_x": output.pinch_center_task.x if output.pinch_center_task else "",
        "pinch_center_task_y": output.pinch_center_task.y if output.pinch_center_task else "",
        "pinch_center_task_z": output.pinch_center_task.z if output.pinch_center_task else "",
        "block_center_task_x": output.block_state.center.x,
        "block_center_task_y": output.block_state.center.y,
        "block_center_task_z": output.block_state.center.z,
        "contact_state": output.contact_state.name,
        "pinch_state": output.pinch_state.name,
        "block_motion_state": output.block_state.motion_state.name,
        "stop_reason": output.feedback_state.stop_reason.name,
        "track_state": output.feedback_state.track_state.name,
        "slip_active": haptic.slip_active,
        "blocked_force_active": haptic.blocked_force_active,
        "events": "|".join(event.event_type for event in result.events),
    }


def _trajectory_range_payload(points: np.ndarray) -> dict[str, Any]:
    return {
        "x_min": float(np.min(points[:, 0])),
        "x_max": float(np.max(points[:, 0])),
        "y_min": float(np.min(points[:, 1])),
        "y_max": float(np.max(points[:, 1])),
        "z_min": float(np.min(points[:, 2])),
        "z_max": float(np.max(points[:, 2])),
        "median_z": float(np.median(points[:, 2])),
        "point_count": int(len(points)),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_optional_plots(result: OfflineReplayResult, out_dir: Path) -> None:
    import matplotlib.pyplot as plt

    times = [float(row["time"]) for row in result.frames]
    task_x = [
        float(row["pinch_center_task_x"])
        for row in result.frames
        if row["pinch_center_task_x"] not in (None, "")
    ]
    task_y = [
        float(row["pinch_center_task_y"])
        for row in result.frames
        if row["pinch_center_task_y"] not in (None, "")
    ]
    block_x = [float(row["block_center_task_x"]) for row in result.frames]
    block_y = [float(row["block_center_task_y"]) for row in result.frames]
    block_z = [float(row["block_center_task_z"]) for row in result.frames]
    pinch_distances = [
        row["pinch_distance"] for row in result.frames if row["pinch_distance"] not in (None, "")
    ]

    plt.figure()
    plt.plot(task_x, task_y)
    plt.xlabel("input task x")
    plt.ylabel("input task y")
    plt.savefig(out_dir / "task_trajectory_xyz.png")
    plt.close()

    plt.figure()
    plt.plot(pinch_distances)
    plt.xlabel("frame")
    plt.ylabel("pinch distance")
    plt.savefig(out_dir / "pinch_distance_over_time.png")
    plt.close()

    plt.figure()
    plt.plot(times, block_x, label="x")
    plt.plot(times, block_y, label="y")
    plt.plot(times, block_z, label="z")
    plt.legend()
    plt.savefig(out_dir / "block_center_xyz_over_time.png")
    plt.close()

    plt.figure()
    plt.plot([row["contact_state"] == "INSIDE_BLOCK" for row in result.frames])
    plt.ylabel("inside block")
    plt.savefig(out_dir / "contact_state_over_time.png")
    plt.close()

    stop_counts: dict[str, int] = {}
    for row in result.frames:
        stop_counts[row["stop_reason"]] = stop_counts.get(row["stop_reason"], 0) + 1
    plt.figure()
    plt.bar(list(stop_counts.keys()), list(stop_counts.values()))
    plt.xticks(rotation=30, ha="right")
    plt.savefig(out_dir / "stop_reason_counts.png")
    plt.close()


def _event_count(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


def _haptic_event_count(events: list[dict[str, Any]]) -> int:
    return sum(1 for event in events if event["event_type"] in HAPTIC_EVENT_TYPES)


def _vec_to_list(vector: Vec3) -> list[float]:
    return [vector.x, vector.y, vector.z]


def _component(value: Any, index: int) -> float | str:
    if value is None:
        return ""
    return float(np.asarray(value, dtype=float)[index])


def _parse_vec3(value: str) -> tuple[float, float, float]:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("--up-axis must contain three comma-separated values.")
    return (parts[0], parts[1], parts[2])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline autocalibrated raw JSONL replay.")
    parser.add_argument("--raw-jsonl", required=True)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--thumb-node", type=int, default=4)
    parser.add_argument("--index-node", type=int, default=9)
    parser.add_argument("--tracker-index", type=int, default=0)
    parser.add_argument("--skeleton-index", type=int, default=0)
    parser.add_argument("--up-axis", default="0,0,1")
    parser.add_argument(
        "--calibration-mode",
        choices=("initial-window", "pca"),
        default="initial-window",
    )
    parser.add_argument("--calibration-frames", type=int, default=100)
    parser.add_argument(
        "--scene-mode",
        choices=("wide-track", "fitted-corridor", "narrow-corridor"),
        default="wide-track",
    )
    parser.add_argument("--block-size", type=float, default=0.20)
    parser.add_argument("--track-margin", type=float, default=0.10)
    parser.add_argument("--track-width", type=float, default=0.20)
    parser.add_argument("--narrow-track-width", type=float, default=0.08)
    parser.add_argument("--z-tolerance", type=float, default=0.20)
    parser.add_argument("--out-dir", default="data/offline_replay")
    parser.add_argument("--offline-max-detach-count", type=int, default=1_000_000_000)
    parser.add_argument(
        "--map-config",
        default=None,
        help=(
            "Optional MapConfig JSON scene. When set, replay still uses post-hoc "
            "auto calibration, but block/track geometry comes from this map."
        ),
    )
    parser.add_argument(
        "--map-id-override",
        default=None,
        help="Override map_id in this replay output only; the source map JSON is not modified.",
    )
    parser.add_argument(
        "--strict-map-validation",
        action="store_true",
        help=(
            "When used with --map-config, fail on validation warnings as well as errors. "
            "By default, map validation warnings are recorded but do not stop replay."
        ),
    )
    parser.add_argument(
        "--write-session",
        action="store_true",
        help=(
            "Write a standard session directory using post-hoc auto calibration. "
            "This output is not formal experiment data."
        ),
    )
    parser.add_argument("--session-dir", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--notes", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    main()
