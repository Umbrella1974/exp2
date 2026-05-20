"""Tests for DeviceFrame to ExperimentInputSample adaptation."""

from __future__ import annotations

import numpy as np
import pytest

from device_frame_models import (
    DeviceAdapterConfig,
    DeviceFrame,
    ManusHandFrame,
    ManusNodeData,
    Pose3D,
    ViveTrackerFrame,
)
from manus_vive_adapter import ManusViveExperimentAdapter


def make_device_frame(
    *,
    tracker_valid: bool = True,
    hand_valid: bool = True,
    include_index: bool = True,
) -> DeviceFrame:
    nodes = {4: ManusNodeData(4, [0.0, 0.0, 0.0])}
    if include_index:
        nodes[9] = ManusNodeData(9, [0.02, 0.0, 0.0])
    return DeviceFrame(
        time=1.0,
        source_timestamp=1.0,
        source_frame_id=1,
        tracker=ViveTrackerFrame(
            tracker_index=0,
            tracker_id="tracker",
            pose_world=Pose3D([1.0, 2.0, 3.0]),
            valid=tracker_valid,
        ),
        hand=ManusHandFrame(
            glove_id="glove",
            side=None,
            nodes=nodes,
            valid=hand_valid,
        ),
        raw={"subject_end": True},
    )


def test_device_adapter_config_normalizes_offset_and_validates_scale() -> None:
    config = DeviceAdapterConfig(local_offset=[1.0, 2.0, 3.0], local_scale=2)
    assert isinstance(config.local_offset, np.ndarray)
    assert config.local_offset.shape == (3,)
    assert config.local_scale == 2.0
    with pytest.raises(ValueError):
        DeviceAdapterConfig(local_offset=[1.0, 2.0])
    with pytest.raises(ValueError):
        DeviceAdapterConfig(local_scale=float("inf"))


def test_valid_device_frame_outputs_world_sample() -> None:
    adapter = ManusViveExperimentAdapter(
        None,
        config=DeviceAdapterConfig(local_offset=[0.1, 0.0, 0.0], local_scale=2.0),
    )
    sample = adapter.to_experiment_input_sample(make_device_frame())
    assert sample.tracker_valid is True
    assert sample.coordinate_space == "world"
    assert np.allclose(sample.pinch_center_world, [1.12, 2.0, 3.0])
    assert sample.pinch_distance == pytest.approx(0.02)
    assert sample.subject_end is True


def test_tracker_invalid_outputs_invalid_sample() -> None:
    sample = ManusViveExperimentAdapter(None).to_experiment_input_sample(
        make_device_frame(tracker_valid=False)
    )
    assert sample.tracker_valid is False
    assert sample.pinch_center_world is None


def test_hand_invalid_outputs_invalid_sample() -> None:
    sample = ManusViveExperimentAdapter(None).to_experiment_input_sample(
        make_device_frame(hand_valid=False)
    )
    assert sample.tracker_valid is False
    assert sample.pinch_center_world is None


def test_pinch_invalid_outputs_invalid_sample() -> None:
    sample = ManusViveExperimentAdapter(None).to_experiment_input_sample(
        make_device_frame(include_index=False)
    )
    assert sample.tracker_valid is False
    assert sample.pinch_center_world is None


def test_adapter_does_not_require_or_call_block_controller() -> None:
    adapter = ManusViveExperimentAdapter(None)
    sample = adapter.to_experiment_input_sample(make_device_frame())
    assert sample.coordinate_space == "world"
