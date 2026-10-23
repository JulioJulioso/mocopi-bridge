"""Reader for the binary UDP format sent by the mocopi phone app.

Each field is little-endian: a uint32 value size, a 4-letter ASCII name,
then the value. Nested fields use the same layout.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


class PacketError(ValueError):
    """The datagram has neither a skeleton nor a frame."""


BONE_NAMES: tuple[str, ...] = (
    "root",
    "torso_1",
    "torso_2",
    "torso_3",
    "torso_4",
    "torso_5",
    "torso_6",
    "torso_7",
    "neck_1",
    "neck_2",
    "head",
    "l_shoulder",
    "l_up_arm",
    "l_low_arm",
    "l_hand",
    "r_shoulder",
    "r_up_arm",
    "r_low_arm",
    "r_hand",
    "l_up_leg",
    "l_low_leg",
    "l_foot",
    "l_toes",
    "r_up_leg",
    "r_low_leg",
    "r_foot",
    "r_toes",
)

NAME_TO_ID: dict[str, int] = {name: index for index, name in enumerate(BONE_NAMES)}

_NO_PARENT = 65535


@dataclass(frozen=True)
class Transform:
    rot: tuple[float, float, float, float]
    pos: tuple[float, float, float]


@dataclass(frozen=True)
class BoneDef:
    bone_id: int
    parent_id: int | None
    rest: Transform


@dataclass(frozen=True)
class SkeletonDef:
    bones: dict[int, BoneDef]
    version: int = 1

    def signature(self) -> tuple:
        """Calibration identity. A repeated identical skdf does not change this."""
        rows = []
        for bone_id in sorted(self.bones):
            bone = self.bones[bone_id]
            pos = tuple(round(v, 5) for v in bone.rest.pos)
            rot = tuple(round(v, 5) for v in bone.rest.rot)
            rows.append((bone_id, bone.parent_id, pos, rot))
        return tuple(rows)


@dataclass(frozen=True)
class FrameData:
    number: int
    time_ms: int
    bones: dict[int, Transform]


@dataclass(frozen=True)
class MotionPacket:
    kind: str
    skeleton: SkeletonDef | None = None
    frame: FrameData | None = None


def parse_packet(data: bytes) -> MotionPacket:
    if len(data) < 8:
        raise PacketError("packet is too short")
    top = _fields(data)
    by_name = {name: payload for name, payload in top}
    version = 1
    if "head" in by_name:
        head = {name: payload for name, payload in _fields(by_name["head"])}
        if "vrsn" in head and head["vrsn"]:
            version = int.from_bytes(head["vrsn"], "little")
    if "skdf" in by_name:
        return MotionPacket("skdf", skeleton=_parse_skeleton(by_name["skdf"], version))
    if "fram" in by_name:
        return MotionPacket("fram", frame=_parse_frame(by_name["fram"]))
    raise PacketError("packet has neither skdf nor fram")


def _parse_skeleton(payload: bytes, version: int) -> SkeletonDef:
    bones: dict[int, BoneDef] = {}
    for name, block in _fields(payload):
        if name != "bons":
            continue
        for bone_name, bone_payload in _fields(block):
            if bone_name != "bndt":
                continue
            bone = _parse_definition(bone_payload)
            bones[bone.bone_id] = bone
    if not bones:
        raise PacketError("skdf has no bones")
    return SkeletonDef(bones=bones, version=version)


def _parse_definition(payload: bytes) -> BoneDef:
    parts = {name: value for name, value in _fields(payload)}
    if "bnid" not in parts or "tran" not in parts:
        raise PacketError("incomplete bndt")
    bone_id = int.from_bytes(parts["bnid"], "little")
    parent_id = None
    if "pbid" in parts:
        parent_raw = int.from_bytes(parts["pbid"], "little")
        if parent_raw != bone_id and parent_raw <= 26:
            parent_id = parent_raw
    return BoneDef(bone_id=bone_id, parent_id=parent_id, rest=_parse_transform(parts["tran"]))


def _parse_frame(payload: bytes) -> FrameData:
    number = 0
    time_ms = 0
    bones: dict[int, Transform] = {}
    for name, block in _fields(payload):
        if name == "fnum":
            number = int.from_bytes(block, "little")
        elif name == "time":
            time_ms = int.from_bytes(block, "little")
        elif name == "btrs":
            for bone_name, bone_payload in _fields(block):
                if bone_name != "btdt":
                    continue
                bone_id, transform = _parse_frame_bone(bone_payload)
                bones[bone_id] = transform
    if not bones:
        raise PacketError("fram has no bones")
    return FrameData(number=number, time_ms=time_ms, bones=bones)


def _parse_frame_bone(payload: bytes) -> tuple[int, Transform]:
    parts = {name: value for name, value in _fields(payload)}
    if "bnid" not in parts or "tran" not in parts:
        raise PacketError("incomplete btdt")
    bone_id = int.from_bytes(parts["bnid"], "little")
    return bone_id, _parse_transform(parts["tran"])


def _parse_transform(payload: bytes) -> Transform:
    if len(payload) < 28:
        raise PacketError("incomplete tran")
    qx, qy, qz, qw, px, py, pz = struct.unpack_from("<7f", payload)
    return Transform(rot=(qx, qy, qz, qw), pos=(px, py, pz))


def _fields(payload: bytes) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    index = 0
    size = len(payload)
    while index + 8 <= size:
        value_size = int.from_bytes(payload[index : index + 4], "little")
        name = payload[index + 4 : index + 8].decode("ascii", errors="replace")
        index += 8
        if value_size < 0 or index + value_size > size:
            raise PacketError(f"field {name!r} runs past the end of the packet")
        found.append((name, payload[index : index + value_size]))
        index += value_size
    return found


def tag(name: str, payload: bytes) -> bytes:
    if len(name) != 4:
        raise ValueError("field name must be 4 characters")
    return struct.pack("<I", len(payload)) + name.encode("ascii") + payload


def encode_skeleton(bones: list[BoneDef], version: int = 1, port: int = 12351) -> bytes:
    blocks = b"".join(_encode_definition(bone) for bone in bones)
    body = b"".join(
        (
            _encode_head(version),
            _encode_sender(port),
            tag("skdf", tag("bons", blocks)),
        )
    )
    return body


def encode_frame(
    bones: dict[int, Transform],
    number: int = 0,
    time_ms: int = 0,
    version: int = 1,
    port: int = 12351,
) -> bytes:
    blocks = b"".join(
        tag(
            "btdt",
            tag("bnid", struct.pack("<H", bone_id & 0xFFFF))
            + tag("tran", _pack_transform(transform)),
        )
        for bone_id, transform in bones.items()
    )
    fram = b"".join(
        (
            tag("fnum", struct.pack("<I", number & 0xFFFFFFFF)),
            tag("time", struct.pack("<I", time_ms & 0xFFFFFFFF)),
            tag("btrs", blocks),
        )
    )
    return _encode_head(version) + _encode_sender(port) + tag("fram", fram)


def _encode_definition(bone: BoneDef) -> bytes:
    parent = _NO_PARENT if bone.parent_id is None else bone.parent_id
    payload = b"".join(
        (
            tag("bnid", struct.pack("<H", bone.bone_id & 0xFFFF)),
            tag("pbid", struct.pack("<H", parent & 0xFFFF)),
            tag("tran", _pack_transform(bone.rest)),
        )
    )
    return tag("bndt", payload)


def _encode_head(version: int) -> bytes:
    payload = tag("ftyp", b"sony motion format") + tag("vrsn", struct.pack("<B", version & 0xFF))
    return tag("head", payload)


def _encode_sender(port: int) -> bytes:
    payload = tag("ipad", b"\x00" * 8) + tag("rcvp", struct.pack("<H", port & 0xFFFF))
    return tag("sndf", payload)


def _pack_transform(transform: Transform) -> bytes:
    qx, qy, qz, qw = transform.rot
    px, py, pz = transform.pos
    return struct.pack("<7f", qx, qy, qz, qw, px, py, pz)
