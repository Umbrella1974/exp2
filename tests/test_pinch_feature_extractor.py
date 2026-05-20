"""Tests for MANUS pinch feature extraction."""

from __future__ import annotations

import numpy as np

from device_frame_models import ManusHandFrame, ManusNodeData
from pinch_feature_extractor import PinchFeatureExtractor


def hand_with_nodes(nodes: dict[int, ManusNodeData]) -> ManusHandFrame:
    return ManusHandFrame(glove_id=None, side=None, nodes=nodes, valid=bool(nodes))


def test_extracts_center_and_distance_for_default_nodes() -> None:
    hand = hand_with_nodes(
        {
            4: ManusNodeData(4, [0.0, 0.0, 0.0]),
            9: ManusNodeData(9, [0.02, 0.0, 0.0]),
        }
    )
    feature = PinchFeatureExtractor().extract(hand)
    assert feature.valid is True
    assert np.allclose(feature.pinch_center_local, [0.01, 0.0, 0.0])
    assert feature.pinch_distance == 0.02


def test_missing_node_returns_invalid() -> None:
    hand = hand_with_nodes({4: ManusNodeData(4, [0.0, 0.0, 0.0])})
    feature = PinchFeatureExtractor().extract(hand)
    assert feature.valid is False
    assert feature.pinch_center_local is None


def test_configurable_node_ids() -> None:
    hand = hand_with_nodes(
        {
            10: ManusNodeData(10, [0.0, 1.0, 0.0]),
            11: ManusNodeData(11, [0.0, 3.0, 0.0]),
        }
    )
    feature = PinchFeatureExtractor(thumb_tip_node_id=10, index_tip_node_id=11).extract(hand)
    assert feature.valid is True
    assert np.allclose(feature.pinch_center_local, [0.0, 2.0, 0.0])
    assert feature.pinch_distance == 2.0
