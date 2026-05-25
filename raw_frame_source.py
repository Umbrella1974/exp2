"""Raw frame sources for Stage 3.1 smoke tests and replay."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


class RawFrameSource:
    """Iterator-style source that yields raw MANUS/Vive JSON dictionaries."""

    def __iter__(self) -> "RawFrameSource":
        return self

    def __next__(self) -> dict[str, Any]:
        frame = self.next_frame()
        if frame is None:
            raise StopIteration
        return frame

    def next_frame(self) -> dict[str, Any] | None:
        """Return the next raw frame, or None when no frame is currently available."""

        raise NotImplementedError


class JsonlRawFrameSource(RawFrameSource):
    """Read raw JSON dictionaries from a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle = self.path.open("r", encoding="utf-8-sig")
        self._line_number = 0

    def next_frame(self) -> dict[str, Any] | None:
        """Return the next JSON object; skip blank lines and return None at EOF."""

        while True:
            line = self._handle.readline()
            if line == "":
                return None
            self._line_number += 1
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in {self.path} at line {self._line_number}."
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"Expected JSON object in {self.path} at line {self._line_number}."
                )
            return payload

    def close(self) -> None:
        """Close the underlying file handle."""

        self._handle.close()


class IterableRawFrameSource(RawFrameSource):
    """Yield raw dictionaries from an iterable without modifying them."""

    def __init__(self, frames: Iterable[dict[str, Any]]) -> None:
        self._iterator: Iterator[dict[str, Any]] = iter(frames)

    def next_frame(self) -> dict[str, Any] | None:
        """Return the next raw dict, or None when the iterable is exhausted."""

        try:
            frame = next(self._iterator)
        except StopIteration:
            return None
        if not isinstance(frame, dict):
            raise TypeError("IterableRawFrameSource expected each frame to be a dict.")
        return dict(frame)


class ManusSocketRawFrameSource(RawFrameSource):
    """Thin receiver shell for future live sources.

    The receiver is dependency-injected and only needs get_latest_raw_frame().
    None means no new frame is currently available, not EOF.
    """

    def __init__(self, receiver: object) -> None:
        self.receiver = receiver

    def next_frame(self) -> dict[str, Any] | None:
        """Return the latest raw frame from the receiver, if one is available."""

        get_latest = getattr(self.receiver, "get_latest_raw_frame", None)
        if get_latest is None or not callable(get_latest):
            raise TypeError("receiver must provide callable get_latest_raw_frame().")

        frame = get_latest()
        if frame is None:
            return None
        if not isinstance(frame, dict):
            raise TypeError("receiver.get_latest_raw_frame() must return a dict or None.")
        return frame
