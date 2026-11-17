"""Per-sensor UDP from the free-placement phone app.

A datagram is one JSON object. Sony skeleton packets are binary and do not
start with '{', so both streams can share the same port.

Sample:
    {"v":1,"type":"sensor","id":"sensor-a","role":"hip","q":[0,0,0,1],"a":[0,9.8,0]}

Object roles use role "prop" plus a name:
    {"v":1,"type":"sensor","id":"sensor-d","role":"prop","name":"box","q":[0,0,0,1],"a":[0,0,0]}

Reset stores the current orientation of one object, or of every object:
    {"v":1,"type":"reset","role":"prop","name":"box"}
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

BODY_ROLES = ("hip", "left_ankle", "right_ankle")


@dataclass(frozen=True)
class SensorSample:
    sensor_id: str
    role: str
    name: str
    rotation: tuple[float, float, float, float]
    acceleration: tuple[float, float, float]

    @property
    def key(self) -> str:
        if self.role == "prop":
            return f"prop:{self.name}"
        return self.role


@dataclass(frozen=True)
class SensorReset:
    role: str
    name: str

    @property
    def key(self) -> str:
        if self.role == "prop" and self.name:
            return f"prop:{self.name}"
        if self.role == "prop":
            return "prop:*"
        return self.role


def parse_sensor_datagram(data: bytes) -> SensorSample | SensorReset | None:
    if not data.startswith(b"{"):
        return None
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    kind = payload.get("type")
    role = str(payload.get("role", "")).strip()
    name = str(payload.get("name", "")).strip()
    if kind == "reset":
        if not role:
            return None
        return SensorReset(role=role, name=name)
    if kind != "sensor" or not role:
        return None
    rotation = _floats(payload.get("q"), 4) or (0.0, 0.0, 0.0, 1.0)
    acceleration = _floats(payload.get("a"), 3) or (0.0, 0.0, 0.0)
    sensor_id = str(payload.get("id", "")).strip() or role
    return SensorSample(sensor_id, role, name, rotation, acceleration)


def encode_sensor(
    sensor_id: str,
    role: str,
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    acceleration: tuple[float, float, float] = (0.0, 0.0, 0.0),
    name: str = "",
) -> bytes:
    payload = {
        "v": 1,
        "type": "sensor",
        "id": sensor_id,
        "role": role,
        "q": [float(value) for value in rotation],
        "a": [float(value) for value in acceleration],
    }
    if name:
        payload["name"] = name
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def encode_reset(role: str = "prop", name: str = "") -> bytes:
    payload: dict[str, object] = {"v": 1, "type": "reset", "role": role}
    if name:
        payload["name"] = name
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def encode_prop(name: str, rotation: tuple[float, float, float, float]) -> bytes:
    payload = {
        "v": 1,
        "type": "prop",
        "name": name,
        "q": [float(value) for value in rotation],
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def relative_rotation(
    current: tuple[float, float, float, float],
    reference: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Orientation of current relative to the stored reset pose."""
    return _mul(_inverse(reference), current)


def _inverse(rotation: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = rotation
    return (-x, -y, -z, w)


def _mul(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = left
    bx, by, bz, bw = right
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _floats(value: object, count: int) -> tuple[float, ...] | None:
    if not isinstance(value, list) or len(value) != count:
        return None
    try:
        numbers = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return numbers if all(math.isfinite(number) for number in numbers) else None
