"""Tests for latest-only debug snapshot storage."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from latest_snapshot_store import LatestSnapshotStore


@dataclass(frozen=True)
class _Snapshot:
    frame_index: int


def test_latest_snapshot_store_keeps_only_newest_snapshot() -> None:
    store = LatestSnapshotStore()

    store.publish(_Snapshot(1))
    store.publish(_Snapshot(2))

    assert store.get_latest().frame_index == 2
    stats = store.stats_snapshot()
    assert stats.update_count == 2
    assert stats.read_count == 1
    assert stats.dropped_snapshot_count == 1
    assert stats.last_frame_index == 2
    assert stats.has_unread_snapshot is False


def test_latest_snapshot_store_thread_safe_latest_wins() -> None:
    store = LatestSnapshotStore()

    def publish_range(start: int) -> None:
        for frame_index in range(start, start + 100):
            store.publish(_Snapshot(frame_index))

    threads = [threading.Thread(target=publish_range, args=(index * 1000,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    snapshot = store.get_latest()
    stats = store.stats_snapshot()
    assert snapshot is not None
    assert stats.update_count == 400
    assert stats.has_snapshot is True
    assert stats.last_frame_index == snapshot.frame_index


def test_mark_gui_closed_is_reported() -> None:
    store = LatestSnapshotStore()

    store.mark_gui_closed()

    assert store.stats_snapshot().gui_closed is True
