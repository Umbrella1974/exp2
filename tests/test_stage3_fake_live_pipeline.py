"""Fake raw JSON pipeline tests for Stage 3."""

from __future__ import annotations

import json

from block_controller import BlockController
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_frame_source import JsonlRawFrameSource
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from task_coordinate_system import build_from_origin_and_x_point
from trial_controller import TrialController, TrialState


def raw_frame(timestamp: float, pinch_x: float, *, subject_end: bool = False) -> dict:
    return {
        "timestamp": timestamp,
        "frame": int(timestamp * 100),
        "subject_end": subject_end,
        "skeletons": [
            {
                "gloveId": "glove-a",
                "nodes": [
                    {"id": 4, "position": [pinch_x - 0.005, 0.0, 0.0]},
                    {"id": 9, "position": [pinch_x + 0.005, 0.0, 0.0]},
                ],
            }
        ],
        "trackers": [
            {
                "trackerId": "tracker-a",
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0, 1.0],
                "valid": True,
            }
        ],
    }


def test_fake_raw_json_runs_full_pipeline() -> None:
    config = EngineConfig(max_hand_delta_per_frame=0.5, slip_motion_threshold=0.05)
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 4.0, 4.0)),))

    def factory() -> BlockController:
        return BlockController(config, track, Vec3(0.0, 0.0, 0.0))

    task_system = build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )
    trial = TrialController(factory, task_system, config)
    trial.start_trial(time=0.0, trial_id="stage3-fake")
    adapter_config = DeviceAdapterConfig()
    adapter = ManusViveExperimentAdapter(task_system, config=adapter_config)

    results = []
    for raw in (
        raw_frame(0.0, 0.0),
        raw_frame(0.1, 0.2),
        raw_frame(0.2, 0.35, subject_end=True),
    ):
        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        sample = adapter.to_experiment_input_sample(device_frame)
        results.append(trial.update(sample))

    event_types = [event.event_type for event in trial.event_history]
    assert "contact_enter" in event_types
    assert "block_moved" in event_types
    assert "subject_end" in event_types
    assert results[-1].trial_state == TrialState.ENDED_BY_SUBJECT


def test_fake_raw_jsonl_source_runs_full_pipeline(tmp_path) -> None:
    path = tmp_path / "frames.jsonl"
    frames = (
        raw_frame(0.0, 0.0),
        raw_frame(0.1, 0.2),
        raw_frame(0.2, 0.35, subject_end=True),
    )
    path.write_text("\n".join(json.dumps(frame) for frame in frames), encoding="utf-8")

    config = EngineConfig(max_hand_delta_per_frame=0.5, slip_motion_threshold=0.05)
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 4.0, 4.0)),))

    def factory() -> BlockController:
        return BlockController(config, track, Vec3(0.0, 0.0, 0.0))

    task_system = build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )
    trial = TrialController(factory, task_system, config)
    trial.start_trial(time=0.0, trial_id="stage3-jsonl")
    adapter_config = DeviceAdapterConfig()
    adapter = ManusViveExperimentAdapter(task_system, config=adapter_config)
    source = JsonlRawFrameSource(path)
    try:
        results = []
        for raw in source:
            device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
            sample = adapter.to_experiment_input_sample(device_frame)
            results.append(trial.update(sample))
    finally:
        source.close()

    event_types = [event.event_type for event in trial.event_history]
    assert "contact_enter" in event_types
    assert "block_moved" in event_types
    assert results[-1].trial_state == TrialState.ENDED_BY_SUBJECT
