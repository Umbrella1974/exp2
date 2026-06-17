"""Tests for Matrix ESP32 packet encoding."""

from __future__ import annotations

import pytest

from matrix_haptic_protocol import (
    MATRIX_MAGIC,
    channel_list_to_payload,
    encode_matrix_channel_packet,
    encode_matrix_packet,
)


def test_encode_matrix_packet_uses_magic_length_payload_and_checksum() -> None:
    packet = encode_matrix_packet(bytes([1, 2, 255]))

    assert packet == MATRIX_MAGIC + bytes([3, 1, 2, 255, (1 + 2 + 255) & 0xFF])


def test_encode_matrix_packet_rejects_long_payload() -> None:
    with pytest.raises(ValueError, match="payload length"):
        encode_matrix_packet(bytes(range(129)))


def test_channel_list_to_payload_validates_range_and_type() -> None:
    assert channel_list_to_payload([0, 7, 127]) == bytes([0, 7, 127])

    with pytest.raises(ValueError, match="0..127"):
        channel_list_to_payload([-1])
    with pytest.raises(ValueError, match="integer"):
        channel_list_to_payload([1.5])  # type: ignore[list-item]


def test_encode_matrix_channel_packet() -> None:
    packet = encode_matrix_channel_packet([4, 5, 6])

    assert packet == MATRIX_MAGIC + bytes([3, 4, 5, 6, 15])
