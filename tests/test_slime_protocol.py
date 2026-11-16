import struct
import unittest

from mocopi_bridge.slime_protocol import (
    START_STREAM,
    build_handshake,
    build_rotation,
    build_sensor_info,
    parse_imu,
)


class SlimeProtocolTests(unittest.TestCase):
    def test_imu_notification_becomes_a_finite_sample(self):
        packet = bytes.fromhex(
            "b0463e63d9095a17871580e840002702"
            "871580e840002702"
            "e5ab36b675b2"
            "d1b022b6179a"
        )
        sample = parse_imu(packet)
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(len(sample.rotation), 4)
        self.assertTrue(all(abs(part) < 5 for part in sample.rotation))
        self.assertGreater(sample.counter, 0)

    def test_slime_headers_match_the_server_packet_types(self):
        handshake = build_handshake("3C:38:F4:AA:BB:CC", "1.0.0", 0)
        self.assertEqual(handshake[:4], struct.pack(">I", 3))
        self.assertEqual(build_sensor_info(1)[:4], struct.pack(">I", 15))
        rotation = build_rotation((1.0, 0.0, 0.0, 0.0), 2)
        self.assertEqual(rotation[:4], struct.pack(">I", 17))
        self.assertEqual(START_STREAM[:1], b"\x7e")

    def test_short_notification_is_ignored(self):
        self.assertIsNone(parse_imu(b"\x00\x01"))


if __name__ == "__main__":
    unittest.main()
