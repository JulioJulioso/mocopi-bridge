"""OSC trackers for VRChat full-body tracking."""

from __future__ import annotations

from mocopi_bridge.skeleton import WorldBone, bone_id, euler_xyz_deg


def build_messages(
    world: dict[int, WorldBone],
    trackers: dict,
) -> list[tuple[str, list[float]]]:
    messages: list[tuple[str, list[float]]] = []
    for spec in trackers.values():
        if not spec.get("enabled", False):
            continue
        resolved = bone_id(spec["bone"])
        bone = world.get(resolved)
        if bone is None:
            continue
        slot = spec.get("slot", 1)
        prefix = f"/tracking/trackers/{slot}"
        if spec.get("position", True):
            messages.append((f"{prefix}/position", [float(v) for v in bone.pos]))
        if spec.get("rotation", True):
            messages.append((f"{prefix}/rotation", [float(v) for v in euler_xyz_deg(bone.rot)]))
    return messages


class OscSender:
    def __init__(self, host: str, port: int):
        from pythonosc.udp_client import SimpleUDPClient

        self._client = SimpleUDPClient(host, port)

    def send(self, messages: list[tuple[str, list[float]]]) -> None:
        for address, args in messages:
            self._client.send_message(address, args)
