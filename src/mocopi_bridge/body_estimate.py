"""Place hip and ankles from free sensors using height and the floor.

The sensors measure orientation. They do not measure where someone is standing
in the room, so the hips stay at a fixed height and the feet stay on the floor.
Turning the hip sensor turns that whole stance. VRChat still owns the head
view and the hands.
"""

from __future__ import annotations

import math

from mocopi_bridge.sensors import BODY_ROLES, SensorSample
from mocopi_bridge.skeleton import WorldBone

_HIP_ID = 0
_HEAD_ID = 10
_LEFT_FOOT_ID = 21
_RIGHT_FOOT_ID = 25
_STANCE = 0.12
_FOOT_Y = 0.05


def stance_world(samples: dict[str, SensorSample], height_m: float) -> dict[int, WorldBone]:
    height = max(float(height_m), 0.5)
    hip = samples.get("hip")
    yaw = _yaw(hip.rotation) if hip is not None else 0.0
    world: dict[int, WorldBone] = {}
    if hip is not None:
        world[_HIP_ID] = WorldBone(_HIP_ID, _turn(yaw, (0.0, height * 0.53, 0.0)), hip.rotation)
        world[_HEAD_ID] = WorldBone(_HEAD_ID, _turn(yaw, (0.0, height * 0.93, 0.0)), (0.0, 0.0, 0.0, 1.0))
    left = samples.get("left_ankle")
    if left is not None:
        world[_LEFT_FOOT_ID] = WorldBone(
            _LEFT_FOOT_ID, _turn(yaw, (-_STANCE, _FOOT_Y, 0.0)), left.rotation
        )
    right = samples.get("right_ankle")
    if right is not None:
        world[_RIGHT_FOOT_ID] = WorldBone(
            _RIGHT_FOOT_ID, _turn(yaw, (_STANCE, _FOOT_Y, 0.0)), right.rotation
        )
    return world


def is_body_role(role: str) -> bool:
    return role in BODY_ROLES


def _turn(yaw: float, pos: tuple[float, float, float]) -> tuple[float, float, float]:
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    x, y, z = pos
    return (cosine * x + sine * z, y, -sine * x + cosine * z)


def _yaw(rotation: tuple[float, float, float, float]) -> float:
    x, y, z, w = rotation
    return math.atan2(2.0 * (w * y + x * z), 1.0 - 2.0 * (y * y + z * z))
