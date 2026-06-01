"""Tests for latest-frame buffering."""

from __future__ import annotations

from dataclasses import dataclass

from latest_frame_buffer import LatestFrameBuffer


@dataclass(frozen=True)
class FakeFrame:
    frame_index: int
    receive_time_monotonic: float


def test_put_multiple_frames_returns_latest() -> None:
    buffer = LatestFrameBuffer()

    buffer.put(FakeFrame(1, 1.0))
    buffer.put(FakeFrame(2, 2.0))
    frame = buffer.get_latest()

    assert frame.frame_index == 2
    stats = buffer.stats_snapshot()
    assert stats.last_frame_index == 2
    assert stats.last_receive_time == 2.0


def test_same_frame_is_not_consumed_twice_unless_allowed() -> None:
    buffer = LatestFrameBuffer()
    buffer.put(FakeFrame(1, 1.0))

    assert buffer.get_latest().frame_index == 1
    assert buffer.get_latest() is None
    assert buffer.get_latest(allow_already_consumed=True).frame_index == 1


def test_overwritten_count_tracks_unconsumed_frames() -> None:
    buffer = LatestFrameBuffer()

    buffer.put(FakeFrame(1, 1.0))
    buffer.put(FakeFrame(2, 2.0))
    buffer.put(FakeFrame(3, 3.0))

    stats = buffer.stats_snapshot()
    assert stats.overwritten_frame_count == 2
    assert stats.dropped_old_frame_count == 2
