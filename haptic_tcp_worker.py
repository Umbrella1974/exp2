"""Bounded TCP worker for Stage 1 Matrix haptic output."""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


class MutableHapticRecord(Protocol):
    queued_monotonic_ms: float | None
    sent_monotonic_ms: float | None
    success: bool | None
    send_status: str
    not_sent_reason: str | None
    error: str | None


@dataclass(frozen=True)
class MatrixSendStep:
    """One ordered packet within an atomic Matrix queue task."""

    record: MutableHapticRecord
    packet: bytes
    role: str = "main"


@dataclass(frozen=True)
class MatrixSendTask:
    """One atomic ordered Matrix packet sequence."""

    steps: tuple[MatrixSendStep, ...]
    on_reset_failure: Callable[[], None] | None = None


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

        return self.submit_sequence((MatrixSendStep(record=record, packet=packet),))

    def submit_sequence(
        self,
        steps: tuple[MatrixSendStep, ...],
        *,
        on_reset_failure: Callable[[], None] | None = None,
    ) -> bool:
        """Queue an ordered packet sequence as one latest-only unit."""

        if not steps:
            raise ValueError("Matrix send sequence must contain at least one step.")
        task = MatrixSendTask(
            steps=tuple(steps),
            on_reset_failure=on_reset_failure,
        )
        try:
            self._queue.put_nowait(task)
            _mark_task_queued(task)
            return True
        except queue.Full:
            if not self.latest_only:
                _mark_task_not_sent(task, "queue_full", "queue_full")
                return False
            try:
                dropped = self._queue.get_nowait()
                _mark_task_not_sent(
                    dropped,
                    "replaced",
                    "queue_replaced_by_latest",
                )
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(task)
                _mark_task_queued(task)
                return True
            except queue.Full:
                _mark_task_not_sent(task, "queue_full", "queue_full")
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
            if task.steps[0].role == "reset":
                _mark_not_sent(
                    task.steps[0].record,
                    "not_connected",
                    "reset_send_failed",
                )
                for remaining in task.steps[1:]:
                    _mark_not_sent(remaining.record, "skipped", "reset_failed")
                if task.on_reset_failure is not None:
                    try:
                        task.on_reset_failure()
                    except Exception:
                        pass
            else:
                _mark_task_not_sent(task, "not_connected", "not_connected")
            return
        for index, step in enumerate(task.steps):
            try:
                sock.sendall(step.packet)
            except Exception as exc:
                step.record.success = False
                step.record.send_status = "send_failed"
                step.record.not_sent_reason = (
                    "reset_send_failed" if step.role == "reset" else "send_failed"
                )
                step.record.error = str(exc)
                if step.role == "reset":
                    for remaining in task.steps[index + 1 :]:
                        _mark_not_sent(remaining.record, "skipped", "reset_failed")
                    if task.on_reset_failure is not None:
                        try:
                            task.on_reset_failure()
                        except Exception:
                            pass
                return
            step.record.sent_monotonic_ms = time.monotonic() * 1000.0
            step.record.success = True
            step.record.send_status = "sent"
            step.record.not_sent_reason = None
            step.record.error = None


def _mark_not_sent(
    record: MutableHapticRecord,
    status: str,
    reason: str,
) -> None:
    record.success = False
    record.send_status = status
    record.not_sent_reason = reason


def _mark_task_queued(task: MatrixSendTask) -> None:
    queued_ms = time.monotonic() * 1000.0
    for step in task.steps:
        step.record.queued_monotonic_ms = queued_ms
        step.record.send_status = "queued"
        step.record.not_sent_reason = None


def _mark_task_not_sent(task: MatrixSendTask, status: str, reason: str) -> None:
    for step in task.steps:
        _mark_not_sent(step.record, status, reason)
