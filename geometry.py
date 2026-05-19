"""Geometry helpers for track and block queries."""

from __future__ import annotations

from math import inf

from data_models import BlockedInfo, Box3D, ClampResult, Surface, TrackRegion, Vec3


def point_in_box(point: Vec3, box: Box3D, epsilon: float = 0.0) -> bool:
    """Return whether a point lies inside an axis-aligned box."""

    minimum = box.min_corner
    maximum = box.max_corner
    return (
        minimum.x - epsilon <= point.x <= maximum.x + epsilon
        and minimum.y - epsilon <= point.y <= maximum.y + epsilon
        and minimum.z - epsilon <= point.z <= maximum.z + epsilon
    )


def point_in_track(point: Vec3, track_region: TrackRegion, epsilon: float = 0.0) -> bool:
    """Return whether a point lies inside the track union."""

    return any(point_in_box(point, box, epsilon=epsilon) for box in track_region.boxes)


def blocked_info_for_point(
    point: Vec3,
    track_region: TrackRegion,
    reference_box: Box3D | None = None,
    *,
    surface_threshold: float = 0.0,
) -> BlockedInfo:
    """Return blocked info using the nearest point on the track union."""

    del reference_box

    if point_in_track(point, track_region):
        return BlockedInfo(
            primary_blocked_surface=None,
            primary_blocked_amount=0.0,
            blocked_distance=0.0,
            blocked_vector=Vec3.zero(),
            all_blocked_surfaces=(),
        )

    closest_point = _closest_point_in_track(point, track_region)
    blocked_vector = point - closest_point
    return _blocked_info_from_vector(blocked_vector, surface_threshold)


def clamp_segment_to_track(
    start: Vec3,
    end: Vec3,
    track_region: TrackRegion,
    *,
    epsilon: float = 0.0,
    iterations: int = 24,
    surface_threshold: float = 0.0,
) -> ClampResult:
    """Clamp a segment end to the last point on the segment that stays in track.

    This returns the furthest point reachable along start -> end while remaining
    continuously inside the track union. It does not allow teleporting across
    gaps even if the final endpoint lies inside a later box in the union.
    """

    if not point_in_track(start, track_region, epsilon=epsilon):
        raise ValueError("Segment start must already be inside the track.")

    coverage_end = _continuous_coverage_end(start, end, track_region, epsilon)
    if coverage_end >= 1.0:
        return ClampResult(clamped_point=end, end_inside_track=True, blocked_info=None)

    low = max(0.0, coverage_end)
    high = min(1.0, coverage_end + 1.0 / max(iterations, 1))
    if high <= low:
        high = min(1.0, low + 1e-6)

    for _ in range(iterations):
        mid = (low + high) * 0.5
        sample = lerp_vec3(start, end, mid)
        if _segment_prefix_inside(start, end, track_region, mid, epsilon):
            low = mid
        else:
            high = mid

    clamped_point = lerp_vec3(start, end, low)
    blocked_info = _blocked_info_from_vector(end - clamped_point, surface_threshold)
    return ClampResult(
        clamped_point=clamped_point,
        end_inside_track=False,
        blocked_info=blocked_info,
    )


def lerp_vec3(start: Vec3, end: Vec3, t: float) -> Vec3:
    """Linearly interpolate between two vectors."""

    return start + (end - start).scale(t)


def _continuous_coverage_end(
    start: Vec3,
    end: Vec3,
    track_region: TrackRegion,
    epsilon: float,
) -> float:
    intervals = []
    for box in track_region.boxes:
        interval = _box_segment_interval(start, end, box, epsilon)
        if interval is not None:
            intervals.append(interval)

    if not intervals:
        return 0.0

    intervals.sort(key=lambda item: (item[0], item[1]))
    merged_start, merged_end = intervals[0]
    if merged_start > epsilon:
        return 0.0

    for interval_start, interval_end in intervals[1:]:
        if interval_start <= merged_end + epsilon:
            merged_end = max(merged_end, interval_end)
            continue
        break

    return min(1.0, max(0.0, merged_end))


def _box_segment_interval(
    start: Vec3,
    end: Vec3,
    box: Box3D,
    epsilon: float,
) -> tuple[float, float] | None:
    minimum = box.min_corner
    maximum = box.max_corner
    low = 0.0
    high = 1.0

    for start_value, end_value, minimum_value, maximum_value in (
        (start.x, end.x, minimum.x - epsilon, maximum.x + epsilon),
        (start.y, end.y, minimum.y - epsilon, maximum.y + epsilon),
        (start.z, end.z, minimum.z - epsilon, maximum.z + epsilon),
    ):
        delta = end_value - start_value
        if abs(delta) <= 1e-12:
            if not (minimum_value <= start_value <= maximum_value):
                return None
            continue

        axis_t0 = (minimum_value - start_value) / delta
        axis_t1 = (maximum_value - start_value) / delta
        axis_low = min(axis_t0, axis_t1)
        axis_high = max(axis_t0, axis_t1)
        low = max(low, axis_low)
        high = min(high, axis_high)
        if low > high:
            return None

    clipped_low = max(0.0, low)
    clipped_high = min(1.0, high)
    if clipped_low > clipped_high:
        return None
    return (clipped_low, clipped_high)


def _segment_prefix_inside(
    start: Vec3,
    end: Vec3,
    track_region: TrackRegion,
    t: float,
    epsilon: float,
) -> bool:
    return _continuous_coverage_end(start, lerp_vec3(start, end, t), track_region, epsilon) >= 1.0 - 1e-12


def _closest_point_in_track(point: Vec3, track_region: TrackRegion) -> Vec3:
    if not track_region.boxes:
        raise ValueError("TrackRegion must contain at least one box.")

    best_point: Vec3 | None = None
    best_distance = inf
    for box in track_region.boxes:
        candidate = _closest_point_on_box(point, box)
        distance = point.distance_to(candidate)
        if distance < best_distance:
            best_distance = distance
            best_point = candidate

    assert best_point is not None
    return best_point


def _closest_point_on_box(point: Vec3, box: Box3D) -> Vec3:
    minimum = box.min_corner
    maximum = box.max_corner
    return Vec3(
        _clamp_scalar(point.x, minimum.x, maximum.x),
        _clamp_scalar(point.y, minimum.y, maximum.y),
        _clamp_scalar(point.z, minimum.z, maximum.z),
    )


def _clamp_scalar(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def _blocked_info_from_vector(
    blocked_vector: Vec3,
    surface_threshold: float,
) -> BlockedInfo:
    positive_components = {
        Surface.X_POS: max(blocked_vector.x, 0.0),
        Surface.X_NEG: max(-blocked_vector.x, 0.0),
        Surface.Y_POS: max(blocked_vector.y, 0.0),
        Surface.Y_NEG: max(-blocked_vector.y, 0.0),
        Surface.Z_POS: max(blocked_vector.z, 0.0),
        Surface.Z_NEG: max(-blocked_vector.z, 0.0),
    }

    primary_surface = None
    primary_amount = 0.0
    for surface, amount in positive_components.items():
        if amount > primary_amount:
            primary_surface = surface
            primary_amount = amount

    all_surfaces = tuple(
        surface
        for surface, amount in positive_components.items()
        if amount > surface_threshold
    )

    return BlockedInfo(
        primary_blocked_surface=primary_surface,
        primary_blocked_amount=primary_amount,
        blocked_distance=blocked_vector.norm(),
        blocked_vector=blocked_vector,
        all_blocked_surfaces=all_surfaces,
    )
