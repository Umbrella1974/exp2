"""Bounded TCP worker for vibration ESP32 line-integer output."""

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
class VibrationSendTask:
    """One queued vibration payload and its mutable log record."""

    record: MutableHapticRecord
    payload: bytes
    close_socket_on_failure: bool = False
    failure_reason: str = "send_failed"


class VibrationHapticConnectionError(RuntimeError):
    """Raised when the vibration TCP worker cannot connect before trial start."""


class VibrationTcpLineWorker:
    """Non-blocking FIFO sender for newline-delimited vibration commands."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        connect_timeout_s: float,
        send_timeout_s: float,
        max_queue_size: int,
        socket_factory: Any = socket.create_connection,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.connect_timeout_s = float(connect_timeout_s)
        self.send_timeout_s = float(send_timeout_s)
        self.socket_factory = socket_factory
        self._queue: queue.Queue[VibrationSendTask] = queue.Queue(
            maxsize=int(max_queue_size)
        )
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
            raise VibrationHapticConnectionError(
                f"vibration haptic connect failed: {self.host}:{self.port}: {exc}"
            ) from exc

        with self._lock:
            self._socket = sock
            self.connected = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="VibrationHapticTcpWorker",
                daemon=True,
            )
            self._thread.start()

    def submit(
        self,
        record: MutableHapticRecord,
        payload: bytes,
        *,
        priority_stop: bool = False,
    ) -> bool:
        """Queue a payload without blocking the trial loop."""

        if self._socket is None or not self.connected:
            _mark_not_sent(record, "not_connected", "not_connected")
            return False
        record.queued_monotonic_ms = time.monotonic() * 1000.0
        task = VibrationSendTask(
            record=record,
            payload=bytes(payload),
            close_socket_on_failure=bool(priority_stop),
            failure_reason="stop_slip_send_failed" if priority_stop else "send_failed",
        )
        if priority_stop:
            self._clear_queue_for_stop_slip()
        try:
            self._queue.put_nowait(task)
        except queue.Full:
            reason = "stop_slip_send_failed" if priority_stop else "queue_full"
            _mark_not_sent(record, "not_sent", reason)
            return False
        record.send_status = "queued"
        record.not_sent_reason = None
        return True

    def stop(self, timeout_s: float = 1.0) -> None:
        """Drain queued commands, stop the worker, and close the socket."""

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        self._close_socket()

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

    def _send_task(self, task: VibrationSendTask) -> None:
        sock = self._socket
        if sock is None:
            _mark_not_sent(task.record, "not_connected", "not_connected")
            return
        try:
            sock.sendall(task.payload)
        except Exception as exc:
            task.record.success = False
            task.record.send_status = "send_failed"
            task.record.not_sent_reason = task.failure_reason
            task.record.error = str(exc)
            if task.close_socket_on_failure:
                self._close_socket()
            return
        task.record.sent_monotonic_ms = time.monotonic() * 1000.0
        task.record.success = True
        task.record.send_status = "sent"
        task.record.not_sent_reason = None
        task.record.error = None

    def _clear_queue_for_stop_slip(self) -> None:
        while True:
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                return
            _mark_not_sent(dropped.record, "not_sent", "queue_cleared_for_stop_slip")
            self._queue.task_done()

    def _close_socket(self) -> None:
        sock = self._socket
        self._socket = None
        self.connected = False
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def _mark_not_sent(
    record: MutableHapticRecord,
    status: str,
    reason: str,
) -> None:
    record.success = False
    record.send_status = status
    record.not_sent_reason = reason
