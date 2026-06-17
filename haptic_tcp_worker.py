"""Bounded TCP worker for Stage 1 Matrix haptic output."""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol


class MutableHapticRecord(Protocol):
    queued_monotonic_ms: float | None
    sent_monotonic_ms: float | None
    success: bool | None
    send_status: str
    not_sent_reason: str | None
    error: str | None


@dataclass(frozen=True)
class MatrixSendTask:
    """One queued Matrix packet and its mutable log record."""

    record: MutableHapticRecord
    packet: bytes


class MatrixHapticConnectionError(RuntimeError):
    """Raised when the Matrix TCP worker cannot connect before trial start."""


class MatrixTcpWorker:
    """Non-blocking Matrix TCP sender with a bounded queue."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        connect_timeout_s: float,
        send_timeout_s: float,
        max_queue_size: int,
        latest_only: bool,
        socket_factory: Any = socket.create_connection,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.connect_timeout_s = float(connect_timeout_s)
        self.send_timeout_s = float(send_timeout_s)
        self.latest_only = bool(latest_only)
        self.socket_factory = socket_factory
        self._queue: queue.Queue[MatrixSendTask] = queue.Queue(maxsize=int(max_queue_size))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any | None = None
        self._lock = threading.Lock()
        self.connected = False
        self.connect_error: str | None = None

    def start(self) -> None:
        """Connect synchronously and start the sender thread."""

        try:
            sock = self.socket_factory(
                (self.host, self.port),
                timeout=self.connect_timeout_s,
            )
            if hasattr(sock, "settimeout"):
                sock.settimeout(self.send_timeout_s)
        except Exception as exc:  # pragma: no cover - exact socket exceptions vary
            self.connect_error = str(exc)
            raise MatrixHapticConnectionError(
                f"matrix haptic connect failed: {self.host}:{self.port}: {exc}"
            ) from exc

        with self._lock:
            self._socket = sock
            self.connected = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="MatrixHapticTcpWorker",
                daemon=True,
            )
            self._thread.start()

    def submit(self, record: MutableHapticRecord, packet: bytes) -> bool:
        """Queue a packet without blocking the trial loop."""

        record.queued_monotonic_ms = time.monotonic() * 1000.0
        task = MatrixSendTask(record=record, packet=packet)
        try:
            self._queue.put_nowait(task)
            record.send_status = "queued"
            return True
        except queue.Full:
            if not self.latest_only:
                _mark_not_sent(record, "queue_full", "queue_full")
                return False
            try:
                dropped = self._queue.get_nowait()
                _mark_not_sent(
                    dropped.record,
                    "replaced",
                    "queue_replaced_by_latest",
                )
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(task)
                record.send_status = "queued"
                return True
            except queue.Full:
                _mark_not_sent(record, "queue_full", "queue_full")
                return False

    def stop(self, timeout_s: float = 1.0) -> None:
        """Stop the worker and close the socket."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        sock = self._socket
        self._socket = None
        self.connected = False
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def _run(self) -> None:
        while True:
            if self._stop_event.is_set() and self._queue.empty():
                return
            try:
                task = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            self._send_task(task)
            self._queue.task_done()

    def _send_task(self, task: MatrixSendTask) -> None:
        sock = self._socket
        if sock is None:
            _mark_not_sent(task.record, "not_connected", "not_connected")
            return
        try:
            sock.sendall(task.packet)
        except Exception as exc:
            task.record.success = False
            task.record.send_status = "send_failed"
            task.record.not_sent_reason = "send_failed"
            task.record.error = str(exc)
            return
        task.record.sent_monotonic_ms = time.monotonic() * 1000.0
        task.record.success = True
        task.record.send_status = "sent"
        task.record.not_sent_reason = None
        task.record.error = None


def _mark_not_sent(
    record: MutableHapticRecord,
    status: str,
    reason: str,
) -> None:
    record.success = False
    record.send_status = status
    record.not_sent_reason = reason
