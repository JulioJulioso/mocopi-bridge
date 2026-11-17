import json
import math
import socket
import tempfile
import unittest
from pathlib import Path

from mocopi_bridge.app import INSTRUCTIONS, BridgeService, Session
from mocopi_bridge.bvh_writer import BvhRecorder
from mocopi_bridge.config import load_config
from mocopi_bridge.forward import UdpForwarder
from mocopi_bridge.parser import (
    BoneDef,
    PacketError,
    Transform,
    encode_frame,
    encode_skeleton,
    parse_packet,
    tag,
)
from mocopi_bridge.skeleton import _mul, euler_unity_deg, forward_kinematics, heading

IDENTITY = (0.0, 0.0, 0.0, 1.0)
ROOT = Path(__file__).resolve().parents[1]


def _bone(bone_id: int, parent: int | None, pos: tuple[float, float, float]) -> BoneDef:
    return BoneDef(bone_id, parent, Transform(IDENTITY, pos))


def tpose() -> list[BoneDef]:
    return [
        _bone(0, None, (0.0, 0.95, 0.0)),
        _bone(1, 0, (0.0, 0.08, 0.0)),
        _bone(2, 1, (0.0, 0.07, 0.0)),
        _bone(3, 2, (0.0, 0.07, 0.0)),
        _bone(4, 3, (0.0, 0.06, 0.0)),
        _bone(5, 4, (0.0, 0.06, 0.0)),
        _bone(6, 5, (0.0, 0.05, 0.0)),
        _bone(7, 6, (0.0, 0.05, 0.0)),
        _bone(8, 7, (0.0, 0.06, 0.0)),
        _bone(9, 8, (0.0, 0.05, 0.0)),
        _bone(10, 9, (0.0, 0.10, 0.0)),
        _bone(11, 7, (0.05, 0.02, 0.0)),
        _bone(12, 11, (0.12, 0.0, 0.0)),
        _bone(13, 12, (0.25, 0.0, 0.0)),
        _bone(14, 13, (0.25, 0.0, 0.0)),
        _bone(15, 7, (-0.05, 0.02, 0.0)),
        _bone(16, 15, (-0.12, 0.0, 0.0)),
        _bone(17, 16, (-0.25, 0.0, 0.0)),
        _bone(18, 17, (-0.25, 0.0, 0.0)),
        _bone(19, 0, (-0.10, -0.05, 0.0)),
        _bone(20, 19, (0.0, -0.42, 0.0)),
        _bone(21, 20, (0.0, -0.40, 0.0)),
        _bone(22, 21, (0.0, -0.08, 0.08)),
        _bone(23, 0, (0.10, -0.05, 0.0)),
        _bone(24, 23, (0.0, -0.42, 0.0)),
        _bone(25, 24, (0.0, -0.40, 0.0)),
        _bone(26, 25, (0.0, -0.08, 0.08)),
    ]


def _locals(bones: list[BoneDef]) -> dict[int, Transform]:
    return {bone.bone_id: bone.rest for bone in bones}


class ParserTests(unittest.TestCase):
    def test_roundtrip_keeps_27_bones_and_parents(self):
        bones = tpose()
        packet = parse_packet(encode_skeleton(bones))
        self.assertEqual(packet.kind, "skdf")
        self.assertIsNotNone(packet.skeleton)
        assert packet.skeleton is not None
        self.assertEqual(len(packet.skeleton.bones), 27)
        self.assertIsNone(packet.skeleton.bones[0].parent_id)
        self.assertEqual(packet.skeleton.bones[10].parent_id, 9)
        self.assertEqual(packet.skeleton.bones[21].parent_id, 20)
        self.assertAlmostEqual(packet.skeleton.bones[0].rest.pos[1], 0.95)

    def test_frame_roundtrip_and_optional_fields(self):
        locals_ = _locals(tpose())
        locals_[0] = Transform(IDENTITY, (1.25, 0.95, -0.5))
        encoded = encode_frame(locals_, number=4, time_ms=40)
        parsed = parse_packet(encoded)
        assert parsed.frame is not None
        self.assertEqual(parsed.frame.number, 4)
        self.assertEqual(parsed.frame.time_ms, 40)
        self.assertEqual(len(parsed.frame.bones), 27)
        self.assertAlmostEqual(parsed.frame.bones[0].pos[0], 1.25)
        self.assertAlmostEqual(parsed.frame.bones[0].rot[3], 1.0)

        extra_field = tag("uttm", b"\x01\x00\x00\x00\x00\x00\x00\x00")
        time_at = encoded.find(b"time")
        time_size = int.from_bytes(encoded[time_at - 4 : time_at], "little")
        insert_at = time_at + 4 + time_size
        fram_at = encoded.find(b"fram")
        fram_size = int.from_bytes(encoded[fram_at - 4 : fram_at], "little")
        with_extra = (
            encoded[: fram_at - 4]
            + (fram_size + len(extra_field)).to_bytes(4, "little")
            + encoded[fram_at:insert_at]
            + extra_field
            + encoded[insert_at:]
        )
        extra = parse_packet(with_extra)
        assert extra.frame is not None
        self.assertEqual(len(extra.frame.bones), 27)

    def test_short_packet_is_rejected(self):
        with self.assertRaises(PacketError):
            parse_packet(b"\x00\x00")


class PoseTests(unittest.TestCase):
    def test_tpose_stacks_head_over_hip_over_feet(self):
        bones = tpose()
        definition = parse_packet(encode_skeleton(bones)).skeleton
        assert definition is not None
        world = forward_kinematics(definition, _locals(bones))
        hip_y = world[0].pos[1]
        head_y = world[10].pos[1]
        left_foot_y = world[21].pos[1]
        right_foot_y = world[25].pos[1]
        self.assertGreater(head_y, hip_y)
        self.assertLess(left_foot_y, hip_y)
        self.assertLess(right_foot_y, hip_y)

    def test_parent_rotation_moves_the_child(self):
        yaw = Transform((0.0, 0.70710678, 0.0, 0.70710678), (0.0, 0.0, 0.0))
        child = Transform(IDENTITY, (0.0, 0.0, 1.0))
        bones = [
            BoneDef(0, None, yaw),
            BoneDef(1, 0, child),
        ]
        definition = parse_packet(encode_skeleton(bones)).skeleton
        assert definition is not None
        world = forward_kinematics(definition, {0: yaw, 1: child})
        self.assertAlmostEqual(world[1].pos[0], 1.0, places=5)
        self.assertAlmostEqual(world[1].pos[2], 0.0, places=5)


class SessionTests(unittest.TestCase):
    def test_instructions_cover_phone_setup_and_recovery(self):
        self.assertIn("mocopi (UDP)", INSTRUCTIONS)
        self.assertIn("SEND", INSTRUCTIONS)
        self.assertIn("Reset pose", INSTRUCTIONS)
        self.assertIn("Recalibrate", INSTRUCTIONS)
        self.assertIn("Calibrate FBT", INSTRUCTIONS)

    def setUp(self):
        self.config = load_config(str(ROOT / "config.example.json"))
        self.session = Session(self.config)
        self.bones = tpose()
        self.session.feed(encode_skeleton(self.bones))
        self.session.feed(encode_frame(_locals(self.bones), number=1, time_ms=20))

    def test_osc_uses_three_floats_and_skips_head_rotation(self):
        messages = dict(self.session.osc_messages())
        for args in messages.values():
            self.assertEqual(len(args), 3)
            self.assertTrue(all(isinstance(value, float) for value in args))
        hip = messages["/tracking/trackers/1/position"]
        head = messages["/tracking/trackers/head/position"]
        left = messages["/tracking/trackers/3/position"]
        right = messages["/tracking/trackers/4/position"]
        self.assertGreater(head[1], hip[1])
        self.assertLess(left[1], hip[1])
        self.assertLess(right[1], hip[1])
        self.assertNotIn("/tracking/trackers/head/rotation", messages)
        self.assertNotIn("/tracking/trackers/2/position", messages)
        self.assertTrue(all("hand" not in address for address in messages))

    def test_reset_mirror_lock_and_new_calibration(self):
        shifted = list(self.bones)
        shifted[0] = _bone(0, None, (1.0, 0.95, 2.0))
        self.session.feed(encode_skeleton(shifted))
        self.session.feed(encode_frame(_locals(shifted), number=2, time_ms=40))
        self.assertEqual(self.session.handle_key("r"), "Position reset.")
        hip = dict(self.session.osc_messages())["/tracking/trackers/1/position"]
        self.assertAlmostEqual(hip[0], 0.0, places=5)
        self.assertAlmostEqual(hip[2], 0.0, places=5)

        self.session.feed(encode_skeleton(shifted))
        self.assertIsNotNone(self.session.reset_xz_yaw)

        self.assertEqual(self.session.handle_key("m"), "Mirror on.")
        mirrored = dict(self.session.osc_messages())["/tracking/trackers/1/position"]
        self.assertAlmostEqual(mirrored[0], -1.0, places=5)
        self.session.handle_key("m")

        self.session.handle_key("h")
        moved = list(shifted)
        moved[0] = _bone(0, None, (4.0, 0.95, 2.0))
        frame = _locals(moved)
        self.session.feed(encode_frame(frame, number=3, time_ms=60))
        locked = dict(self.session.osc_messages())
        self.assertAlmostEqual(locked["/tracking/trackers/1/position"][0], 1.0, places=5)
        self.assertGreater(locked["/tracking/trackers/3/position"][0], 2.0)

        taller = list(self.bones)
        taller[0] = _bone(0, None, (0.0, 1.05, 0.0))
        self.session.handle_key("r")
        self.assertIsNotNone(self.session.reset_xz_yaw)
        self.session.feed(encode_skeleton(taller))
        self.assertIsNone(self.session.reset_xz_yaw)

    def test_pause_stops_osc_and_stream_loss_warns_once(self):
        self.session.handle_key("p")
        self.assertEqual(self.session.osc_messages(), [])
        self.session.last_rx = 10.0
        self.session.ever_received = True
        warning = self.session.take_stream_warning(10.6)
        self.assertIsNotNone(warning)
        self.assertIn("No UDP data", warning or "")
        self.assertIsNone(self.session.take_stream_warning(11.0))

    def test_bvh_records_while_messages_keep_flowing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.session.recorder = BvhRecorder(directory)
            note = self.session.handle_key("b")
            self.assertIn("Recording BVH", note or "")
            self.session.feed(encode_frame(_locals(self.bones), number=5, time_ms=100))
            self.assertTrue(self.session.osc_messages())
            text = self.session.recorder.render()
            self.assertIn("ROOT root", text)
            self.assertIn("JOINT head", text)
            self.assertIn("MOTION", text)
            self.assertIn("Frames: 2", text)
            saved = self.session.handle_key("b")
            self.assertIsNotNone(saved)
            written = list(Path(directory).glob("*.bvh"))
            self.assertEqual(len(written), 1)
            self.assertIn("JOINT l_foot", written[0].read_text(encoding="utf-8"))


