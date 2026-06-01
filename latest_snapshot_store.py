"""Thread-safe latest-snapshot store for debug GUI subscribers.

The store deliberately keeps only the newest DashboardSnapshot. It never queues
old snapshots, so a slow GUI cannot build up display latency.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LatestSnapshotStoreStats:
    """Immutable diagnostics for LatestSnapshotStore."""

    update_count: int
    read_count: int
    dropped_snapshot_count: int
    last_frame_index: int | None
    last_update_monotonic: float | None
    has_snapshot: bool
    has_unread_snapshot: bool
    gui_closed: bool


class LatestSnapshotStore:
    """Keep only the latest snapshot for GUI polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: Any | None = None
        self._update_count = 0
        self._read_count = 0
        self._dropped_snapshot_count = 0
        self._last_frame_index: int | None = None
        self._last_update_monotonic: float | None = None
        self._has_unread_snapshot = False
        self._gui_closed = False

    def publish(self, snapshot: Any) -> None:
        """Publish a new snapshot, replacing any unread older snapshot."""

        with self._lock:
            if self._has_unread_snapshot:
                self._dropped_snapshot_count += 1
            self._snapshot = snapshot
            self._update_count += 1
            self._last_frame_index = _frame_index(snapshot)
            self._last_update_monotonic = time.monotonic()
            self._has_unread_snapshot = True

    def get_latest(self, *, mark_read: bool = True) -> Any | None:
        """Return the latest snapshot without blocking."""

        with self._lock:
            snapshot = self._snapshot
            if snapshot is not None and mark_read:
                self._read_count += 1
                self._has_unread_snapshot = False
            return snapshot

    def snapshot_age_seconds(self, now: float | None = None) -> float | None:
        """Return age of the latest snapshot, or None when empty."""

        with self._lock:
            if self._last_update_monotonic is None:
                return None
            current = time.monotonic() if now is None else float(now)
            return max(0.0, current - self._last_update_monotonic)

    def mark_gui_closed(self) -> None:
        """Record that the GUI display was closed."""

        with self._lock:
            self._gui_closed = True

    def stats_snapshot(self) -> LatestSnapshotStoreStats:
        """Return immutable store diagnostics."""

        with self._lock:
            return LatestSnapshotStoreStats(
                update_count=self._update_count,
                read_count=self._read_count,
                dropped_snapshot_count=self._dropped_snapshot_count,
                last_frame_index=self._last_frame_index,
                last_update_monotonic=self._last_update_monotonic,
                has_snapshot=self._snapshot is not None,
                has_unread_snapshot=self._has_unread_snapshot,
                gui_closed=self._gui_closed,
            )


def _frame_index(snapshot: Any) -> int | None:
    value = getattr(snapshot, "frame_index", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
