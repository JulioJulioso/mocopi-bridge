"""BVH recording of the skeleton while the live stream keeps running.

Frames go to a .part file next to the take, so a long session does not grow in memory.
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from mocopi_bridge.parser import BONE_NAMES, SkeletonDef, Transform
from mocopi_bridge.skeleton import euler_xyz_deg

_DEFAULT_FRAME_TIME = 0.02
_MIN_FRAME_TIME = 1.0 / 240.0


class BvhRecorder:
    def __init__(self, directory: str):
        self.directory = directory
        self.active = False
        self.path: Path | None = None
        self._hierarchy: list[str] = []
        self._order: list[int] = []
        self._rest: dict[int, Transform] = {}
        self._part: TextIO | None = None
        self._frames = 0
        self._first_at: float | None = None
        self._last_at: float | None = None

    def start(self, definition: SkeletonDef) -> Path:
        if self.active:
            self.stop()
        directory = Path(self.directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = _unused_path(directory, f"mocopi-{datetime.now():%Y%m%d-%H%M%S}")
        self._hierarchy, self._order = _hierarchy(definition)
        self._rest = {bone_id: bone.rest for bone_id, bone in definition.bones.items()}
        self._part = _part_path(self.path).open("w+", encoding="utf-8", newline="\n")
        self._frames = 0
        self._first_at = None
        self._last_at = None
        self.active = True
        return self.path

    def add(self, locals_: dict[int, Transform], now: float | None = None) -> None:
        if not self.active or self._part is None:
            return
        self._part.write(_motion_line(self._order, self._rest, locals_))
        self._frames += 1
        moment = time.monotonic() if now is None else now
        if self._first_at is None:
            self._first_at = moment
        self._last_at = moment

    def render(self) -> str:
        if self._part is None:
            raise RuntimeError("no take in progress")
        self._part.flush()
        self._part.seek(0)
        motion = self._part.read()
        self._part.seek(0, 2)
        return self._header() + motion

    def stop(self) -> Path | None:
        part = self._part
        self.active = False
        self._part = None
        if part is None or self.path is None:
            return None
        try:
            part.flush()
            part.seek(0)
            with self.path.open("w", encoding="utf-8", newline="\n") as out:
                out.write(self._header())
                shutil.copyfileobj(part, out)
        finally:
            part.close()
        _part_path(self.path).unlink(missing_ok=True)
        return self.path

    def _header(self) -> str:
        return "\n".join(
            self._hierarchy
            + ["MOTION", f"Frames: {self._frames}", f"Frame Time: {self._frame_time():.6f}"]
        ) + "\n"

    def _frame_time(self) -> float:
        if self._frames < 2 or self._first_at is None or self._last_at is None:
            return _DEFAULT_FRAME_TIME
        measured = (self._last_at - self._first_at) / (self._frames - 1)
        return measured if measured >= _MIN_FRAME_TIME else _DEFAULT_FRAME_TIME


def _part_path(path: Path) -> Path:
    return path.with_name(path.name + ".part")


def _unused_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.bvh"
    index = 2
    while candidate.exists() or _part_path(candidate).exists():
        candidate = directory / f"{stem}-{index}.bvh"
        index += 1
    return candidate


def _motion_line(order: list[int], rest: dict[int, Transform], frame: dict[int, Transform]) -> str:
    values: list[str] = []
    for bone_id in order:
        transform = frame.get(bone_id, rest[bone_id])
        px, py, pz = transform.pos
        rx, ry, rz = euler_xyz_deg(transform.rot)
        values.extend(f"{number:.6f}" for number in (px, py, pz, rz, ry, rx))
    return " ".join(values) + "\n"


def _hierarchy(definition: SkeletonDef) -> tuple[list[str], list[int]]:
    children: dict[int | None, list[int]] = {}
    for bone in definition.bones.values():
        parent = bone.parent_id if bone.parent_id in definition.bones else None
        children.setdefault(parent, []).append(bone.bone_id)
    for ids in children.values():
        ids.sort()
    roots = children.get(None) or [min(definition.bones)]
    lines = ["HIERARCHY"]
    order: list[int] = []
    for root_id in roots:
        _write_joint(children, root_id, 0, True, lines, order)
    return lines, order


def _write_joint(
    children: dict[int | None, list[int]],
    bone_id: int,
    depth: int,
    is_root: bool,
    lines: list[str],
    order: list[int],
) -> None:
    indent = "  " * depth
    name = BONE_NAMES[bone_id] if bone_id < len(BONE_NAMES) else f"bone_{bone_id}"
    kind = "ROOT" if is_root else "JOINT"
    lines.append(f"{indent}{kind} {name}")
    lines.append(f"{indent}{{")
    lines.append(f"{indent}  OFFSET 0.000000 0.000000 0.000000")
    # euler_xyz_deg decomposes Rz * Ry * Rx, so the channels must read Z, Y, X.
    lines.append(
        f"{indent}  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation"
    )
    order.append(bone_id)
    kids = children.get(bone_id, [])
    if not kids:
        lines.append(f"{indent}  End Site")
        lines.append(f"{indent}  {{")
        lines.append(f"{indent}    OFFSET 0.000000 0.000000 0.000000")
        lines.append(f"{indent}  }}")
    else:
        for child in kids:
            _write_joint(children, child, depth + 1, False, lines, order)
    lines.append(f"{indent}}}")


def scaled_locals(
    locals_: dict[int, Transform],
    scale: float,
) -> dict[int, Transform]:
    if scale == 1.0:
        return locals_
    return {
        bone_id: Transform(
            transform.rot,
            (transform.pos[0] * scale, transform.pos[1] * scale, transform.pos[2] * scale),
        )
        for bone_id, transform in locals_.items()
    }
