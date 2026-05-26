"""Simple rule-based map generation helpers.

The first generator only creates xoy-plane orthogonal corridor maps from
axis-aligned AABB segments. All turns are 90 degrees; diagonal segments,
arbitrary angles, 180-degree turns, and 3D mazes are intentionally out of scope.
It is a basic tooling layer, not a formal random experiment design system.
"""

from __future__ import annotations

import random
from typing import Sequence

from map_config import MapBoxSpec, MapConfig


DIRECTIONS = {
    "x+": (1.0, 0.0),
    "x-": (-1.0, 0.0),
    "y+": (0.0, 1.0),
    "y-": (0.0, -1.0),
}

LEFT_TURN = {
    "x+": "y+",
    "y+": "x-",
    "x-": "y-",
    "y-": "x+",
}

RIGHT_TURN = {
    "x+": "y-",
    "y-": "x-",
    "x-": "y+",
    "y+": "x+",
}


def generate_orthogonal_corridor_map(
    *,
    map_id: str,
    seed: int,
    num_segments: int,
    start: Sequence[float],
    initial_direction: str,
    segment_length_range: tuple[float, float],
    track_width: float,
    z_tolerance: float,
    allowed_turns: Sequence[str],
    plane: str = "xoy",
    junction_overlap: float = 0.03,
    target_length: float | None = None,
) -> MapConfig:
    """Generate a simple continuous xoy orthogonal corridor map."""

    if plane != "xoy":
        raise NotImplementedError("generate_orthogonal_corridor_map currently supports only plane='xoy'.")
    if initial_direction not in DIRECTIONS:
        raise ValueError('initial_direction must be one of "x+", "x-", "y+", "y-".')
    if num_segments <= 0:
        raise ValueError("num_segments must be > 0.")
    if len(start) != 3:
        raise ValueError("start must contain three coordinates.")
    if track_width <= 0:
        raise ValueError("track_width must be > 0.")
    if z_tolerance <= 0:
        raise ValueError("z_tolerance must be > 0.")
    min_length, max_length = segment_length_range
    if min_length <= 0 or max_length < min_length:
        raise ValueError("segment_length_range must be positive and ordered.")
    unsupported_turns = set(allowed_turns) - {"left", "right", "straight"}
    if unsupported_turns:
        raise ValueError(f"unsupported turns: {', '.join(sorted(unsupported_turns))}")
    if not allowed_turns:
        raise ValueError("allowed_turns must not be empty.")
    if target_length is not None and target_length <= 0:
        raise ValueError("target_length must be > 0 when provided.")

    rng = random.Random(seed)
    current_point = [float(start[0]), float(start[1])]
    z = float(start[2])
    direction = initial_direction
    boxes: list[MapBoxSpec] = []
    chosen_turns: list[str] = []
    segment_records: list[dict[str, object]] = []

    for index in range(num_segments):
        turn = "straight" if index == 0 else rng.choice(list(allowed_turns))
        direction = _apply_turn(direction, turn)
        chosen_turns.append(turn)
        length = rng.uniform(float(min_length), float(max_length))
        dx, dy = DIRECTIONS[direction]
        end_point = [
            current_point[0] + dx * length,
            current_point[1] + dy * length,
        ]
        box_min, box_max = _segment_bounds(
            current_point,
            end_point,
            z,
            direction,
            track_width,
            z_tolerance,
            junction_overlap,
            include_start_overlap=index > 0,
            include_end_overlap=index < num_segments - 1,
        )
        boxes.append(
            MapBoxSpec(
                id=f"segment_{index:02d}",
                order=index,
                label=f"Segment {index + 1}",
                min=box_min,
                max=box_max,
                metadata={
                    "segment_direction": direction,
                    "direction": direction,
                    "segment_length": length,
                    "turn_from_previous": turn,
                },
            )
        )
        segment_records.append(
            {
                "id": f"segment_{index:02d}",
                "start": list(current_point),
                "end": list(end_point),
                "direction": direction,
                "length": length,
            }
        )
        current_point = end_point

    target_region = _target_region_for_last_segment(
        last_segment=segment_records[-1],
        last_box=boxes[-1],
        requested_target_length=target_length,
    )

    return MapConfig(
        map_id=map_id,
        description="Generated xoy orthogonal corridor map.",
        coordinate_space="task",
        unit="m",
        block_initial_center_task=[float(start[0]), float(start[1]), float(start[2])],
        block_size=[track_width, track_width, z_tolerance * 2.0],
        track_boxes=boxes,
        target_region=target_region,
        metadata={
            "generated": True,
            "generator_name": "generate_orthogonal_corridor_map",
            "generator_seed": seed,
            "generator_params": {
                "num_segments": num_segments,
                "start": [float(start[0]), float(start[1]), float(start[2])],
                "initial_direction": initial_direction,
                "segment_length_range": [float(min_length), float(max_length)],
                "track_width": track_width,
                "z_tolerance": z_tolerance,
                "allowed_turns": list(allowed_turns),
                "plane": plane,
                "junction_overlap": junction_overlap,
                "target_length": target_region.metadata["target_length"],
                "requested_target_length": target_length,
                "turns": chosen_turns,
            },
        },
    )


