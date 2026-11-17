"""SlimeVR UDP packets and the mocopi IMU notification layout.

The field order matches the stream SlimeVR already accepts from MoSlime:
https://github.com/moslime/moslime
That project is MIT licensed. This module is a separate implementation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# Characteristics as Bleak lists them on Windows (declaration handle in brackets).
# MoSlime reads the same ones by value handle: IMU on 73 (0x49), replies on 34 (0x22).
IMU_UUID = "25047e64-657c-4856-afcf-e315048a965b"  # [0x48] 36-byte motion stream
COMMAND_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"  # [0x1f] start and status commands
REPLY_UUID = "0000ff03-0000-1000-8000-00805f9b34fb"  # [0x21] command replies, battery
# Notifications carry 36 bytes. Below this ATT MTU the sensor cannot send them whole.
MIN_MTU = 39

# Public start and status commands used by the trackers.
START_STREAM = bytes.fromhex("7e0318d6010000")
REQUEST_STATUS = bytes.fromhex("7e0209029ada")

_MOUNT = (0.70710678118, 0.70710678118, 0.0, 0.0)
_STATUS_REPLY = b"\x7e\x07\x09"
_SERVER_HELLO = b"Hey OVR =D"


@dataclass(frozen=True)
class ImuSample:
    rotation: tuple[float, float, float, float]
    acceleration: tuple[float, float, float]
    counter: int


def parse_imu(data: bytes) -> ImuSample | None:
    """Read one IMU notification (36 bytes, 30 used). None for short or status packets."""
    if len(data) < 30 or is_status_reply(data):
        return None
    raw_w = _quat_component(data[8:10])
    raw_x = _quat_component(data[10:12])
    raw_y = _quat_component(data[12:14])
    raw_z = _quat_component(data[14:16])
    rotation = _multiply((raw_w, raw_x, raw_y, raw_z), _MOUNT)
    acceleration = (
        _float16(data[24:26]) * 0.12,
        _float16(data[28:30]) * 0.12,
        _float16(data[26:28]) * 0.12,
    )
    counter = int.from_bytes(data[1:8], "little")
    return ImuSample(rotation, acceleration, counter)


def build_handshake(mac: str, firmware: str, packet_id: int) -> bytes:
    label = f"mocopi-bridge - Puck Version:{firmware}"
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(mac_bytes) != 6:
        raise ValueError("a tracker address needs 6 bytes")
    build = min(int("".join(ch for ch in firmware if ch.isdigit()) or "0"), 0xFFFFFFFF)
    return b"".join(
        (
            _header(3, packet_id),
            struct.pack(">III", 15, 8, 7),
            struct.pack(">III", 0, 0, 0),
            struct.pack(">I", build),
            struct.pack("B", len(label)),
            label.encode("utf-8"),
            struct.pack("6s", mac_bytes),
            struct.pack("B", 255),
        )
    )


def build_sensor_info(packet_id: int) -> bytes:
    """Sensor 0, status 1 (SlimeVR firmware: 0 offline, 1 ok, 2 error), IMU type 8."""
    return _header(15, packet_id) + struct.pack("BBB", 0, 1, 8)


def build_rotation(rotation: tuple[float, float, float, float], packet_id: int) -> bytes:
    w, x, y, z = rotation
    return _header(17, packet_id) + struct.pack("BB", 0, 1) + struct.pack(">ffff", -x, z, y, w) + struct.pack("B", 0)


def build_acceleration(acceleration: tuple[float, float, float], packet_id: int) -> bytes:
    return _header(4, packet_id) + struct.pack(">fff", *acceleration) + struct.pack("B", 0)


def build_battery(voltage: float, fraction: float, packet_id: int) -> bytes:
    return _header(12, packet_id) + struct.pack(">ff", voltage, fraction)


def is_status_reply(payload: bytes) -> bool:
    return payload[:3] == _STATUS_REPLY


def is_server_hello(payload: bytes) -> bool:
    """SlimeVR answers a tracker handshake with packet 3 and 'Hey OVR =D <version>'."""
    return _SERVER_HELLO in payload


def battery_from_status(payload: bytes) -> tuple[float, float] | None:
    if len(payload) < 11 or not is_status_reply(payload):
        return None
    raw = int.from_bytes(payload[7:11], "big")
    fraction = min(max((raw - 67410) / 6501390, 0.0), 1.0)
    return fraction + 3.2, fraction


def _header(kind: int, packet_id: int) -> bytes:
    return struct.pack(">I", kind) + struct.pack(">Q", packet_id)


def _quat_component(raw: bytes) -> float:
    return int.from_bytes(raw, "little", signed=True) / 8192


def _float16(raw: bytes) -> float:
    return struct.unpack("<e", raw)[0]


def _multiply(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    w1, x1, y1, z1 = left
    w2, x2, y2, z2 = right
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 + y1 * w2 + z1 * x2 - x1 * z2,
        w1 * z2 + z1 * w2 + x1 * y2 - y1 * x2,
    )
