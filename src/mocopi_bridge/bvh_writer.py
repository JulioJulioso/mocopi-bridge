"""BVH recording of the skeleton while the live stream keeps running."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mocopi_bridge.parser import BONE_NAMES, SkeletonDef, Transform
from mocopi_bridge.skeleton import euler_xyz_deg


class BvhRecorder:
    def __init__(self, directory: str):
        self.directory = directory
        self.active = False
        self.path: Path | None = None
        self._definition: SkeletonDef | None = None
        self._frames: list[dict[int, Transform]] = []

    def start(self, definition: SkeletonDef) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = Path(self.directory) / f"mocopi-{stamp}.bvh"
        self._definition = definition
        self._frames = []
        self.active = True
        return self.path

    def add(self, locals_: dict[int, Transform]) -> None:
        if self.active:
            self._frames.append(dict(locals_))

    def render(self) -> str:
        if self._definition is None:
            raise RuntimeError("no skeleton to record")
        return _render(self._definition, self._frames)

    def stop(self) -> Path | None:
        if not self.active or self._definition is None or self.path is None:
            self.active = False
            return None
        text = self.render()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")
        self.active = False
        return self.path


def _render(definition: SkeletonDef, frames: list[dict[int, Transform]]) -> str:
    roots = [bone.bone_id for bone in definition.bones.values() if bone.parent_id is None]
    if not roots:
        roots = [min(definition.bones)]
    order: list[int] = []
    lines = ["HIERARCHY"]
    for root_id in roots:
        _write_joint(definition, root_id, 0, True, lines, order)
    lines.append("MOTION")
    lines.append(f"Frames: {len(frames)}")
    lines.append("Frame Time: 0.020000")
    for frame in frames:
        values: list[str] = []
        for bone_id in order:
            transform = frame.get(bone_id, definition.bones[bone_id].rest)
            px, py, pz = transform.pos
            rx, ry, rz = euler_xyz_deg(transform.rot)
            values.extend(
                f"{number:.6f}"
                for number in (px, py, pz, rz, rx, ry)
            )
        lines.append(" ".join(values))
    return "\n".join(lines) + "\n"


def _write_joint(
    definition: SkeletonDef,
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
    lines.append(
        f"{indent}  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation"
    )
    order.append(bone_id)
    children = [
        bone.bone_id
        for bone in definition.bones.values()
        if bone.parent_id == bone_id
    ]
    children.sort()
    if not children:
        lines.append(f"{indent}  End Site")
        lines.append(f"{indent}  {{")
        lines.append(f"{indent}    OFFSET 0.000000 0.000000 0.000000")
        lines.append(f"{indent}  }}")
    else:
        for child in children:
            _write_joint(definition, child, depth + 1, False, lines, order)
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
