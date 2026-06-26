from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matrix_haptic_protocol import encode_matrix_channel_packet
from run_matrix_haptic_smoke import (
    build_contact_to_pinch_sequence,
    parse_channels,
    run_matrix_haptic_smoke,
)


def test_parse_channels_accepts_comma_list() -> None:
    assert parse_channels("1, 2,3") == [1, 2, 3]


def test_parse_channels_rejects_invalid_value() -> None:
    with pytest.raises(Exception, match="0..127"):
        parse_channels("1,128")

    with pytest.raises(Exception, match="integer"):
        parse_channels("1,bad")


def test_matrix_haptic_smoke_connects_waits_sends_and_logs(tmp_path: Path) -> None:
    socket_factory = _FakeSocketFactory()
    sleeps: list[float] = []
    clock = _Clock()
    out = tmp_path / "smoke.json"

    result = run_matrix_haptic_smoke(
        host="192.168.1.20",
        port=12345,
        channels=[1, 2, 3],
        out_path=out,
        startup_settle_seconds=0.25,
        connect_timeout_s=1.0,
        send_timeout_s=0.5,
        socket_factory=socket_factory,
        sleep_fn=sleeps.append,
        monotonic_ms_fn=clock.now,
    )

    packet = encode_matrix_channel_packet([1, 2, 3])
    assert result.success is True
    assert socket_factory.calls == [(("192.168.1.20", 12345), 1.0)]
    assert socket_factory.socket.timeout == 0.5
    assert socket_factory.socket.sent_packets == [packet]
    assert socket_factory.socket.closed is True
    assert sleeps == [0.25]

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["channels"] == [1, 2, 3]
    assert payload["packet_hex"] == packet.hex()
    assert payload["steps"][0]["role"] == "main"
    assert payload["steps"][0]["key"] == "manual"
    assert payload["steps"][0]["channels"] == [1, 2, 3]
    assert payload["error"] is None


def test_matrix_haptic_sequence_smoke_sends_ordered_main_reset_main(tmp_path: Path) -> None:
    socket_factory = _FakeSocketFactory()
    out = tmp_path / "sequence_smoke.json"
    steps = build_contact_to_pinch_sequence(
        contact_valid_channels=[1],
        contact_valid_reset_channels=[10],
        pinch_insufficient_channels=[2],
    )

    result = run_matrix_haptic_smoke(
        host="192.168.1.20",
        port=12345,
        steps=steps,
        out_path=out,
        startup_settle_seconds=0.0,
        socket_factory=socket_factory,
        monotonic_ms_fn=_Clock().now,
    )

    assert result.success is True
    assert socket_factory.socket.sent_packets == [
        encode_matrix_channel_packet([1]),
        encode_matrix_channel_packet([10]),
        encode_matrix_channel_packet([2]),
    ]

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["channels"] == []
    assert payload["packet_hex"] is None
    assert [(step["role"], step["key"], step["channels"]) for step in payload["steps"]] == [
        ("main", "contact_valid", [1]),
        ("reset", "contact_valid", [10]),
        ("main", "pinch_insufficient", [2]),
    ]
    assert all(step["success"] is True for step in payload["steps"])


def test_matrix_haptic_smoke_logs_connection_failure(tmp_path: Path) -> None:
    def fail_connect(*_: Any, **__: Any) -> Any:
        raise OSError("connection refused")

    out = tmp_path / "smoke_fail.json"
    result = run_matrix_haptic_smoke(
        host="192.168.1.20",
        port=12345,
        channels=[1],
        out_path=out,
        startup_settle_seconds=0.0,
        socket_factory=fail_connect,
    )

    assert result.success is False
    assert "connection refused" in str(result.error)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert "connection refused" in payload["error"]


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        self.value += 1.0
        return self.value


class _FakeSocket:
    def __init__(self) -> None:
        self.timeout: float | None = None
        self.sent_packets: list[bytes] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, packet: bytes) -> None:
        self.sent_packets.append(packet)

    def close(self) -> None:
        self.closed = True


class _FakeSocketFactory:
    def __init__(self) -> None:
        self.socket = _FakeSocket()
        self.calls: list[tuple[tuple[str, int], float]] = []

    def __call__(self, address: tuple[str, int], *, timeout: float) -> _FakeSocket:
        self.calls.append((address, timeout))
        return self.socket
