"""Live newline-delimited JSON TCP source for raw MANUS/Vive frames.

This module only implements the transport bridge used by Stage 5B-0 smoke
tests. It does not parse MANUS data into DeviceFrame, start TrialController, or
touch haptic/controller logic.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


QUEUE_DROP_POLICY = "drop_oldest_when_full"


@dataclass(frozen=True)
class LiveRawFrame:
    """One valid raw JSON frame received from the live TCP stream."""

    frame_index: int
    raw_frame: dict[str, Any]
    receive_time_monotonic: float
    receive_wall_time: float
    byte_length: int


@dataclass(frozen=True)
class LiveRawStreamStats:
    """Snapshot of live raw stream transport statistics."""

    total_received_frames: int
    parse_error_count: int
    bad_json_line_count: int
    dropped_frame_count: int
    queue_size: int
    queue_drop_policy: str
    last_parse_error_message: str
    last_bad_json_preview: str
    client_connected: bool
    running: bool
    stop_reason: str | None


class LiveRawStreamServer:
    """TCP server that accepts one newline-delimited JSON stream."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8888,
        *,
        max_queue_size: int = 300,
        socket_timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be > 0.")
        self.host = host
        self.port = int(port)
        self.max_queue_size = int(max_queue_size)
        self.socket_timeout = socket_timeout
        self.stop_event = stop_event or threading.Event()
        self._owns_stop_event = stop_event is None

        self._frames: deque[LiveRawFrame] = deque()
        self._condition = threading.Condition()
        self._stats_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._server_socket: socket.socket | None = None
        self._client_socket: socket.socket | None = None
        self._running = False
        self._client_connected = False
        self._stop_reason: str | None = None
        self._next_frame_index = 0
        self._total_received_frames = 0
        self._parse_error_count = 0
        self._bad_json_line_count = 0
        self._dropped_frame_count = 0
        self._last_parse_error_message = ""
        self._last_bad_json_preview = ""

    def start(self) -> None:
        """Bind the socket and start the background receiver thread."""

        if self._thread is not None and self._thread.is_alive():
            return
        if self._owns_stop_event:
            self.stop_event.clear()
        self._set_stop_reason(None)
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.bind((self.host, self.port))
        self.port = int(self._server_socket.getsockname()[1])
        self._server_socket.listen(1)
        self._server_socket.settimeout(self.socket_timeout or 0.1)
        self._running = True
        self._thread = threading.Thread(target=self._run, name="LiveRawStreamServer", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "server_stopped") -> None:
        """Stop the server and wake any waiting consumers."""

        self._set_stop_reason(reason)
        self.stop_event.set()
        self._running = False
        self._close_socket(self._client_socket)
        self._close_socket(self._server_socket)
        self._client_socket = None
        self._server_socket = None
        with self._condition:
            self._condition.notify_all()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the background receiver thread to finish."""

        if self._thread is not None:
            self._thread.join(timeout)

    def get_frame(self, timeout: float | None = None) -> LiveRawFrame | None:
        """Return the next queued frame, or None on timeout/closed empty queue."""

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._frames:
                if self.stop_event.is_set() or not self._running:
                    return None
                if timeout is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic() if deadline is not None else 0.0
                if remaining <= 0.0:
                    return None
                self._condition.wait(remaining)
            return self._frames.popleft()

    def iter_frames(self, timeout: float = 0.1):
        """Yield frames until the server stops and the queue is empty."""

        while True:
            frame = self.get_frame(timeout=timeout)
            if frame is not None:
                yield frame
                continue
            if self.stop_event.is_set() or not self._running:
                break

    @property
    def dropped_frame_count(self) -> int:
        """Return the number of valid frames dropped due to a full queue."""

        with self._stats_lock:
            return self._dropped_frame_count

    @property
    def parse_error_count(self) -> int:
        """Return the number of bad JSON lines encountered."""

        with self._stats_lock:
            return self._parse_error_count

    @property
    def bad_json_line_count(self) -> int:
        """Return the number of bad JSON lines encountered."""

        with self._stats_lock:
            return self._bad_json_line_count

    @property
    def stop_reason(self) -> str | None:
        """Return the transport-level stop reason, if known."""

        with self._stats_lock:
            return self._stop_reason

    def queue_size(self) -> int:
        """Return current queued frame count."""

        with self._condition:
            return len(self._frames)

    def stats_snapshot(self) -> LiveRawStreamStats:
        """Return a thread-safe statistics snapshot."""

        with self._condition:
            queue_size = len(self._frames)
        with self._stats_lock:
            return LiveRawStreamStats(
                total_received_frames=self._total_received_frames,
                parse_error_count=self._parse_error_count,
                bad_json_line_count=self._bad_json_line_count,
                dropped_frame_count=self._dropped_frame_count,
                queue_size=queue_size,
                queue_drop_policy=QUEUE_DROP_POLICY,
                last_parse_error_message=self._last_parse_error_message,
                last_bad_json_preview=self._last_bad_json_preview,
                client_connected=self._client_connected,
                running=self._running,
                stop_reason=self._stop_reason,
            )

    def _run(self) -> None:
        try:
            client_socket = self._accept_client()
            if client_socket is None:
                return
            self._client_socket = client_socket
            with self._stats_lock:
                self._client_connected = True
            self._receive_loop(client_socket)
        finally:
            with self._stats_lock:
                self._client_connected = False
            self._running = False
            self._close_socket(self._client_socket)
            self._close_socket(self._server_socket)
            self._client_socket = None
            self._server_socket = None
            with self._condition:
                self._condition.notify_all()

    def _accept_client(self) -> socket.socket | None:
        assert self._server_socket is not None
        while not self.stop_event.is_set():
            try:
                client_socket, _ = self._server_socket.accept()
                client_socket.settimeout(self.socket_timeout or 0.1)
                return client_socket
            except socket.timeout:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    self._set_stop_reason("socket_error")
                return None
        return None

    def _receive_loop(self, client_socket: socket.socket) -> None:
        buffer = b""
        while not self.stop_event.is_set():
            try:
                data = client_socket.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                if not self.stop_event.is_set():
                    self._set_stop_reason("socket_error")
                return
            if not data:
                self._set_stop_reason("client_disconnected")
                return
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                stripped = line.strip()
                if not stripped:
                    continue
                self._handle_line(stripped)

    def _handle_line(self, line: bytes) -> None:
        try:
            decoded = line.decode("utf-8")
            payload = json.loads(decoded)
            if not isinstance(payload, dict):
                raise ValueError("decoded JSON value is not an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._record_bad_json(line, exc)
            return

        receive_time_monotonic = time.monotonic()
        receive_wall_time = time.time()
        with self._stats_lock:
            frame_index = self._next_frame_index
            self._next_frame_index += 1
            self._total_received_frames += 1
        frame = LiveRawFrame(
            frame_index=frame_index,
            raw_frame=payload,
            receive_time_monotonic=receive_time_monotonic,
            receive_wall_time=receive_wall_time,
            byte_length=len(line),
        )
        self._enqueue_frame(frame)

    def _enqueue_frame(self, frame: LiveRawFrame) -> None:
        with self._condition:
            if len(self._frames) >= self.max_queue_size:
                self._frames.popleft()
                with self._stats_lock:
                    self._dropped_frame_count += 1
            self._frames.append(frame)
            self._condition.notify()

    def _record_bad_json(self, line: bytes, exc: Exception) -> None:
        preview = line[:200].decode("utf-8", errors="replace")
        with self._stats_lock:
            self._parse_error_count += 1
            self._bad_json_line_count += 1
            self._last_parse_error_message = str(exc)
            self._last_bad_json_preview = preview

    def _set_stop_reason(self, reason: str | None) -> None:
        with self._stats_lock:
            if reason is None or self._stop_reason is None:
                self._stop_reason = reason

    @staticmethod
    def _close_socket(sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass
