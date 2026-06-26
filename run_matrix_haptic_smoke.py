"""Standalone Matrix haptic TCP smoke test.

This script intentionally does not depend on MANUS/Vive input, trial lifecycle,
track geometry, cue generation, or blocked-state logic. It only connects to a
Matrix ESP32 endpoint, sends explicit channel lists, writes a smoke log, and
exits.
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

from matrix_haptic_protocol import encode_matrix_channel_packet


DEFAULT_PORT = 12345
DEFAULT_CONNECT_TIMEOUT_S = 3.0
DEFAULT_SEND_TIMEOUT_S = 0.05
DEFAULT_STARTUP_SETTLE_SECONDS = 7.0
MATRIX_SEQUENCE_CONTACT_TO_PINCH = "contact_valid_to_pinch_insufficient"


@dataclass
class MatrixHapticSmokeStep:
    role: str
    key: str
    channels: list[int]
    packet_hex: str | None = None
    send_started_monotonic_ms: float | None = None
    sent_monotonic_ms: float | None = None
    success: bool | None = None
    error: str | None = None


@dataclass
class MatrixHapticSmokeResult:
    host: str
    port: int
    channels: list[int]
    steps: list[MatrixHapticSmokeStep]
    startup_settle_seconds: float
    connect_timeout_s: float
    send_timeout_s: float
    packet_hex: str | None
    log_path: str
    success: bool
    connect_started_monotonic_ms: float | None = None
    connected_monotonic_ms: float | None = None
    send_started_monotonic_ms: float | None = None
    sent_monotonic_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_matrix_haptic_smoke(
    *,
    host: str,
    port: int,
    channels: list[int] | None = None,
    steps: list[MatrixHapticSmokeStep] | None = None,
    out_path: str | Path,
    startup_settle_seconds: float = DEFAULT_STARTUP_SETTLE_SECONDS,
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
    send_timeout_s: float = DEFAULT_SEND_TIMEOUT_S,
    socket_factory: Callable[..., Any] = socket.create_connection,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_ms_fn: Callable[[], float] | None = None,
) -> MatrixHapticSmokeResult:
    """Connect to Matrix ESP32, send one or more channel packets, and log them."""

    monotonic_ms = monotonic_ms_fn or (lambda: time.monotonic() * 1000.0)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if channels is None and steps is None:
        raise ValueError("Either channels or steps must be provided.")
    if channels is not None and steps is not None:
        raise ValueError("Provide channels or steps, not both.")
    smoke_steps = (
        list(steps)
        if steps is not None
        else [
            MatrixHapticSmokeStep(
                role="main",
                key="manual",
                channels=list(channels or []),
            )
        ]
    )

    result = MatrixHapticSmokeResult(
        host=str(host),
        port=int(port),
        channels=list(channels or []),
        steps=smoke_steps,
        startup_settle_seconds=float(startup_settle_seconds),
        connect_timeout_s=float(connect_timeout_s),
        send_timeout_s=float(send_timeout_s),
        packet_hex=None,
        log_path=str(output),
        success=False,
    )

    sock: Any | None = None
    try:
        encoded_steps: list[tuple[MatrixHapticSmokeStep, bytes]] = []
        for step in smoke_steps:
            packet = encode_matrix_channel_packet(step.channels)
            step.packet_hex = packet.hex()
            step.success = False
            encoded_steps.append((step, packet))
        if len(encoded_steps) == 1:
            result.packet_hex = encoded_steps[0][1].hex()
        result.connect_started_monotonic_ms = monotonic_ms()
        sock = socket_factory((result.host, result.port), timeout=result.connect_timeout_s)
        if hasattr(sock, "settimeout"):
            sock.settimeout(result.send_timeout_s)
        result.connected_monotonic_ms = monotonic_ms()
        if result.startup_settle_seconds > 0.0:
            sleep_fn(result.startup_settle_seconds)
        for index, (step, packet) in enumerate(encoded_steps):
            step.send_started_monotonic_ms = monotonic_ms()
            if index == 0:
                result.send_started_monotonic_ms = step.send_started_monotonic_ms
            try:
                sock.sendall(packet)
            except Exception as exc:
                step.success = False
                step.error = str(exc)
                result.error = str(exc)
                break
            step.sent_monotonic_ms = monotonic_ms()
            step.success = True
            result.sent_monotonic_ms = step.sent_monotonic_ms
        result.success = bool(encoded_steps) and all(
            step.success is True for step, _packet in encoded_steps
        )
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


def build_contact_to_pinch_sequence(
    *,
    contact_valid_channels: list[int],
    contact_valid_reset_channels: list[int],
    pinch_insufficient_channels: list[int],
) -> list[MatrixHapticSmokeStep]:
    return [
        MatrixHapticSmokeStep(
            role="main",
            key="contact_valid",
            channels=list(contact_valid_channels),
        ),
        MatrixHapticSmokeStep(
            role="reset",
            key="contact_valid",
            channels=list(contact_valid_reset_channels),
        ),
        MatrixHapticSmokeStep(
            role="main",
            key="pinch_insufficient",
            channels=list(pinch_insufficient_channels),
        ),
    ]


def parse_channels(value: str) -> list[int]:
    """Parse a comma-separated Matrix channel list."""

    if value is None or not str(value).strip():
        raise argparse.ArgumentTypeError("--channels must not be empty.")
    channels: list[int] = []
    for raw_item in str(value).split(","):
        item = raw_item.strip()
        if not item:
            raise argparse.ArgumentTypeError("--channels contains an empty item.")
        try:
            channel = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"matrix channel must be an integer: {item!r}"
            ) from exc
        if channel < 0 or channel > 127:
            raise argparse.ArgumentTypeError(
                f"matrix channel must be in 0..127, got {channel!r}."
            )
        channels.append(channel)
    return channels


def default_smoke_log_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("data") / "haptic_smoke" / f"matrix_haptic_smoke_{timestamp}.json"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone Matrix ESP32 haptic TCP smoke test."
    )
    parser.add_argument("--host", required=True, help="Matrix ESP32 TCP host/IP.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--channels",
        default=None,
        type=parse_channels,
        help="Comma-separated Matrix channels, for example: 1,2,3.",
    )
    parser.add_argument(
        "--sequence",
        choices=[MATRIX_SEQUENCE_CONTACT_TO_PINCH],
        default=None,
        help="Optional ordered sequence smoke instead of a single --channels packet.",
    )
    parser.add_argument(
        "--contact-valid-channels",
        type=parse_channels,
        default=None,
        help="Channels for sequence main output contact_valid.",
    )
    parser.add_argument(
        "--contact-valid-reset-channels",
        type=parse_channels,
        default=None,
        help="Reset channels for sequence previous key contact_valid.",
    )
    parser.add_argument(
        "--pinch-insufficient-channels",
        type=parse_channels,
        default=None,
        help="Channels for sequence main output pinch_insufficient.",
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
        help="Smoke JSON log path. Defaults to data/haptic_smoke/matrix_haptic_smoke_<timestamp>.json.",
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
    if args.sequence is None and args.channels is None:
        raise SystemExit("--channels is required unless --sequence is provided.")
    if args.sequence is not None and args.channels is not None:
        raise SystemExit("--channels cannot be combined with --sequence.")

    steps = None
    channels = args.channels
    if args.sequence == MATRIX_SEQUENCE_CONTACT_TO_PINCH:
        missing = [
            name
            for name, value in (
                ("--contact-valid-channels", args.contact_valid_channels),
                ("--contact-valid-reset-channels", args.contact_valid_reset_channels),
                ("--pinch-insufficient-channels", args.pinch_insufficient_channels),
            )
            if value is None
        ]
        if missing:
            raise SystemExit(
                f"{args.sequence} requires: {', '.join(missing)}."
            )
        steps = build_contact_to_pinch_sequence(
            contact_valid_channels=args.contact_valid_channels,
            contact_valid_reset_channels=args.contact_valid_reset_channels,
            pinch_insufficient_channels=args.pinch_insufficient_channels,
        )
        channels = None

    out_path = Path(args.out) if args.out is not None else default_smoke_log_path()
    result = run_matrix_haptic_smoke(
        host=args.host,
        port=args.port,
        channels=channels,
        steps=steps,
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
