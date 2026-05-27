"""Offline replay using a formal table-line calibration and MapConfig scene."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from block_controller import BlockController
from calibration_io import (
    build_task_coordinate_system_from_calibration,
    calibration_to_dict,
    load_calibration,
    validate_calibration,
)
from config import EngineConfig
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
from trial_controller import ExperimentInputSample, TrialController


OFFLINE_FORMAL_REPLAY_WARNING = (
    "This is offline replay using a formal calibration file; it is not a live formal trial."
)


@dataclass(frozen=True)
class FormalReplayConfig:
    """Configuration for formal-calibrated offline replay."""

    raw_jsonl: Path
    calibration_json: Path
    map_config: Path
    out_dir: Path = Path("data/offline_replay_formal")
    max_frames: int | None = None
    thumb_node: int = 4
    index_node: int = 9
    tracker_index: int = 0
    skeleton_index: int = 0
    timestamp_scale: float = 0.001
    offline_trial_timeout_seconds: float = 1e9
    offline_max_detach_count: int = 1_000_000_000
    write_session: bool = False
    session_dir: Path | None = None
    session_id: str | None = None
    subject_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class FormalReplayRecord:
    """Raw frame plus parsed/adapted sample."""

    raw_index: int
    raw: dict[str, Any]
    device_frame: Any
    sample: ExperimentInputSample


@dataclass(frozen=True)
class FormalReplayResult:
    """Serializable formal replay output."""

    frames: list[dict[str, Any]]
    events: list[dict[str, Any]]
    summary: dict[str, Any]
    calibration_payload: dict[str, Any]
    scene_payload: dict[str, Any]


def load_formal_replay_records(config: FormalReplayConfig) -> list[FormalReplayRecord]:
    """Load raw JSONL frames and adapt them to ExperimentInputSample objects."""

    adapter_config = DeviceAdapterConfig(
        skeleton_index=config.skeleton_index,
        tracker_index=config.tracker_index,
        thumb_tip_node_id=config.thumb_node,
        index_tip_node_id=config.index_node,
        timestamp_scale=config.timestamp_scale,
    )
    adapter = ManusViveExperimentAdapter(None, config=adapter_config)
    records: list[FormalReplayRecord] = []
    source = JsonlRawFrameSource(config.raw_jsonl)
    try:
        for raw_index, raw in enumerate(source):
            if config.max_frames is not None and raw_index >= config.max_frames:
                break
            device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
            sample = adapter.to_experiment_input_sample(device_frame)
            records.append(
                FormalReplayRecord(
                    raw_index=raw_index,
                    raw=raw,
                    device_frame=device_frame,
                    sample=sample,
                )
            )
    finally:
        source.close()
    return records


def run_formal_replay(config: FormalReplayConfig) -> FormalReplayResult:
    """Run formal-calibrated offline replay and return serializable outputs."""

    calibration = load_calibration(config.calibration_json)
    calibration_validation = validate_calibration(calibration)
    if calibration_validation.errors:
        raise ValueError(
            "calibration validation failed: " + "; ".join(calibration_validation.errors)
        )
    task_system = build_task_coordinate_system_from_calibration(calibration)

    map_config = load_map_config(config.map_config)
    map_validation = validate_map_config(map_config)
    if map_validation.errors:
        raise ValueError("map validation failed: " + "; ".join(map_validation.errors))
    track_region, block_center, block_size = compile_map_to_track_region(map_config)
    scene_payload = map_config_to_trial_config(map_config)
    scene_payload.update(
        {
            "scene_type": "map_config",
            "scene_mode": "map_config",
            "is_formal_scene": False,
            "map_config_used": True,
            "map_config_path": str(config.map_config),
            "map_validation_errors": list(map_validation.errors),
            "map_validation_warnings": list(map_validation.warnings),
            "track_box_count": len(map_config.track_boxes),
            "target_region_present": map_config.target_region is not None,
        }
    )

    engine_config = EngineConfig(
        block_size_x=block_size.x,
        block_size_y=block_size.y,
        block_size_z=block_size.z,
        trial_timeout_seconds=config.offline_trial_timeout_seconds,
        max_detach_count=config.offline_max_detach_count,
        max_hand_delta_per_frame=10.0,
    )

    def factory() -> BlockController:
        return BlockController(engine_config, track_region, block_center)

    records = load_formal_replay_records(config)
    if not records:
        raise ValueError("raw_jsonl did not contain any frames.")

    trial = TrialController(factory, task_system, engine_config)
    trial.start_trial(time=records[0].sample.time, trial_id="offline_formal_calibrated")

    session_recorder = None
    if config.write_session:
        session_dir = config.session_dir or (config.out_dir / "session")
        session_recorder = SessionRecorder(session_dir)
        session_recorder.start_session(
            session_meta=_session_meta(config, session_dir),
            calibration=calibration_to_dict(calibration),
            trial_config=_session_trial_config(scene_payload, engine_config, map_validation.warnings),
        )

    frames: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings = [OFFLINE_FORMAL_REPLAY_WARNING] + list(calibration_validation.warnings)
    warnings.extend(map_validation.warnings)
    raw_subject_end_count = 0
    pinch_distances: list[float] = []
    valid_pinch_frames = 0
    tracker_invalid_count = 0
    slip_active_count = 0
    blocked_count = 0
    large_delta_count = 0
    haptic_active_count = 0
    haptic_edge_count = 0
    previous_haptic_active = False

    for record in records:
        if session_recorder is not None:
            session_recorder.record_raw_frame(record.raw_index, record.raw)
            session_recorder.record_device_frame(record.raw_index, record.device_frame)

        if bool(record.raw.get("subject_end", False)):
            raw_subject_end_count += 1
        if record.sample.pinch_distance is not None:
            pinch_distances.append(float(record.sample.pinch_distance))
        if record.sample.tracker_valid and record.sample.pinch_center_world is not None:
            valid_pinch_frames += 1
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
        result = trial.update(replay_sample)
        output = result.frame_output
        haptic_active = bool(
            output.haptic_feedback.slip_active
            or output.haptic_feedback.blocked_force_active
        )
        if output.haptic_feedback.slip_active:
            slip_active_count += 1
        if haptic_active:
            haptic_active_count += 1
        if haptic_active and not previous_haptic_active:
            haptic_edge_count += 1
        previous_haptic_active = haptic_active
        if output.feedback_state.stop_reason.name == "TRACK_BLOCKED":
            blocked_count += 1
        if output.feedback_state.stop_reason.name == "LARGE_DELTA":
            large_delta_count += 1

        for event in result.events:
            events.append(_event_row(event))
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
                    "input_source": "formal_calibrated_pinch",
                    "trial_time": result.time_since_prompt,
                },
            )
            session_recorder.record_events(record.raw_index, replay_sample.time, result.events)
            session_recorder.record_haptic(
                record.raw_index,
                replay_sample.time,
                output.haptic_feedback,
                details={"mode": "offline_formal_calibrated_replay", "sent_to_hardware": False},
            )

    task_points = _task_points_from_frames(frames)
    summary = {
        "mode": "offline_formal_calibrated_replay",
        "calibration_type": "formal_table_lines",
        "is_formal_calibration": True,
        "is_live_trial": False,
        "scene_type": "map_config",
        "is_formal_scene": False,
        "map_config_used": True,
        "map_id": scene_payload.get("map_id", ""),
        "map_config_path": str(config.map_config),
        "total_raw_frames": len(records),
        "replayed_raw_frames": len(frames),
        "valid_pinch_frames": int(valid_pinch_frames),
        "generated_contact_enter_count": _event_count(events, "contact_enter"),
        "generated_contact_exit_count": _event_count(events, "contact_exit"),
        "slip_active_frame_count": int(slip_active_count),
        "blocked_frame_count": int(blocked_count),
        "large_delta_frame_count": int(large_delta_count),
        "tracker_invalid_frame_count": int(tracker_invalid_count),
        "haptic_active_frame_count": int(haptic_active_count),
        "haptic_event_count": int(haptic_edge_count),
        "pinch_distance_min": min(pinch_distances) if pinch_distances else None,
        "pinch_distance_mean": mean(pinch_distances) if pinch_distances else None,
        "pinch_distance_max": max(pinch_distances) if pinch_distances else None,
        "task_trajectory_range": _trajectory_range_payload(task_points),
        "raw_subject_end_frame_count": int(raw_subject_end_count),
        "forced_subject_end_false": True,
        "offline_trial_timeout_seconds": config.offline_trial_timeout_seconds,
        "timeout_effectively_disabled_for_offline_replay": True,
        "offline_max_detach_count": config.offline_max_detach_count,
        "too_many_detaches_effectively_disabled_for_offline_replay": True,
        "calibration_validation_warnings": list(calibration_validation.warnings),
        "map_validation_warnings": list(map_validation.warnings),
        "warnings": warnings,
    }
    if session_recorder is not None:
        session_recorder.finalize(summary)

    return FormalReplayResult(
        frames=frames,
        events=events,
        summary=summary,
        calibration_payload=calibration_to_dict(calibration),
        scene_payload=scene_payload,
    )


def write_outputs(result: FormalReplayResult, out_dir: Path) -> None:
    """Write formal replay CSV/JSON outputs."""

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "frames.csv", result.frames)
    _write_csv(out_dir / "events.csv", result.events)
    _write_json(out_dir / "summary.json", result.summary)
    _write_json(out_dir / "calibration_formal.json", result.calibration_payload)
    _write_json(out_dir / "scene_map_config.json", result.scene_payload)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = _parse_args(argv)
    config = FormalReplayConfig(
        raw_jsonl=Path(args.raw_jsonl),
        calibration_json=Path(args.calibration_json),
        map_config=Path(args.map_config),
        out_dir=Path(args.out_dir),
        max_frames=args.max_frames,
        thumb_node=args.thumb_node,
        index_node=args.index_node,
        tracker_index=args.tracker_index,
        skeleton_index=args.skeleton_index,
        timestamp_scale=args.timestamp_scale,
        write_session=args.write_session,
        session_dir=Path(args.session_dir) if args.session_dir is not None else None,
        session_id=args.session_id,
        subject_id=args.subject_id,
        notes=args.notes,
    )
    result = run_formal_replay(config)
    write_outputs(result, config.out_dir)
    print(json.dumps(result.summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _session_meta(config: FormalReplayConfig, session_dir: Path) -> dict[str, Any]:
    payload = {
        "session_id": config.session_id or session_dir.name,
        "mode": "offline_formal_calibrated_replay",
        "trial_id": "offline_formal_calibrated",
        "calibration_type": "formal_table_lines",
        "is_formal_calibration": True,
        "is_live_trial": False,
        "scene_type": "map_config",
        "is_formal_scene": False,
        "warnings": [OFFLINE_FORMAL_REPLAY_WARNING],
    }
    if config.subject_id is not None:
        payload["subject_id"] = config.subject_id
    if config.notes is not None:
        payload["notes"] = config.notes
    return payload


def _session_trial_config(
    scene_payload: dict[str, Any],
    engine_config: EngineConfig,
    map_warnings: list[str],
) -> dict[str, Any]:
    payload = dict(scene_payload)
    payload["pinch_threshold"] = {
        "grab": engine_config.pinch_grab_threshold,
        "release": engine_config.pinch_release_threshold,
    }
    payload["trial_timeout_seconds"] = engine_config.trial_timeout_seconds
    payload["warnings"] = [OFFLINE_FORMAL_REPLAY_WARNING] + list(map_warnings)
    return payload


def _frame_row(record: FormalReplayRecord, replay_sample: ExperimentInputSample, result: Any) -> dict[str, Any]:
    output = result.frame_output
    haptic = output.haptic_feedback
    return {
        "frame_index": record.raw_index,
        "time": replay_sample.time,
        "tracker_valid": replay_sample.tracker_valid,
        "hand_valid": replay_sample.metadata.get("hand_valid", False),
        "pinch_valid": replay_sample.metadata.get("pinch_valid", False),
        "pinch_distance": replay_sample.pinch_distance,
        "input_point_source": "pinch_center_world",
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
        "slip_reason": haptic.slip_reason.name if haptic.slip_reason is not None else "",
        "blocked_force_active": haptic.blocked_force_active,
        "events": "|".join(event.event_type for event in result.events),
    }


def _event_row(event: Any) -> dict[str, Any]:
    return {
        "time": event.time,
        "trial_id": event.trial_id,
        "event_type": event.event_type,
        "trial_state": event.state.name,
        "value": event.value,
        "details_json": json.dumps(event.details, ensure_ascii=False, sort_keys=True),
    }


def _task_points_from_frames(frames: list[dict[str, Any]]) -> np.ndarray:
    points: list[list[float]] = []
    for row in frames:
        x = row.get("pinch_center_task_x")
        y = row.get("pinch_center_task_y")
        z = row.get("pinch_center_task_z")
        if x in ("", None) or y in ("", None) or z in ("", None):
            continue
        points.append([float(x), float(y), float(z)])
    if not points:
        return np.empty((0, 3))
    return np.asarray(points, dtype=float)


def _trajectory_range_payload(points: np.ndarray) -> dict[str, Any]:
    if len(points) == 0:
        return {"point_count": 0}
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


def _event_count(events: list[dict[str, Any]], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


def _component(value: Any, index: int) -> float | str:
    if value is None:
        return ""
    return float(np.asarray(value, dtype=float)[index])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline replay with formal table-line calibration and MapConfig."
    )
    parser.add_argument("--raw-jsonl", required=True)
    parser.add_argument("--calibration-json", required=True)
    parser.add_argument("--map-config", required=True)
    parser.add_argument("--out-dir", default="data/offline_replay_formal")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--thumb-node", type=int, default=4)
    parser.add_argument("--index-node", type=int, default=9)
    parser.add_argument("--tracker-index", type=int, default=0)
    parser.add_argument("--skeleton-index", type=int, default=0)
    parser.add_argument("--timestamp-scale", type=float, default=0.001)
    parser.add_argument("--write-session", action="store_true")
    parser.add_argument("--session-dir", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--subject-id", default=None)
    parser.add_argument("--notes", default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
