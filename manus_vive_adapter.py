"""Adapter from stable DeviceFrame objects to Stage 2 ExperimentInputSample."""

from __future__ import annotations

from device_frame_models import DeviceAdapterConfig, DeviceFrame
from pinch_feature_extractor import PinchFeatureExtractor
from task_coordinate_system import TaskCoordinateSystem
from trial_controller import ExperimentInputSample


class ManusViveExperimentAdapter:
    """Convert parsed device frames into ExperimentInputSample objects.

    The original Stage 3 spatial model treats MANUS node positions as tracker-local:
    pinch_center_world = tracker_position_world + local_offset + pinch_center_local * local_scale.
    Real MANUS/Vive live streams can instead publish node positions already in world
    coordinates; set DeviceAdapterConfig.pinch_position_mode="nodes_world" for that case.
    """

    def __init__(
        self,
        task_coordinate_system: TaskCoordinateSystem | None,
        pinch_feature_extractor: PinchFeatureExtractor | None = None,
        config: DeviceAdapterConfig | None = None,
    ) -> None:
        self.task_coordinate_system = task_coordinate_system
        self.config = config or DeviceAdapterConfig()
        self.pinch_feature_extractor = (
            pinch_feature_extractor or PinchFeatureExtractor.from_config(self.config)
        )

    def to_experiment_input_sample(self, device_frame: DeviceFrame) -> ExperimentInputSample:
        """Convert one DeviceFrame into a Stage 2 input sample."""

        feature = self.pinch_feature_extractor.extract(device_frame.hand)
        if (
            device_frame.tracker is None
            or not device_frame.tracker.valid
            or device_frame.hand is None
            or not device_frame.hand.valid
            or not feature.valid
            or feature.pinch_center_local is None
        ):
            return ExperimentInputSample(
                time=device_frame.time,
                pinch_center_world=None,
                pinch_distance=feature.pinch_distance,
                tracker_valid=False,
                coordinate_space="world",
                subject_end=_subject_end(device_frame),
                metadata=_metadata(device_frame, feature.valid),
            )

        pinch_center_world = self._pinch_center_world(device_frame, feature.pinch_center_local)
        return ExperimentInputSample(
            time=device_frame.time,
            pinch_center_world=pinch_center_world,
            pinch_distance=feature.pinch_distance,
            tracker_valid=True,
            coordinate_space="world",
            subject_end=_subject_end(device_frame),
            metadata=_metadata(device_frame, feature.valid),
        )

    def _pinch_center_world(self, device_frame: DeviceFrame, pinch_center_node: object):
        pinch_component = pinch_center_node * self.config.local_scale + self.config.local_offset
        if self.config.pinch_position_mode == "nodes_world":
            return pinch_component
        return device_frame.tracker.pose_world.position + pinch_component


def _subject_end(device_frame: DeviceFrame) -> bool:
    if device_frame.raw is None:
        return False
    return bool(device_frame.raw.get("subject_end", False))


def _metadata(device_frame: DeviceFrame, pinch_valid: bool) -> dict[str, object]:
    return {
        "source_timestamp": device_frame.source_timestamp,
        "source_frame_id": device_frame.source_frame_id,
        "hand_valid": device_frame.hand.valid if device_frame.hand is not None else False,
        "tracker_valid": (
            device_frame.tracker.valid if device_frame.tracker is not None else False
        ),
        "pinch_valid": pinch_valid,
    }
