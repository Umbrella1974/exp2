"""Fake-source live preview for the Stage 3 device adapter pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from block_controller import BlockController
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from task_coordinate_system import build_from_origin_and_x_point
from trial_controller import TrialController


def iter_raw_json_source(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield raw JSON frames from a JSON object, JSON list, or JSONL file."""

    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8").strip()
    if not text:
        return

    if source_path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)
        return

    payload = json.loads(text)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
    elif isinstance(payload, dict):
        yield payload


def run_preview(raw_frames: Iterable[dict[str, Any]]) -> None:
    """Run raw frames through parser, adapter, TrialController, and BlockController."""

    engine_config = EngineConfig()
    adapter_config = DeviceAdapterConfig()
    task_system = build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )
    track = TrackRegion(
        boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 4.0, 4.0)),)
    )

    def factory() -> BlockController:
        return BlockController(engine_config, track, Vec3(0.0, 0.0, 0.0))

    trial_controller = TrialController(factory, task_system, engine_config)
    trial_controller.start_trial(time=0.0, trial_id="preview")
    adapter = ManusViveExperimentAdapter(task_system, config=adapter_config)

    for raw in raw_frames:
        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        sample = adapter.to_experiment_input_sample(device_frame)
        result = trial_controller.update(sample)
        output = result.frame_output
        print(
            {
                "tracker_valid": sample.tracker_valid,
                "hand_valid": sample.metadata.get("hand_valid"),
                "pinch_valid": sample.metadata.get("pinch_valid"),
                "pinch_distance": sample.pinch_distance,
                "pinch_center_world": (
                    sample.pinch_center_world.tolist()
                    if hasattr(sample.pinch_center_world, "tolist")
                    else sample.pinch_center_world
                ),
                "pinch_center_task": output.pinch_center_task.components()
                if output.pinch_center_task is not None
                else None,
                "contact_state": output.contact_state.name,
                "block_motion_state": output.block_state.motion_state.name,
                "stop_reason": output.feedback_state.stop_reason.name,
                "track_state": output.feedback_state.track_state.name,
                "slip_active": output.haptic_feedback.slip_active,
                "blocked_force_active": output.haptic_feedback.blocked_force_active,
            }
        )


def main() -> None:
    """CLI entrypoint for fake JSON/JSONL preview sources."""

    parser = argparse.ArgumentParser(description="Run Stage 3 fake live preview.")
    parser.add_argument("raw_json_path", help="Path to a raw JSON or JSONL fake source.")
    args = parser.parse_args()
    run_preview(iter_raw_json_source(args.raw_json_path))


if __name__ == "__main__":
    main()
