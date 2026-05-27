"""Simulated live source backed by raw JSONL frames.

This source is for testing live-style calibration flow without hardware. It
keeps raw frames unchanged and only wraps them with LiveRawFrame timing data.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from live_raw_stream import LiveRawFrame, LiveRawStreamStats, QUEUE_DROP_POLICY
from raw_frame_source import JsonlRawFrameSource


class RawJsonlSimulatedLiveSource:
    """Read raw JSONL frames through the same get_frame() shape as live TCP."""

    def __init__(
        self,
        raw_jsonl: str | Path,
        *,
        timestamp_scale: float = 0.001,
        real_time: bool = False,
        speed: float = 1.0,
        max_frames: int | None = None,
        start_frame: int = 0,
    ) -> None:
        if speed <= 0.0:
            raise ValueError("speed must be > 0.")
        if max_frames is not None and max_frames <= 0:
            raise ValueError("max_frames must be > 0.")
        if start_frame < 0:
            raise ValueError("start_frame must be >= 0.")
        self.raw_jsonl = Path(raw_jsonl)
        self.timestamp_scale = float(timestamp_scale)
        self.real_time = bool(real_time)
        self.speed = float(speed)
        self.max_frames = max_frames
        self.start_frame = int(start_frame)
        self.enforces_monotonic_frame_time = True

        self._source = JsonlRawFrameSource(self.raw_jsonl)
        self._started = False
        self._closed = False
        self._skipped = 0
        self._returned = 0
        self._last_frame_time: float | None = None
        self._last_wall_time: float | None = None
        self.stop_reason: str | None = None
        self.stopped = False

    def start(self) -> None:
        """Mark the source as running.

        The file is opened during construction so tests can use get_frame()
        directly, but the method mirrors LiveRawStreamServer.
        """

        self._started = True
        self.stopped = False
        self.stop_reason = None

    def stop(self, reason: str = "source_stopped") -> None:
        """Close the underlying file source."""

        if self.stop_reason is None:
            self.stop_reason = reason
        self.stopped = True
        self._closed = True
        self._source.close()

    def join(self, timeout: float | None = None) -> None:
        """Compatibility no-op for live-source callers."""

        del timeout

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame | None:
        """Return the next simulated live frame, or None at EOF."""

        del timeout
        if self._closed:
            return None
        if self.max_frames is not None and self._returned >= self.max_frames:
            self._finish("max_frames")
            return None

        raw = self._next_raw_after_start_frame()
        if raw is None:
            self._finish("eof")
            return None

        frame_time = self._strict_frame_time(raw)
        if self._last_frame_time is not None and frame_time < self._last_frame_time:
            raise ValueError(
                "raw JSONL simulated live mode requires monotonic raw timestamps; "
                "use calibrate_from_raw_jsonl_table.py with explicit windows or "
                "check --timestamp-scale."
            )

        if self.real_time and self._last_frame_time is not None:
            self._sleep_for_replay_delta(frame_time - self._last_frame_time)

        self._last_frame_time = frame_time
        self._last_wall_time = time.monotonic()
        frame = LiveRawFrame(
            frame_index=self.start_frame + self._returned,
            raw_frame=raw,
            receive_time_monotonic=frame_time,
            receive_wall_time=time.time(),
            byte_length=len(json.dumps(raw, ensure_ascii=False).encode("utf-8")),
        )
        self._returned += 1
        self._started = True
        return frame

    def iter_frames(self, timeout: float = 0.1):
        """Yield frames until EOF."""

        while True:
            frame = self.get_frame(timeout=timeout)
            if frame is None:
                break
            yield frame

    def queue_size(self) -> int:
        """Return zero; this source does not buffer ahead."""

        return 0

    @property
    def dropped_frame_count(self) -> int:
        """Return zero; this source never drops queued frames."""

        return 0

    @property
    def parse_error_count(self) -> int:
        """Transport JSON errors are raised by JsonlRawFrameSource."""

        return 0

    @property
    def bad_json_line_count(self) -> int:
        """Transport JSON errors are raised by JsonlRawFrameSource."""

        return 0

    def stats_snapshot(self) -> LiveRawStreamStats:
        """Return a live-source-like stats snapshot."""

        return LiveRawStreamStats(
            total_received_frames=self._returned,
            parse_error_count=0,
            bad_json_line_count=0,
            dropped_frame_count=0,
            queue_size=0,
            queue_drop_policy=QUEUE_DROP_POLICY,
            last_parse_error_message="",
            last_bad_json_preview="",
            client_connected=False,
            running=not self.stopped,
            stop_reason=self.stop_reason,
        )

    def close(self) -> None:
        """Close the underlying file source."""

        self.stop("closed")

    def _next_raw_after_start_frame(self) -> dict[str, Any] | None:
        while self._skipped < self.start_frame:
            skipped = self._source.next_frame()
            if skipped is None:
                return None
            self._skipped += 1
        return self._source.next_frame()

    def _strict_frame_time(self, raw: dict[str, Any]) -> float:
        try:
            timestamp = float(raw["timestamp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "raw JSONL simulated live mode requires a numeric raw timestamp; "
                "use calibrate_from_raw_jsonl_table.py with --sample-window-frames "
                "for recordings without reliable timestamps."
            ) from exc
        return timestamp * self.timestamp_scale

    def _sleep_for_replay_delta(self, frame_delta_seconds: float) -> None:
        if frame_delta_seconds <= 0.0:
            return
        target_delay = frame_delta_seconds / self.speed
        if self._last_wall_time is not None:
            target_delay -= time.monotonic() - self._last_wall_time
        if target_delay > 0.0:
            time.sleep(target_delay)

    def _finish(self, reason: str) -> None:
        self.stopped = True
        if self.stop_reason is None:
            self.stop_reason = reason
        self._closed = True
        self._source.close()
