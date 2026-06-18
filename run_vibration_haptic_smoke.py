"""Standalone vibration haptic TCP smoke test.

This script intentionally does not depend on MANUS/Vive input, trial lifecycle,
track geometry, cue generation, or slip/contact logic. It only connects to a
vibration ESP32 endpoint, sends explicit newline-delimited integer commands,
writes a smoke log, and exits.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from vibration_haptic_protocol import (
    encode_vibration_line_command,
    vibration_payload_to_log_string,
)


DEFAULT_PORT = 12346
DEFAULT_CONNECT_TIMEOUT_S = 3.0
DEFAULT_SEND_TIMEOUT_S = 0.05
DEFAULT_STARTUP_SETTLE_SECONDS = 7.0


@dataclass
class VibrationHapticSmokeResult:
    host: str
    port: int
    commands: list[int]
    startup_settle_seconds: float
    connect_timeout_s: float
    send_timeout_s: float
    sent_payloads: list[str]
    payload_hex: list[str]
    log_path: str
    success: bool
    connect_started_monotonic_ms: float | None = None
    connected_monotonic_ms: float | None = None
    send_started_monotonic_ms: float | None = None
    sent_monotonic_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_vibration_haptic_smoke(
    *,
    host: str,
    port: int,
    commands: list[int],
    out_path: str | Path,
    startup_settle_seconds: float = DEFAULT_STARTUP_SETTLE_SECONDS,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    send_timeout_s: float = DEFAULT_SEND_TIMEOUT_S,
    socket_factory: Callable[..., Any] = socket.create_connection,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_ms_fn: Callable[[], float] | None = None,
) -> VibrationHapticSmokeResult:
    """Connect to vibration ESP32, send commands, and write a JSON log."""

    monotonic_ms = monotonic_ms_fn or (lambda: time.monotonic() * 1000.0)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    payloads = [encode_vibration_line_command(command) for command in commands]
    result = VibrationHapticSmokeResult(
        host=str(host),
        port=int(port),
        commands=list(commands),
        startup_settle_seconds=float(startup_settle_seconds),
        connect_timeout_s=float(connect_timeout_s),
        send_timeout_s=float(send_timeout_s),
        sent_payloads=[vibration_payload_to_log_string(payload) for payload in payloads],
        payload_hex=[payload.hex() for payload in payloads],
        log_path=str(output),
        success=False,
    )

    sock: Any | None = None
    try:
        result.connect_started_monotonic_ms = monotonic_ms()
        sock = socket_factory((result.host, result.port), timeout=result.connect_timeout_s)
        if hasattr(sock, "settimeout"):
            sock.settimeout(result.send_timeout_s)
        result.connected_monotonic_ms = monotonic_ms()
        if result.startup_settle_seconds > 0.0:
            sleep_fn(result.startup_settle_seconds)
        result.send_started_monotonic_ms = monotonic_ms()
        for payload in payloads:
            sock.sendall(payload)
        result.sent_monotonic_ms = monotonic_ms()
        result.success = True
    except Exception as exc:
        result.error = str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        _write_json(output, result.to_dict())

    return result


def parse_commands(value: str) -> list[int]:
    """Parse a comma-separated vibration command list."""

    if value is None or not str(value).strip():
        raise argparse.ArgumentTypeError("--commands must not be empty.")
    commands: list[int] = []
    for raw_item in str(value).split(","):
        item = raw_item.strip()
        if not item:
            raise argparse.ArgumentTypeError("--commands contains an empty item.")
        try:
            command = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"vibration command must be an integer: {item!r}"
            ) from exc
        if command < 1 or command > 255:
            raise argparse.ArgumentTypeError(
                f"vibration command must be in 1..255, got {command!r}."
            )
        commands.append(command)
    return commands


def default_smoke_log_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("data") / "haptic_smoke" / f"vibration_haptic_smoke_{timestamp}.json"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone vibration ESP32 haptic TCP smoke test."
    )
    parser.add_argument("--host", required=True, help="Vibration ESP32 TCP host/IP.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--commands",
        required=True,
        type=parse_commands,
        help="Comma-separated vibration commands, for example: 1 or 3,4.",
    )
    parser.add_argument(
        "--startup-settle-seconds",
        type=float,
        default=DEFAULT_STARTUP_SETTLE_SECONDS,
        help="Seconds to wait after TCP connection before sending.",
    )
    parser.add_argument(
        "--connect-timeout-s",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT_S,
    )
    parser.add_argument("--send-timeout-s", type=float, default=DEFAULT_SEND_TIMEOUT_S)
    parser.add_argument(
        "--out",
        default=None,
        help="Smoke JSON log path. Defaults to data/haptic_smoke/vibration_haptic_smoke_<timestamp>.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.startup_settle_seconds < 0.0:
        raise SystemExit("--startup-settle-seconds must be >= 0.")
    if args.connect_timeout_s <= 0.0:
        raise SystemExit("--connect-timeout-s must be > 0.")
    if args.send_timeout_s <= 0.0:
        raise SystemExit("--send-timeout-s must be > 0.")

    out_path = Path(args.out) if args.out is not None else default_smoke_log_path()
    result = run_vibration_haptic_smoke(
        host=args.host,
        port=args.port,
        commands=args.commands,
        out_path=out_path,
        startup_settle_seconds=args.startup_settle_seconds,
        connect_timeout_s=args.connect_timeout_s,
        send_timeout_s=args.send_timeout_s,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.success else 1


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
