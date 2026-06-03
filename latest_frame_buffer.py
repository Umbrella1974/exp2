"""Latest-frame buffering helpers for low-latency live control loops."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LatestFrameBufferStats:
    """Thread-safe snapshot of latest-frame buffer state."""

    put_count: int
    consumed_count: int
    overwritten_frame_count: int
    dropped_old_frame_count: int
    last_frame_index: int | None
    last_receive_time: float | None
    has_unconsumed_frame: bool


class LatestFrameBuffer:
    """Keep only the most recent frame and consume each frame at most once."""

    def __init__(
        self,
        *,
        frame_published_callback: Callable[[Any, float, Any | None], None] | None = None,
        frame_consumed_callback: Callable[[Any, float], None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._latest_frame: Any | None = None
        self._latest_token = 0
        self._last_consumed_token = 0
        self._put_count = 0
        self._consumed_count = 0
        self._overwritten_frame_count = 0
        self._last_frame_index: int | None = None
        self._last_receive_time: float | None = None
        self.frame_published_callback = frame_published_callback
        self.frame_consumed_callback = frame_consumed_callback

    def put(self, frame: Any) -> None:
        """Store a new frame, overwriting any unconsumed older frame."""

        overwritten_frame = None
        with self._condition:
            if self._latest_frame is not None and self._latest_token != self._last_consumed_token:
                self._overwritten_frame_count += 1
                overwritten_frame = self._latest_frame
            self._latest_frame = frame
            self._latest_token += 1
            self._put_count += 1
            self._last_frame_index = _frame_index(frame)
            self._last_receive_time = _receive_time(frame)
            published_monotonic = time.monotonic()
            self._condition.notify_all()
        _safe_callback(
            self.frame_published_callback,
            frame,
            published_monotonic,
            overwritten_frame,
        )

    def get_latest(
        self,
        *,
        allow_already_consumed: bool = False,
        consume: bool = True,
    ) -> Any | None:
        """Return the latest frame, or None if no unconsumed frame is available."""

        consumed_frame = None
        consumed_monotonic = None
        with self._condition:
            if self._latest_frame is None:
                return None
            if not allow_already_consumed and self._latest_token == self._last_consumed_token:
                return None
            frame = self._latest_frame
            if consume and self._latest_token != self._last_consumed_token:
                self._last_consumed_token = self._latest_token
                self._consumed_count += 1
                consumed_frame = frame
                consumed_monotonic = time.monotonic()
        if consumed_frame is not None:
            _safe_callback(self.frame_consumed_callback, consumed_frame, consumed_monotonic)
        return frame

    def get_frame(self, timeout: float | None = None) -> Any | None:
        """Compatibility wrapper used by calibration collectors."""

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._latest_frame is None or self._latest_token == self._last_consumed_token:
                if timeout == 0.0:
                    return None
                if timeout is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic() if deadline is not None else 0.0
                if remaining <= 0.0:
                    return None
                self._condition.wait(remaining)
            frame = self._latest_frame
            self._last_consumed_token = self._latest_token
            self._consumed_count += 1
            consumed_monotonic = time.monotonic()
        _safe_callback(self.frame_consumed_callback, frame, consumed_monotonic)
        return frame

    def stats_snapshot(self) -> LatestFrameBufferStats:
        """Return a thread-safe immutable stats snapshot."""

        with self._condition:
            has_unconsumed = (
                self._latest_frame is not None
                and self._latest_token != self._last_consumed_token
            )
            return LatestFrameBufferStats(
                put_count=self._put_count,
                consumed_count=self._consumed_count,
                overwritten_frame_count=self._overwritten_frame_count,
                dropped_old_frame_count=self._overwritten_frame_count,
                last_frame_index=self._last_frame_index,
                last_receive_time=self._last_receive_time,
                has_unconsumed_frame=has_unconsumed,
            )


class LatestFramePump:
    """Background pump that drains a source into a LatestFrameBuffer."""

    def __init__(
        self,
        source: Any,
        buffer: LatestFrameBuffer,
        *,
        raw_frame_callback: Callable[[Any], None] | None = None,
        stop_event: threading.Event | None = None,
        poll_timeout: float = 0.05,
    ) -> None:
        self.source = source
        self.buffer = buffer
        self.raw_frame_callback = raw_frame_callback
        self.stop_event = stop_event or threading.Event()
        self.poll_timeout = float(poll_timeout)
        self._thread: threading.Thread | None = None
        self._started_source = False
        self._stop_reason: str | None = None

    def start(self) -> None:
        """Start source if needed and begin pumping frames."""

        if self._thread is not None and self._thread.is_alive():
            return
        self.stop_event.clear()
        if hasattr(self.source, "start"):
            self.source.start()
            self._started_source = True
        self._thread = threading.Thread(target=self._run, name="LatestFramePump", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "stopped") -> None:
        """Stop pumping and request source shutdown."""

        self._stop_reason = reason
        self.stop_event.set()
        if hasattr(self.source, "stop"):
            self.source.stop(reason)

    def join(self, timeout: float | None = None) -> None:
        """Wait for the pump thread to finish."""

        if self._thread is not None:
            self._thread.join(timeout)
        if self._started_source and hasattr(self.source, "join"):
            self.source.join(timeout)

    @property
    def stop_reason(self) -> str | None:
        """Return pump/source stop reason."""

        source_reason = _source_stop_reason(self.source)
        return source_reason or self._stop_reason

    def stats_snapshot(self) -> dict[str, Any]:
        """Return combined source and latest-buffer statistics."""

        stats = _source_stats(self.source)
        buffer_stats = self.buffer.stats_snapshot()
        stats.update(
            {
                "latest_put_count": buffer_stats.put_count,
                "latest_consumed_count": buffer_stats.consumed_count,
                "overwritten_frame_count": buffer_stats.overwritten_frame_count,
                "dropped_old_frame_count": buffer_stats.dropped_old_frame_count,
                "last_frame_index": buffer_stats.last_frame_index,
                "last_receive_time": buffer_stats.last_receive_time,
            }
        )
        return stats

    def _run(self) -> None:
        while not self.stop_event.is_set():
            frame = self.source.get_frame(timeout=self.poll_timeout)
            if frame is None:
                reason = _source_stop_reason(self.source)
                if reason in {"client_disconnected", "server_stopped", "socket_error", "eof"}:
                    self._stop_reason = reason
                    return
                if _source_is_stopped(self.source):
                    self._stop_reason = reason or "source_stopped"
                    return
                continue
            self.buffer.put(frame)
            if self.raw_frame_callback is not None:
                self.raw_frame_callback(frame)


def _frame_index(frame: Any) -> int | None:
    value = getattr(frame, "frame_index", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _receive_time(frame: Any) -> float | None:
    value = getattr(frame, "receive_time_monotonic", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _source_stats(source: Any) -> dict[str, Any]:
    if hasattr(source, "stats_snapshot"):
        snapshot = source.stats_snapshot()
        if isinstance(snapshot, dict):
            return dict(snapshot)
        if hasattr(snapshot, "__dict__"):
            return dict(snapshot.__dict__)
    return {
        "total_received_frames": getattr(source, "total_received_frames", None),
        "parse_error_count": getattr(source, "parse_error_count", 0),
        "bad_json_line_count": getattr(source, "bad_json_line_count", 0),
        "dropped_frame_count": getattr(source, "dropped_frame_count", 0),
        "stop_reason": _source_stop_reason(source),
    }


def _source_stop_reason(source: Any) -> str | None:
    value = getattr(source, "stop_reason", None)
    if callable(value):
        return value()
    return value


def _source_is_stopped(source: Any) -> bool:
    if hasattr(source, "stop_event"):
        return bool(source.stop_event.is_set())
    return bool(getattr(source, "stopped", False))


def _safe_callback(callback: Callable[..., None] | None, *args: Any) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:
        # Diagnostics must never interrupt the realtime frame path.
        return
