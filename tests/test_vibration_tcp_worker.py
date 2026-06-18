"""Tests for the vibration haptic TCP worker."""

from __future__ import annotations

import time

from vibration_tcp_worker import VibrationTcpLineWorker


def test_vibration_tcp_worker_sends_fifo_packets_on_background_thread() -> None:
    sock = _FakeSocket()
    worker = VibrationTcpLineWorker(
        host="127.0.0.1",
        port=12346,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=2,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    first = _Record()
    second = _Record()

    worker.start()
    assert worker.submit(first, b"1\n") is True
    assert worker.submit(second, b"2\n") is True
    _wait_for(lambda: sock.sent == [b"1\n", b"2\n"])
    worker.stop()

    assert first.send_status == "sent"
    assert second.send_status == "sent"
    assert first.success is True
    assert second.success is True


def test_vibration_tcp_worker_queue_full_does_not_replace_regular_commands() -> None:
    sock = _BlockingSocket()
    worker = VibrationTcpLineWorker(
        host="127.0.0.1",
        port=12346,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=1,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    first = _Record()
    second = _Record()
    third = _Record()

    worker.start()
    assert worker.submit(first, b"1\n") is True
    _wait_for(lambda: sock.started)
    assert worker.submit(second, b"2\n") is True
    assert worker.submit(third, b"3\n") is False
    sock.release()
    _wait_for(lambda: sock.sent == [b"1\n", b"2\n"])
    worker.stop()

    assert third.send_status == "not_sent"
    assert third.not_sent_reason == "queue_full"


def test_vibration_tcp_worker_priority_stop_clears_pending_queue() -> None:
    sock = _BlockingSocket()
    worker = VibrationTcpLineWorker(
        host="127.0.0.1",
        port=12346,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=1,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    first = _Record()
    pending = _Record()
    stop = _Record()

    worker.start()
    assert worker.submit(first, b"3\n") is True
    _wait_for(lambda: sock.started)
    assert worker.submit(pending, b"1\n") is True
    assert worker.submit(stop, b"4\n", priority_stop=True) is True
    sock.release()
    _wait_for(lambda: b"4\n" in sock.sent)
    worker.stop()

    assert pending.send_status == "not_sent"
    assert pending.not_sent_reason == "queue_cleared_for_stop_slip"
    assert stop.send_status == "sent"
    assert sock.sent == [b"3\n", b"4\n"]


def test_vibration_tcp_worker_priority_stop_failure_closes_socket() -> None:
    sock = _FailingSocket()
    worker = VibrationTcpLineWorker(
        host="127.0.0.1",
        port=12346,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=1,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    record = _Record()

    worker.start()
    assert worker.submit(record, b"4\n", priority_stop=True) is True
    _wait_for(lambda: record.send_status == "send_failed")

    assert record.not_sent_reason == "stop_slip_send_failed"
    assert sock.closed is True
    assert worker.connected is False


class _Record:
    queued_monotonic_ms = None
    sent_monotonic_ms = None
    success = None
    send_status = ""
    not_sent_reason = None
    error = None


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


class _BlockingSocket(_FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self._blocked = True
        self.started = False

    def sendall(self, payload: bytes) -> None:
        self.started = True
        while self._blocked:
            time.sleep(0.001)
        super().sendall(payload)

    def release(self) -> None:
        self._blocked = False


class _FailingSocket(_FakeSocket):
    def sendall(self, _payload: bytes) -> None:
        raise OSError("send failed")


def _wait_for(predicate: object, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met before timeout")
