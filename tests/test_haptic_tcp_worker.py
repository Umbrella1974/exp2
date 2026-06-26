"""Tests for the Matrix haptic TCP worker."""

from __future__ import annotations

import time

from haptic_tcp_worker import MatrixSendStep, MatrixTcpWorker


def test_matrix_tcp_worker_sends_packets_on_background_thread() -> None:
    sock = _FakeSocket()
    worker = MatrixTcpWorker(
        host="127.0.0.1",
        port=12345,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=2,
        latest_only=True,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    record = _Record()

    worker.start()
    assert worker.submit(record, b"packet") is True
    _wait_for(lambda: sock.sent == [b"packet"])
    worker.stop()

    assert record.send_status == "sent"
    assert record.success is True


def test_matrix_tcp_worker_latest_only_replaces_oldest_when_queue_full() -> None:
    sock = _BlockingSocket()
    worker = MatrixTcpWorker(
        host="127.0.0.1",
        port=12345,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=1,
        latest_only=True,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    first = _Record()
    second = _Record()
    third = _Record()

    worker.start()
    assert worker.submit(first, b"first") is True
    assert worker.submit(second, b"second") is True
    assert worker.submit(third, b"third") is True
    sock.release()
    _wait_for(lambda: b"third" in sock.sent)
    worker.stop()

    assert second.send_status == "replaced"
    assert second.not_sent_reason == "queue_replaced_by_latest"


def test_matrix_tcp_worker_sends_reset_and_main_as_one_ordered_sequence() -> None:
    sock = _FakeSocket()
    worker = MatrixTcpWorker(
        host="127.0.0.1",
        port=12345,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=2,
        latest_only=True,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    reset = _Record()
    main = _Record()

    worker.start()
    assert worker.submit_sequence(
        (
            MatrixSendStep(reset, b"reset", role="reset"),
            MatrixSendStep(main, b"main", role="main"),
        )
    ) is True
    _wait_for(lambda: sock.sent == [b"reset", b"main"])
    worker.stop()

    assert reset.send_status == "sent"
    assert main.send_status == "sent"


def test_matrix_tcp_worker_latest_only_replaces_whole_sequence() -> None:
    sock = _BlockingSocket()
    worker = MatrixTcpWorker(
        host="127.0.0.1",
        port=12345,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=1,
        latest_only=True,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    first = _Record()
    old_reset = _Record()
    old_main = _Record()
    new_reset = _Record()
    new_main = _Record()

    worker.start()
    assert worker.submit(first, b"first") is True
    assert worker.submit_sequence(
        (
            MatrixSendStep(old_reset, b"old-reset", role="reset"),
            MatrixSendStep(old_main, b"old-main", role="main"),
        )
    ) is True
    assert worker.submit_sequence(
        (
            MatrixSendStep(new_reset, b"new-reset", role="reset"),
            MatrixSendStep(new_main, b"new-main", role="main"),
        )
    ) is True
    sock.release()
    _wait_for(lambda: b"new-main" in sock.sent)
    worker.stop()

    assert old_reset.send_status == "replaced"
    assert old_main.send_status == "replaced"
    assert b"old-reset" not in sock.sent
    assert b"old-main" not in sock.sent


def test_matrix_tcp_worker_reset_failure_aborts_main() -> None:
    sock = _FailFirstSendSocket()
    worker = MatrixTcpWorker(
        host="127.0.0.1",
        port=12345,
        connect_timeout_s=0.1,
        send_timeout_s=0.1,
        max_queue_size=2,
        latest_only=True,
        socket_factory=lambda *_args, **_kwargs: sock,
    )
    reset = _Record()
    main = _Record()
    reset_failed = []

    worker.start()
    assert worker.submit_sequence(
        (
            MatrixSendStep(reset, b"reset", role="reset"),
            MatrixSendStep(main, b"main", role="main"),
        ),
        on_reset_failure=lambda: reset_failed.append(True),
    ) is True
    _wait_for(lambda: reset.send_status == "send_failed")
    worker.stop()

    assert sock.attempted == [b"reset"]
    assert reset.not_sent_reason == "reset_send_failed"
    assert main.send_status == "skipped"
    assert main.not_sent_reason == "reset_failed"
    assert reset_failed == [True]


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

    def sendall(self, packet: bytes) -> None:
        self.sent.append(packet)

    def close(self) -> None:
        self.closed = True


class _BlockingSocket(_FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self._blocked = True

    def sendall(self, packet: bytes) -> None:
        while self._blocked:
            time.sleep(0.001)
        super().sendall(packet)

    def release(self) -> None:
        self._blocked = False


class _FailFirstSendSocket(_FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.attempted: list[bytes] = []

    def sendall(self, packet: bytes) -> None:
        self.attempted.append(packet)
        raise OSError("fake reset send failure")


def _wait_for(predicate: object, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met before timeout")
