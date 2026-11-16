import json
import socket
import unittest
from pathlib import Path

from mocopi_bridge.app import Session
from mocopi_bridge.config import load_config
from mocopi_bridge.forward import UdpForwarder
from mocopi_bridge.parser import encode_skeleton
from mocopi_bridge.sensors import encode_reset, encode_sensor, parse_sensor_datagram, relative_rotation

ROOT = Path(__file__).resolve().parents[1]
YAW_90 = (0.0, 0.70710678118, 0.0, 0.70710678118)


class SensorProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(str(ROOT / "config.example.json"))
        self.session = Session(self.config)

    def test_body_roles_estimate_height_and_floor(self):
        self.session.feed(encode_sensor("a", "hip"))
        self.session.feed(encode_sensor("b", "left_ankle"))
        self.session.feed(encode_sensor("c", "right_ankle"))
        messages = dict(self.session.osc_messages())
        hip = messages["/tracking/trackers/1/position"]
        head = messages["/tracking/trackers/head/position"]
        left = messages["/tracking/trackers/3/position"]
        right = messages["/tracking/trackers/4/position"]
        self.assertGreater(head[1], hip[1])
        self.assertGreater(hip[1], left[1])
        self.assertLess(left[0], 0.0)
        self.assertGreater(right[0], 0.0)
        self.assertAlmostEqual(left[1], 0.05, places=4)
        self.assertNotIn("/tracking/trackers/head/rotation", messages)
        self.assertTrue(all("hand" not in address for address in messages))
        self.assertIn("hip", self.session.status_line())

    def test_object_reset_zeroes_rotation_only(self):
        self.session.feed(encode_sensor("d", "prop", YAW_90, name="box"))
        first = json.loads(self.session.prop_datagrams()[0])
        self.assertEqual(first["name"], "box")
        self.assertAlmostEqual(first["q"][1], YAW_90[1], places=5)
        note = self.session.feed(encode_reset("prop", "box"))
        self.assertIn("box", note or "")
        zero = json.loads(self.session.prop_datagrams()[0])
        self.assertAlmostEqual(zero["q"][3], 1.0, places=5)
        self.session.feed(encode_sensor("d", "prop", YAW_90, name="box"))
        held = json.loads(self.session.prop_datagrams()[0])
        self.assertAlmostEqual(held["q"][3], 1.0, places=4)
        self.assertAlmostEqual(held["q"][1], 0.0, places=4)

    def test_relative_rotation_of_same_pose_is_identity(self):
        identity = relative_rotation(YAW_90, YAW_90)
        self.assertAlmostEqual(identity[3], 1.0, places=5)
        self.assertAlmostEqual(identity[1], 0.0, places=5)

    def test_sensor_packets_do_not_replace_sony_forwarding(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.3)
        session = Session(self.config, forwarder=UdpForwarder([receiver.getsockname()]))
        try:
            session.feed(encode_sensor("a", "hip"))
            with self.assertRaises(socket.timeout):
                receiver.recvfrom(65535)
            skeleton = encode_skeleton_min()
            session.feed(skeleton)
            self.assertEqual(receiver.recvfrom(65535)[0], skeleton)
            self.assertTrue(session.osc_messages())
        finally:
            session.forwarder.close()
            receiver.close()

    def test_unknown_text_is_not_a_sensor(self):
        self.assertIsNone(parse_sensor_datagram(b"hello"))
        self.assertIsNone(parse_sensor_datagram(b"{\"v\":2,\"type\":\"sensor\",\"role\":\"hip\"}"))


def encode_skeleton_min():
    from mocopi_bridge.parser import BoneDef, Transform

    bone = BoneDef(0, None, Transform((0.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0)))
    return encode_skeleton([bone])


if __name__ == "__main__":
    unittest.main()
