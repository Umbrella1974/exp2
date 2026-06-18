from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from run_vibration_haptic_smoke import parse_commands, run_vibration_haptic_smoke


def test_parse_commands_accepts_comma_list() -> None:
    assert parse_commands("1, 3,4") == [1, 3, 4]


def test_parse_commands_rejects_invalid_value() -> None:
    with pytest.raises(Exception, match="1..255"):
        parse_commands("1,0")

    with pytest.raises(Exception, match="integer"):
        parse_commands("1,bad")


def test_vibration_haptic_smoke_connects_waits_sends_and_logs(tmp_path: Path) -> None:
    socket_factory = _FakeSocketFactory()
    sleeps: list[float] = []
    clock = _Clock()
    out = tmp_path / "smoke.json"

    result = run_vibration_haptic_smoke(
        host="192.168.1.30",
        port=12346,
        commands=[1, 3, 4],
        out_path=out,
        startup_settle_seconds=0.25,
        connect_timeout_s=1.0,
        send_timeout_s=0.5,
        socket_factory=socket_factory,
        sleep_fn=sleeps.append,
        monotonic_ms_fn=clock.now,
    )

    assert result.success is True
    assert socket_factory.calls == [(("192.168.1.30", 12346), 1.0)]
    assert socket_factory.socket.timeout == 0.5
    assert socket_factory.socket.sent_packets == [b"1\n", b"3\n", b"4\n"]
    assert socket_factory.socket.closed is True
    assert sleeps == [0.25]

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["success"] is True
    assert payload["commands"] == [1, 3, 4]
    assert payload["sent_payloads"] == ["1\\n", "3\\n", "4\\n"]
    assert payload["payload_hex"] == ["310a", "330a", "340a"]
    assert payload["error"] is None


def test_vibration_haptic_smoke_logs_connection_failure(tmp_path: Path) -> None:
    def fail_connect(*_: Any, **__: Any) -> Any:
        raise OSError("connection refused")

    out = tmp_path / "smoke_fail.json"
    result = run_vibration_haptic_smoke(
        host="192.168.1.30",
        port=12346,
        commands=[1],
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