def _apply_turn(direction: str, turn: str) -> str:
    if turn == "straight":
        return direction
    if turn == "left":
        return LEFT_TURN[direction]
    if turn == "right":
        return RIGHT_TURN[direction]
    raise ValueError(f"unsupported turn: {turn}")


def _segment_bounds(
    start: list[float],
    end: list[float],
    z: float,
    direction: str,
    track_width: float,
    z_tolerance: float,
    junction_overlap: float,
    *,
    include_start_overlap: bool,
    include_end_overlap: bool,
) -> tuple[list[float], list[float]]:
    half_width = track_width / 2.0
    start_extension = junction_overlap if include_start_overlap else 0.0
    end_extension = junction_overlap if include_end_overlap else 0.0

    if direction in ("x+", "x-"):
        x0, x1 = sorted([start[0], end[0]])
        if direction == "x+":
            x0 -= start_extension
            x1 += end_extension
        else:
            x0 -= end_extension
            x1 += start_extension
        y0 = start[1] - half_width
        y1 = start[1] + half_width
    else:
        y0, y1 = sorted([start[1], end[1]])
        if direction == "y+":
            y0 -= start_extension
            y1 += end_extension
        else:
            y0 -= end_extension
            y1 += start_extension
        x0 = start[0] - half_width
        x1 = start[0] + half_width

    return [x0, y0, z - z_tolerance], [x1, y1, z + z_tolerance]


def _target_region_for_last_segment(
    *,
    last_segment: dict[str, object],
    last_box: MapBoxSpec,
    requested_target_length: float | None,
) -> MapBoxSpec:
    segment_length = float(last_segment["length"])
    direction = str(last_segment["direction"])
    target_length = (
        min(0.20, segment_length * 0.25)
        if requested_target_length is None
        else float(requested_target_length)
    )
    warnings: list[str] = []
    if target_length > segment_length:
        warnings.append("target_length exceeded last segment length and was clamped.")
        target_length = segment_length

    box_min = list(last_box.min)
    box_max = list(last_box.max)
    if direction == "x+":
        box_min[0] = box_max[0] - target_length
    elif direction == "x-":
        box_max[0] = box_min[0] + target_length
    elif direction == "y+":
        box_min[1] = box_max[1] - target_length
    elif direction == "y-":
        box_max[1] = box_min[1] + target_length

    return MapBoxSpec(
        id="target",
        order=None,
        label="Target region",
        min=box_min,
        max=box_max,
        metadata={
            "type": "target_region",
            "based_on_segment_id": last_segment["id"],
            "target_length": target_length,
            "requested_target_length": requested_target_length,
            "warnings": warnings,
        },
    )
