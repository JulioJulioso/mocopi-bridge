"""Forward kinematics and adjustments that affect OSC output only."""

from __future__ import annotations

import math
from dataclasses import dataclass

from mocopi_bridge.parser import BONE_NAMES, NAME_TO_ID, BoneDef, SkeletonDef, Transform


@dataclass
class WorldBone:
    bone_id: int
    pos: tuple[float, float, float]
    rot: tuple[float, float, float, float]


@dataclass
class PoseAdjust:
    mirror: bool = False
    reset_xz_yaw: tuple[float, float, float] | None = None
    locked_hip: tuple[float, float, float] | None = None


def local_transforms(
    definition: SkeletonDef,
    frame_bones: dict[int, Transform] | None,
) -> dict[int, Transform]:
    frame_bones = frame_bones or {}
    locals_: dict[int, Transform] = {}
    for bone_id, bone in definition.bones.items():
        locals_[bone_id] = frame_bones.get(bone_id, bone.rest)
    return locals_


def forward_kinematics(
    definition: SkeletonDef,
    locals_: dict[int, Transform],
) -> dict[int, WorldBone]:
    world: dict[int, WorldBone] = {}

    def solve(bone_id: int, trail: set[int]) -> WorldBone:
        if bone_id in world:
            return world[bone_id]
        if bone_id in trail:
            raise ValueError(f"bone cycle at {bone_id}")
        trail.add(bone_id)
        bone: BoneDef = definition.bones[bone_id]
        local = locals_[bone_id]
        if bone.parent_id is None or bone.parent_id not in definition.bones:
            solved = WorldBone(bone_id, local.pos, _normalize(local.rot))
        else:
            parent = solve(bone.parent_id, trail)
            pos = _add(parent.pos, _rotate(parent.rot, local.pos))
            rot = _normalize(_mul(parent.rot, local.rot))
            solved = WorldBone(bone_id, pos, rot)
        world[bone_id] = solved
        return solved

    for bone_id in definition.bones:
        solve(bone_id, set())
    return world


def present_world(
    world: dict[int, WorldBone],
    axes: list[str],
    scale: float,
    adjust: PoseAdjust,
    hip_bone_id: int = 0,
) -> dict[int, WorldBone]:
    basis = _basis(axes)
    presented: dict[int, WorldBone] = {}
    for bone_id, bone in world.items():
        pos = _scale(_mat_vec(basis, bone.pos), scale)
        rot = _remap_rotation(bone.rot, basis)
        if adjust.mirror:
            pos = (-pos[0], pos[1], pos[2])
            rot = _reflect_x(rot)
        if adjust.reset_xz_yaw is not None:
            origin_x, origin_z, origin_yaw = adjust.reset_xz_yaw
            pos = _rot_y((pos[0] - origin_x, pos[1], pos[2] - origin_z), -origin_yaw)
            rot = _normalize(_mul(_quat_yaw(-origin_yaw), rot))
        presented[bone_id] = WorldBone(bone_id, pos, rot)
    if adjust.locked_hip is not None and hip_bone_id in presented:
        hip = presented[hip_bone_id]
        presented[hip_bone_id] = WorldBone(hip_bone_id, adjust.locked_hip, hip.rot)
    return presented


def capture_reset(hip: WorldBone) -> tuple[float, float, float]:
    return (hip.pos[0], hip.pos[2], heading(hip.rot))


def euler_unity_deg(rot: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """(x, y, z) degrees for Unity's Quaternion.Euler: z first, then x, then y.

    VRChat reads OSC tracker rotation in this convention.
    """
    x, y, z, w = _normalize(rot)
    sin_x = max(-1.0, min(1.0, 2.0 * (w * x - y * z)))
    pitch = math.asin(sin_x)
    if abs(sin_x) < 0.9999999:
        yaw = math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))
        roll = math.atan2(2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z))
    else:
        yaw = math.atan2(-2.0 * (x * z - w * y), 1.0 - 2.0 * (y * y + z * z))
        roll = 0.0
    return (math.degrees(pitch), math.degrees(yaw), math.degrees(roll))