def _axis_quat(axis: str, degrees: float) -> tuple[float, float, float, float]:
    half = math.radians(degrees) / 2.0
    s, c = math.sin(half), math.cos(half)
    return {"x": (s, 0.0, 0.0, c), "y": (0.0, s, 0.0, c), "z": (0.0, 0.0, s, c)}[axis]


def _compose(*steps: tuple[str, float]) -> tuple[float, float, float, float]:
    """Matrix product of the steps, left to right."""
    result = IDENTITY
    for axis, degrees in steps:
        result = _mul(result, _axis_quat(axis, degrees))
    return result


def _same_rotation(test: unittest.TestCase, left, right) -> None:
    dot = abs(sum(a * b for a, b in zip(left, right)))
    test.assertAlmostEqual(dot, 1.0, places=6)


class RotationTests(unittest.TestCase):
    def test_osc_euler_matches_unity_quaternion_euler(self):
        for x, y, z in ((30.0, 45.0, 60.0), (-70.0, 170.0, -20.0), (10.0, -95.0, 120.0)):
            unity = _compose(("y", y), ("x", x), ("z", z))
            ex, ey, ez = euler_unity_deg(unity)
            _same_rotation(self, _compose(("y", ey), ("x", ex), ("z", ez)), unity)
            self.assertAlmostEqual(ex, x, places=4)

    def test_osc_euler_survives_gimbal_lock(self):
        unity = _compose(("y", 40.0), ("x", 90.0), ("z", 0.0))
        ex, ey, ez = euler_unity_deg(unity)
        _same_rotation(self, _compose(("y", ey), ("x", ex), ("z", ez)), unity)

    def test_bvh_channels_rebuild_the_bone_rotation(self):
        rot = _compose(("z", 50.0), ("y", 20.0), ("x", 30.0))
        bones = [BoneDef(0, None, Transform(rot, (0.0, 1.0, 0.0)))]
        definition = parse_packet(encode_skeleton(bones)).skeleton
        assert definition is not None
        with tempfile.TemporaryDirectory() as directory:
            recorder = BvhRecorder(directory)
            recorder.start(definition)
            recorder.add({0: bones[0].rest})
            text = recorder.render()
            recorder.stop()
        rotation_channels = next(line.split()[5:] for line in text.splitlines() if "CHANNELS" in line)
        values = [float(v) for v in text.strip().splitlines()[-1].split()[3:]]
        steps = [(name[0].lower(), value) for name, value in zip(rotation_channels, values)]
        _same_rotation(self, _compose(*steps), rot)

    def test_heading_ignores_roll_and_pitch(self):
        for tilt_axis in ("x", "z"):
            rot = _compose(("y", 35.0), (tilt_axis, 25.0))
            self.assertAlmostEqual(math.degrees(heading(rot)), 35.0, places=5)


