"""Tests for the Stage 5B-0 live raw TCP stream source."""

from __future__ import annotations

import socket
import time

from live_raw_stream import LiveRawStreamServer


def test_live_raw_stream_receives_newline_json_and_fragmented_packets() -> None:
    server = LiveRawStreamServer(port=0)
    server.start()
    try:
        with socket.create_connection((server.host, server.port), timeout=2.0) as client:
            client.sendall(b'{"frame":1}\n{"frame":')
            client.sendall(b'2}\n')
            first = server.get_frame(timeout=2.0)
            second = server.get_frame(timeout=2.0)

        assert first is not None
        assert second is not None
        assert first.raw_frame == {"frame": 1}
        assert second.raw_frame == {"frame": 2}
        assert first.frame_index == 0
        assert second.frame_index == 1
        assert first.byte_length > 0
    finally:
        server.stop()
        server.join(timeout=1.0)


def test_live_raw_stream_bad_json_does_not_stop_server() -> None:
    server = LiveRawStreamServer(port=0)
    server.start()
    try:
        with socket.create_connection((server.host, server.port), timeout=2.0) as client:
            client.sendall(b"{bad json}\n")
            client.sendall(b'{"frame":2}\n')
            frame = server.get_frame(timeout=2.0)

        assert frame is not None
        assert frame.raw_frame == {"frame": 2}
        stats = server.stats_snapshot()
        assert stats.parse_error_count == 1
        assert stats.bad_json_line_count == 1
        assert "bad json" in stats.last_bad_json_preview
    finally:
        server.stop()
        server.join(timeout=1.0)


def test_live_raw_stream_queue_full_drops_oldest_frame() -> None:
    server = LiveRawStreamServer(port=0, max_queue_size=2)
    server.start()
    try:
        with socket.create_connection((server.host, server.port), timeout=2.0) as client:
            for index in range(4):
                client.sendall(f'{{"frame":{index}}}\n'.encode("utf-8"))
            _wait_until(lambda: server.stats_snapshot().total_received_frames == 4)

        first = server.get_frame(timeout=1.0)
        second = server.get_frame(timeout=1.0)
        assert first is not None
        assert second is not None
        assert first.raw_frame == {"frame": 2}
        assert second.raw_frame == {"frame": 3}
        assert server.stats_snapshot().dropped_frame_count == 2
        assert server.stats_snapshot().queue_drop_policy == "drop_oldest_when_full"
    finally:
        server.stop()
        server.join(timeout=1.0)


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")
