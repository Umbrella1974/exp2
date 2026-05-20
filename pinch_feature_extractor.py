"""Pinch feature extraction from parsed MANUS hand frames."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from device_frame_models import DeviceAdapterConfig, ManusHandFrame


@dataclass(frozen=True)
class PinchFeature:
    """Pinch center and distance derived from two fingertip nodes."""

    thumb_tip_local: np.ndarray | None
    index_tip_local: np.ndarray | None
    pinch_center_local: np.ndarray | None
    pinch_distance: float | None
    valid: bool


class PinchFeatureExtractor:
    """Extract pinch center and pinch distance from MANUS node positions."""

    def __init__(
        self,
        thumb_tip_node_id: int = 4,
        index_tip_node_id: int = 9,
    ) -> None:
        self.thumb_tip_node_id = int(thumb_tip_node_id)
        self.index_tip_node_id = int(index_tip_node_id)

    @classmethod
    def from_config(cls, config: DeviceAdapterConfig) -> "PinchFeatureExtractor":
        """Create an extractor from adapter config node ids."""

        return cls(
            thumb_tip_node_id=config.thumb_tip_node_id,
            index_tip_node_id=config.index_tip_node_id,
        )

    def extract(self, hand_frame: ManusHandFrame | None) -> PinchFeature:
        """Extract pinch features; return invalid output if required nodes are missing."""

        if hand_frame is None or not hand_frame.valid:
            return _invalid_feature()

        thumb = hand_frame.nodes.get(self.thumb_tip_node_id)
        index = hand_frame.nodes.get(self.index_tip_node_id)
        if thumb is None or index is None:
            return _invalid_feature()

        center = (thumb.position + index.position) * 0.5
        distance = float(np.linalg.norm(thumb.position - index.position))
        return PinchFeature(
            thumb_tip_local=thumb.position,
            index_tip_local=index.position,
            pinch_center_local=center,
            pinch_distance=distance,
            valid=True,
        )


def _invalid_feature() -> PinchFeature:
    return PinchFeature(
        thumb_tip_local=None,
        index_tip_local=None,
        pinch_center_local=None,
        pinch_distance=None,
        valid=False,
    )
