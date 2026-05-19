"""Tests for geometry helpers."""

from __future__ import annotations

import pytest

from data_models import BlockedInfo, Box3D, Surface, TrackRegion, Vec3
from geometry import blocked_info_for_point, clamp_segment_to_track, point_in_box, point_in_track


def test_point_in_box() -> None:
    box = Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0))
    assert point_in_box(Vec3(0.0, 0.0, 0.0), box)
    assert point_in_box(Vec3(1.0, -1.0, 0.5), box)
    assert not point_in_box(Vec3(1.1, 0.0, 0.0), box)


def test_point_in_track_with_multiple_boxes() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(3.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )
    assert point_in_track(Vec3(0.5, 0.0, 0.0), track)
    assert point_in_track(Vec3(3.5, 0.0, 0.0), track)
    assert not point_in_track(Vec3(1.8, 1.8, 1.8), track)


def test_clamp_segment_to_track_returns_end_when_candidate_inside() -> None:
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),))
    result = clamp_segment_to_track(Vec3(0.0, 0.0, 0.0), Vec3(0.5, 0.0, 0.0), track)
    assert result.end_inside_track is True
    assert result.clamped_point == Vec3(0.5, 0.0, 0.0)
    assert result.blocked_info is None


def test_clamp_segment_to_track_returns_boundary_point_when_candidate_outside() -> None:
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),))
    result = clamp_segment_to_track(
        Vec3(0.5, 0.0, 0.0),
        Vec3(1.5, 0.0, 0.0),
        track,
        iterations=40,
        surface_threshold=0.05,
    )
    assert result.end_inside_track is False
    assert result.clamped_point.x == pytest.approx(1.0, abs=1e-6)
    assert result.clamped_point.y == pytest.approx(0.0)
    assert result.blocked_info is not None
    assert result.blocked_info.primary_blocked_surface == Surface.X_POS
    assert result.blocked_info.primary_blocked_amount == pytest.approx(0.5, abs=1e-4)
    assert result.blocked_info.blocked_vector.x == pytest.approx(0.5, abs=1e-9)
    assert result.blocked_info.blocked_vector.y == pytest.approx(0.0, abs=1e-9)
    assert result.blocked_info.blocked_vector.z == pytest.approx(0.0, abs=1e-9)
    assert result.blocked_info.blocked_distance == pytest.approx(0.5, abs=1e-4)


def test_clamp_segment_to_track_allows_continuous_face_touch_union() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(2.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )
    result = clamp_segment_to_track(Vec3(0.5, 0.0, 0.0), Vec3(2.5, 0.0, 0.0), track)
    assert result.end_inside_track is True
    assert result.clamped_point == Vec3(2.5, 0.0, 0.0)


def test_clamp_segment_to_track_allows_continuous_overlap_union() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(1.5, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )
    result = clamp_segment_to_track(Vec3(0.0, 0.0, 0.0), Vec3(2.2, 0.0, 0.0), track)
    assert result.end_inside_track is True
    assert result.clamped_point == Vec3(2.2, 0.0, 0.0)


def test_clamp_segment_to_track_stops_at_gap_even_if_endpoint_reenters_union() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(2.4, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )
    result = clamp_segment_to_track(
        Vec3(0.5, 0.0, 0.0),
        Vec3(1.6, 0.0, 0.0),
        track,
        iterations=40,
    )
    assert result.end_inside_track is False
    assert result.clamped_point.x == pytest.approx(1.0, abs=1e-6)
    assert result.blocked_info is not None
    assert result.blocked_info.blocked_vector.x == pytest.approx(0.6, abs=1e-4)


def test_clamp_segment_to_track_handles_continuous_l_shaped_union() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(4.0, 2.0, 2.0)),
            Box3D(center=Vec3(1.0, 1.0, 0.0), size=Vec3(2.0, 4.0, 2.0)),
        )
    )
    result = clamp_segment_to_track(Vec3(-1.0, 0.0, 0.0), Vec3(1.0, 2.0, 0.0), track)
    assert result.end_inside_track is True
    assert result.clamped_point == Vec3(1.0, 2.0, 0.0)


def test_clamp_segment_to_track_returns_last_legal_point_along_direction_for_union_exit() -> None:
    track = TrackRegion(
        boxes=(
            Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
            Box3D(center=Vec3(2.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),
        )
    )
    result = clamp_segment_to_track(
        Vec3(2.5, 0.0, 0.0),
        Vec3(4.5, 0.0, 0.0),
        track,
        iterations=40,
    )
    assert result.end_inside_track is False
    assert result.clamped_point.x == pytest.approx(3.0, abs=1e-6)
    assert result.blocked_info is not None
    assert result.blocked_info.primary_blocked_surface == Surface.X_POS


def test_blocked_info_records_primary_and_all_surfaces() -> None:
    track = TrackRegion(boxes=(Box3D(center=Vec3(0.0, 0.0, 0.0), size=Vec3(2.0, 2.0, 2.0)),))
    blocked_info: BlockedInfo = blocked_info_for_point(
        Vec3(1.2, 1.3, 0.0),
        track,
        surface_threshold=0.1,
    )
    assert blocked_info.primary_blocked_surface == Surface.Y_POS
    assert blocked_info.primary_blocked_amount == pytest.approx(0.3, abs=1e-4)
    assert blocked_info.all_blocked_surfaces == (Surface.X_POS, Surface.Y_POS)