def euler_xyz_deg(rot: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """(x, y, z) degrees for the rotation Rz(z) * Ry(y) * Rx(x)."""
    x, y, z, w = _normalize(rot)
    sin_roll = 2.0 * (w * x + y * z)
    cos_roll = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sin_roll, cos_roll)
    sin_pitch = 2.0 * (w * y - z * x)
    if abs(sin_pitch) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sin_pitch)
    else:
        pitch = math.asin(sin_pitch)
    sin_yaw = 2.0 * (w * z + x * y)
    cos_yaw = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(sin_yaw, cos_yaw)
    return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))


def bone_id(name: str) -> int:
    try:
        return NAME_TO_ID[name]
    except KeyError as exc:
        known = ", ".join(BONE_NAMES)
        raise KeyError(f"unknown bone {name!r}. Valid names: {known}") from exc


def heading(rot: tuple[float, float, float, float]) -> float:
    """Turn about +Y in radians, taken from where the local +Z axis points."""
    x, y, z, w = _normalize(rot)
    return math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))


def _quat_yaw(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, math.sin(yaw * 0.5), 0.0, math.cos(yaw * 0.5))


def _rot_y(pos: tuple[float, float, float], angle: float) -> tuple[float, float, float]:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    x, y, z = pos
    return (cosine * x + sine * z, y, -sine * x + cosine * z)


def _reflect_x(rot: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    reflected = _mat_mul(_REFLECT_X, _mat_mul(_quat_to_matrix(rot), _REFLECT_X))
    return _matrix_to_quat(reflected)


def _remap_rotation(
    rot: tuple[float, float, float, float],
    basis: list[list[float]],
) -> tuple[float, float, float, float]:
    if basis == _IDENTITY:
        return _normalize(rot)
    transformed = _mat_mul(basis, _mat_mul(_quat_to_matrix(rot), _transpose(basis)))
    return _matrix_to_quat(transformed)


def _basis(axes: list[str]) -> list[list[float]]:
    matrix = [[0.0, 0.0, 0.0] for _ in range(3)]
    for row, axis in enumerate(axes):
        sign = -1.0 if axis.startswith("-") else 1.0
        column = "xyz".index(axis[-1])
        matrix[row][column] = sign
    return matrix


def _add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(pos: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (pos[0] * factor, pos[1] * factor, pos[2] * factor)


def _mat_vec(matrix: list[list[float]], vec: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[row][col] * vec[col] for col in range(3)) for row in range(3))  # type: ignore[return-value]


def _rotate(rot: tuple[float, float, float, float], vec: tuple[float, float, float]) -> tuple[float, float, float]:
    qx, qy, qz, qw = rot
    vx, vy, vz = vec
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


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


def _normalize(rot: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, z, w = rot
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def _quat_to_matrix(rot: tuple[float, float, float, float]) -> list[list[float]]:
    x, y, z, w = _normalize(rot)
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _matrix_to_quat(matrix: list[list[float]]) -> tuple[float, float, float, float]:
    trace = matrix[0][0] + matrix[1][1] + matrix[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2][1] - matrix[1][2]) / scale
        y = (matrix[0][2] - matrix[2][0]) / scale
        z = (matrix[1][0] - matrix[0][1]) / scale
    elif matrix[0][0] > matrix[1][1] and matrix[0][0] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[0][0] - matrix[1][1] - matrix[2][2]) * 2.0
        w = (matrix[2][1] - matrix[1][2]) / scale
        x = 0.25 * scale
        y = (matrix[0][1] + matrix[1][0]) / scale
        z = (matrix[0][2] + matrix[2][0]) / scale
    elif matrix[1][1] > matrix[2][2]:
        scale = math.sqrt(1.0 + matrix[1][1] - matrix[0][0] - matrix[2][2]) * 2.0
        w = (matrix[0][2] - matrix[2][0]) / scale
        x = (matrix[0][1] + matrix[1][0]) / scale
        y = 0.25 * scale
        z = (matrix[1][2] + matrix[2][1]) / scale
    else:
        scale = math.sqrt(1.0 + matrix[2][2] - matrix[0][0] - matrix[1][1]) * 2.0
        w = (matrix[1][0] - matrix[0][1]) / scale
        x = (matrix[0][2] + matrix[2][0]) / scale
        y = (matrix[1][2] + matrix[2][1]) / scale
        z = 0.25 * scale
    return _normalize((x, y, z, w))


def _mat_mul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(left[row][k] * right[k][col] for k in range(3)) for col in range(3)]
        for row in range(3)
    ]


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix)]


_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
_REFLECT_X = [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
