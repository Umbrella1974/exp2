"""Parser from loose MANUS/Vive combined JSON into stable DeviceFrame models."""

from __future__ import annotations

import time as time_module
from typing import Any

from device_frame_models import (
    DeviceAdapterConfig,
    DeviceFrame,
    ManusHandFrame,
    ManusNodeData,
    Pose3D,
    ViveTrackerFrame,
)


def parse_raw_manus_vive_frame(
    raw: dict[str, Any],
    config: DeviceAdapterConfig | None = None,
) -> DeviceFrame:
    """Parse a raw combined JSON dict into a stable DeviceFrame."""

    if not isinstance(raw, dict):
        raise ValueError("raw must be a dict.")

    config = config or DeviceAdapterConfig()
    source_timestamp = raw.get("timestamp")
    frame_id = _optional_int(raw.get("frame"))
    return DeviceFrame(
        time=_frame_time(source_timestamp, config.timestamp_scale),
        source_timestamp=source_timestamp,
        source_frame_id=frame_id,
        tracker=_parse_tracker(raw, config),
        hand=_parse_hand(raw, config),
        raw=raw,
    )


def _frame_time(source_timestamp: object, timestamp_scale: float) -> float:
    try:
        return float(source_timestamp) * timestamp_scale
    except (TypeError, ValueError):
        return time_module.time()


def _parse_hand(raw: dict[str, Any], config: DeviceAdapterConfig) -> ManusHandFrame | None:
    skeletons = raw.get("skeletons")
    if not isinstance(skeletons, list) or config.skeleton_index >= len(skeletons):
        return None

    skeleton = skeletons[config.skeleton_index]
    if not isinstance(skeleton, dict):
        return ManusHandFrame(glove_id=None, side=None, nodes={}, valid=False)

    nodes_raw = skeleton.get("nodes")
    nodes: dict[int, ManusNodeData] = {}
    if isinstance(nodes_raw, list):
        for node in nodes_raw:
            parsed_node = _parse_node(node)
            if parsed_node is not None:
                nodes[parsed_node.node_id] = parsed_node

    return ManusHandFrame(
        glove_id=_optional_str(skeleton.get("gloveId", skeleton.get("glove_id"))),
        side=_optional_str(
            skeleton.get("side", skeleton.get("handedness", skeleton.get("hand")))
        ),
        nodes=nodes,
        valid=bool(nodes),
    )


def _parse_node(node: object) -> ManusNodeData | None:
    if not isinstance(node, dict):
        return None
    node_id = _optional_int(node.get("id", node.get("node_id")))
    position = node.get("position")
    if node_id is None or position is None:
        return None
    try:
        return ManusNodeData(
            node_id=node_id,
            position=position,
            rotation=node.get("rotation"),
        )
    except ValueError:
        return None


def _parse_tracker(raw: dict[str, Any], config: DeviceAdapterConfig) -> ViveTrackerFrame | None:
    trackers = raw.get("trackers")
    if not isinstance(trackers, list) or config.tracker_index >= len(trackers):
        return None

    tracker = trackers[config.tracker_index]
    if not isinstance(tracker, dict) or tracker.get("position") is None:
        return None

    try:
        pose = Pose3D(
            position=tracker.get("position"),
            rotation=tracker.get("rotation"),
        )
    except ValueError:
        return None

    return ViveTrackerFrame(
        tracker_index=config.tracker_index,
        tracker_id=_optional_str(tracker.get("trackerId", tracker.get("id"))),
        pose_world=pose,
        valid=bool(tracker.get("valid", True)),
        quality=tracker.get("quality"),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