class ConfigTests(unittest.TestCase):
    def _write(self, directory: str, payload: dict) -> str:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_bad_axes_and_bones_fail_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            for axes in (["x", "x", "z"], ["xy", "y", "z"], ["--x", "y", "z"], ["", "y", "z"]):
                with self.assertRaises(ValueError):
                    load_config(self._write(directory, {"axes": axes}))
            with self.assertRaises(ValueError):
                load_config(self._write(directory, {"trackers": {"hip": {"bone": "pelvis"}}}))
            self.assertEqual(load_config(self._write(directory, {"axes": ["-x", "z", "y"]}))["axes"], ["-x", "z", "y"])


class BvhRecorderTests(unittest.TestCase):
    def test_frames_stream_to_disk_and_part_file_is_removed(self):
        definition = parse_packet(encode_skeleton(tpose())).skeleton
        assert definition is not None
        with tempfile.TemporaryDirectory() as directory:
            recorder = BvhRecorder(directory)
            first = recorder.start(definition)
            for index in range(3):
                recorder.add(_locals(tpose()), now=index * 0.02)
            path = recorder.stop()
            second = recorder.start(definition)
            recorder.stop()
            self.assertEqual(path, first)
            self.assertNotEqual(first, second)
            self.assertEqual(list(Path(directory).glob("*.part")), [])
            text = first.read_text(encoding="utf-8")
            self.assertIn("Frames: 3", text)
            self.assertIn("Frame Time: 0.020000", text)

    def test_bone_with_missing_parent_is_still_recorded(self):
        bones = [_bone(0, None, (0.0, 1.0, 0.0)), _bone(5, 4, (0.0, 0.1, 0.0))]
        definition = parse_packet(encode_skeleton(bones)).skeleton
        assert definition is not None
        with tempfile.TemporaryDirectory() as directory:
            recorder = BvhRecorder(directory)
            recorder.start(definition)
            recorder.add(_locals(bones))
            text = recorder.render()
            recorder.stop()
        self.assertIn("torso_5", text)
        self.assertEqual(len(text.strip().splitlines()[-1].split()), 12)


class ServiceNoteTests(unittest.TestCase):
    def test_repeated_notes_are_queued_once(self):
        config = load_config(str(ROOT / "config.example.json"))
        service = BridgeService(config)
        try:
            with service.lock:
                for _ in range(50):
                    service._note("Ignored packet: packet is too short")
                service._note(None)
                service._note("UDP is back.")
            self.assertEqual(
                service.drain_notes(),
                ["Ignored packet: packet is too short", "UDP is back."],
            )
        finally:
            service.stop()


class ForwardTests(unittest.TestCase):
    def test_raw_datagram_is_copied_to_both_ports(self):
        left = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        right = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        left.bind(("127.0.0.1", 0))
        right.bind(("127.0.0.1", 0))
        left.settimeout(1)
        right.settimeout(1)
        payload = encode_frame(_locals(tpose()))
        forwarder = UdpForwarder([left.getsockname(), right.getsockname()])
        try:
            forwarder.send(payload)
            self.assertEqual(left.recvfrom(65535)[0], payload)
            self.assertEqual(right.recvfrom(65535)[0], payload)
        finally:
            forwarder.close()
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
