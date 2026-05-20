"""Smoke-test live preview for the Stage 3 raw-frame adapter pipeline.

This script is not a formal experiment UI. It only pulls raw dictionaries from a
RawFrameSource and prints pipeline summaries for adapter/debug smoke tests.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import Any

from block_controller import BlockController
from calibration_io import load_task_calibration
from config import EngineConfig
from data_models import Box3D, TrackRegion, Vec3
from device_frame_models import DeviceAdapterConfig
from manus_vive_adapter import ManusViveExperimentAdapter
from raw_frame_source import JsonlRawFrameSource, RawFrameSource
from raw_manus_vive_parser import parse_raw_manus_vive_frame
from task_coordinate_system import build_from_origin_and_x_point
from trial_controller import TrialController


def run_preview(
    raw_frames: Iterable[dict[str, Any]],
    *,
    max_frames: int | None = None,
    print_every: int = 1,
    calibration_path: str | None = None,
) -> None:
    """Run raw frames through parser, adapter, TrialController, and BlockController."""

    engine_config = EngineConfig()
    adapter_config = DeviceAdapterConfig()
    task_system = (
        load_task_calibration(calibration_path)
        if calibration_path is not None
        else _demo_task_coordinate_system()
    )
    track = TrackRegion(
        boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 4.0, 4.0)),)
    )

    def factory() -> BlockController:
        return BlockController(engine_config, track, Vec3(0.0, 0.0, 0.0))

    trial_controller = TrialController(factory, task_system, engine_config)
    trial_controller.start_trial(time=0.0, trial_id="preview")
    adapter = ManusViveExperimentAdapter(task_system, config=adapter_config)

    for frame_index, raw in enumerate(raw_frames):
        if max_frames is not None and frame_index >= max_frames:
            break

        device_frame = parse_raw_manus_vive_frame(raw, adapter_config)
        sample = adapter.to_experiment_input_sample(device_frame)
        result = trial_controller.update(sample)
        output = result.frame_output
        if frame_index % print_every == 0:
            print(_preview_summary(frame_index, sample, result))


def raw_source_from_args(args: argparse.Namespace) -> RawFrameSource:
    """Build a RawFrameSource from CLI args."""

    if args.raw_jsonl is None:
        raise ValueError("--raw-jsonl is required for Stage 3.1 preview.")
    return JsonlRawFrameSource(args.raw_jsonl)


def _demo_task_coordinate_system():
    return build_from_origin_and_x_point(
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    )


def _preview_summary(frame_index, sample, result):
    output = result.frame_output
    blocked_info = output.feedback_state.blocked_info
    return {
        "frame_index": frame_index,
        "time": result.frame_output.time,
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
        "trial_state": result.trial_state.name,
        "contact_state": output.contact_state.name,
        "pinch_state": output.pinch_state.name,
        "block_motion_state": output.block_state.motion_state.name,
        "stop_reason": output.feedback_state.stop_reason.name,
        "track_state": output.feedback_state.track_state.name,
        "detach_state": output.feedback_state.detach_state.name,
        "slip_active": output.haptic_feedback.slip_active,
        "slip_reason": output.haptic_feedback.slip_reason.name
        if output.haptic_feedback.slip_reason is not None
        else None,
        "blocked_force_active": output.haptic_feedback.blocked_force_active,
        "primary_blocked_surface": blocked_info.primary_blocked_surface.name
        if blocked_info is not None and blocked_info.primary_blocked_surface is not None
        else None,
        "primary_blocked_amount": blocked_info.primary_blocked_amount
        if blocked_info is not None
        else None,
    }


def main() -> None:
    """CLI entrypoint for fake JSON/JSONL preview sources."""

    parser = argparse.ArgumentParser(description="Run Stage 3 fake live preview.")
    parser.add_argument("--raw-jsonl", help="Path to a raw JSONL fake source.")
    parser.add_argument("--max-frames", type=int, default=None, help="Maximum frames to process.")
    parser.add_argument("--calibration", default=None, help="Optional task calibration JSON.")
    parser.add_argument("--print-every", type=int, default=1, help="Print every N frames.")
    args = parser.parse_args()
    if args.print_every <= 0:
        raise ValueError("--print-every must be positive.")

    source = raw_source_from_args(args)
    try:
        run_preview(
            source,
            max_frames=args.max_frames,
            print_every=args.print_every,
            calibration_path=args.calibration,
        )
    finally:
        if hasattr(source, "close"):
            source.close()


if __name__ == "__main__":
    main()
