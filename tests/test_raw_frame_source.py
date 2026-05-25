"""Tests for Stage 3.1 raw frame sources."""

from __future__ import annotations

import pytest

from raw_frame_source import (
    IterableRawFrameSource,
    JsonlRawFrameSource,
    ManusSocketRawFrameSource,
)


def test_jsonl_raw_frame_source_reads_multiple_lines_and_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "frames.jsonl"
    path.write_text('{"frame": 1}\n\n{"frame": 2}\n', encoding="utf-8")

    source = JsonlRawFrameSource(path)
    try:
        assert next(source) == {"frame": 1}
        assert source.next_frame() == {"frame": 2}
        assert source.next_frame() is None
        with pytest.raises(StopIteration):
            next(source)
    finally:
        source.close()


def test_jsonl_raw_frame_source_invalid_json_reports_line_number(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"frame": 1}\n{bad json}\n', encoding="utf-8")

    source = JsonlRawFrameSource(path)
    try:
        assert source.next_frame() == {"frame": 1}
        with pytest.raises(ValueError, match="line 2"):
            source.next_frame()
    finally:
        source.close()


def test_jsonl_raw_frame_source_non_object_reports_line_number(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('[1, 2, 3]\n', encoding="utf-8")

    source = JsonlRawFrameSource(path)
    try:
        with pytest.raises(ValueError, match="line 1"):
            source.next_frame()
    finally:
        source.close()


def test_jsonl_raw_frame_source_accepts_utf8_bom(tmp_path) -> None:
    path = tmp_path / "bom.jsonl"
    path.write_text('\ufeff{"frame": 1}\n', encoding="utf-8")

    source = JsonlRawFrameSource(path)
    try:
        assert source.next_frame() == {"frame": 1}
    finally:
        source.close()


def test_iterable_raw_frame_source_yields_copies_in_order() -> None:
    original = [{"frame": 1}, {"frame": 2}]
    source = IterableRawFrameSource(original)
    first = source.next_frame()
    assert first == {"frame": 1}
    assert first is not original[0]
    assert next(source) == {"frame": 2}
    assert source.next_frame() is None


def test_iterable_raw_frame_source_rejects_non_dict_frames() -> None:
    source = IterableRawFrameSource([{"frame": 1}, ["not", "dict"]])  # type: ignore[list-item]
    assert source.next_frame() == {"frame": 1}
    with pytest.raises(TypeError, match="dict"):
        source.next_frame()


def test_manus_socket_raw_frame_source_receiver_shell() -> None:
    class Receiver:
        def __init__(self) -> None:
            self.frames = [{"frame": 1}, None]

        def get_latest_raw_frame(self):
            return self.frames.pop(0)

    source = ManusSocketRawFrameSource(Receiver())
    assert source.next_frame() == {"frame": 1}
    assert source.next_frame() is None


def test_manus_socket_raw_frame_source_rejects_bad_receiver_output() -> None:
    class Receiver:
        def get_latest_raw_frame(self):
            return "bad"

    source = ManusSocketRawFrameSource(Receiver())
    with pytest.raises(TypeError, match="dict or None"):
        source.next_frame()


def test_manus_socket_raw_frame_source_requires_receiver_method() -> None:
    source = ManusSocketRawFrameSource(object())
    with pytest.raises(TypeError, match="get_latest_raw_frame"):
        source.next_frame()
