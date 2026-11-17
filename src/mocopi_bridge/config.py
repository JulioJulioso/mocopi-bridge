"""Bridge settings. The default preset targets a Quest 3."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from mocopi_bridge.parser import NAME_TO_ID


def default_config() -> dict:
    return {
        "listen_host": "0.0.0.0",
        "listen_port": 12351,
        "forward": [
            {"host": "127.0.0.1", "port": 12352},
            {"host": "127.0.0.1", "port": 12353},
        ],
        "osc": {"host": "127.0.0.1", "port": 9000},
        "scale": 1.0,
        "axes": ["x", "y", "z"],
        "stream_timeout_s": 0.5,
        "bvh": {"directory": "recordings", "autostart": False},
        "body_height_m": 1.7,
        "props": {"host": "127.0.0.1", "port": 12354},
        "trackers": {
            "hip": {
                "enabled": True,
                "bone": "root",
                "slot": 1,
                "position": True,
                "rotation": True,
            },
            "chest": {
                "enabled": False,
                "bone": "torso_7",
                "slot": 2,
                "position": True,
                "rotation": True,
            },
            "left_foot": {
                "enabled": True,
                "bone": "l_foot",
                "slot": 3,
                "position": True,
                "rotation": True,
            },
            "right_foot": {
                "enabled": True,
                "bone": "r_foot",
                "slot": 4,
                "position": True,
                "rotation": True,
            },
            "left_knee": {
                "enabled": False,
                "bone": "l_low_leg",
                "slot": 5,
                "position": True,
                "rotation": True,
            },
            "right_knee": {
                "enabled": False,
                "bone": "r_low_leg",
                "slot": 6,
                "position": True,
                "rotation": True,
            },
            "left_elbow": {
                "enabled": False,
                "bone": "l_low_arm",
                "slot": 7,
                "position": True,
                "rotation": True,
            },
            "right_elbow": {
                "enabled": False,
                "bone": "r_low_arm",
                "slot": 8,
                "position": True,
                "rotation": True,
            },
            "head": {
                "enabled": True,
                "bone": "head",
                "slot": "head",
                "position": True,
                "rotation": False,
            },
        },
    }


def load_config(path: str | None) -> dict:
    config = default_config()
    if path:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        _merge(config, loaded)
    _validate_axes(config["axes"])
    _validate_bones(config["trackers"])
    return config


def resolve_config(path: str | None) -> dict:
    """Use an explicit file, otherwise config.json, then config.example.json."""
    if path:
        return load_config(path)
    for name in ("config.json", "config.example.json"):
        candidate = Path.cwd() / name
        if candidate.is_file():
            return load_config(str(candidate))
    return load_config(None)


def forward_targets(config: dict) -> list[tuple[str, int]]:
    return [(item["host"], int(item["port"])) for item in config["forward"]]


def hip_bone_name(config: dict) -> str:
    return config["trackers"]["hip"]["bone"]


def _merge(base: dict, incoming: dict) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _validate_axes(axes: list[str]) -> None:
    if not isinstance(axes, list) or len(axes) != 3:
        raise ValueError("axes must contain 3 components")
    for axis in axes:
        if axis not in _AXES:
            raise ValueError(f"invalid axis {axis!r}. Use x, y, z, -x, -y, or -z")
    if len({axis[-1] for axis in axes}) != 3:
        raise ValueError(f"axes {axes} repeat a component. Use each of x, y, z once")


def _validate_bones(trackers: dict) -> None:
    if "hip" not in trackers:
        raise ValueError("trackers must define hip")
    for label, spec in trackers.items():
        bone = spec.get("bone")
        if bone not in NAME_TO_ID:
            raise ValueError(
                f"tracker {label!r} uses unknown bone {bone!r}. Valid names: {', '.join(NAME_TO_ID)}"
            )


_AXES = ("x", "y", "z", "-x", "-y", "-z")


def example_text() -> str:
    return json.dumps(deepcopy(default_config()), indent=2) + "\n"
