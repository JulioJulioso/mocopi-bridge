import asyncio
import logging
import socket
import struct
import sys
import time
import types
import unittest
from unittest import mock

from mocopi_bridge import slime_link
from mocopi_bridge.slime_link import (
    WARMUP_PACKETS,
    GattChar,
    TrackerSession,
    choose_channels,
    configured_addresses,
)
from mocopi_bridge.slime_protocol import (
    COMMAND_UUID,
    IMU_UUID,
    REPLY_UUID,
    START_STREAM,
    battery_from_status,
    build_handshake,
    build_rotation,
    build_sensor_info,
    parse_imu,
)

IMU_PACKET = bytes.fromhex(
    "b0463e63d9095a17871580e840002702"
    "871580e840002702"
    "e5ab36b675b2"
    "d1b022b6179a"
)
MAC = "3C:38:F4:AA:BB:CC"

# Characteristics a QM-SS1 lists through Bleak on Windows, from a real slime_link.log.
FULL_TABLE = [
    ("00002a00-0000-1000-8000-00805f9b34fb", 0x02, ["read"]),
    ("00002a19-0000-1000-8000-00805f9b34fb", 0x13, ["notify", "read"]),
    ("00002a2b-0000-1000-8000-00805f9b34fb", 0x17, ["notify", "read", "write"]),
    ("0000ff02-0000-1000-8000-00805f9b34fb", 0x1D, ["read"]),
    ("0000ff01-0000-1000-8000-00805f9b34fb", 0x1F, ["read", "write"]),
    ("0000ff03-0000-1000-8000-00805f9b34fb", 0x21, ["notify", "read"]),
    ("5f78df94-798c-46f5-990a-b3eb6a065c88", 0x2F, ["notify", "read"]),
    ("00002a26-0000-1000-8000-00805f9b34fb", 0x43, ["read"]),
    ("25047e64-657c-4856-afcf-e315048a965b", 0x48, ["notify", "read"]),
    ("e8fc21f5-3cf8-c8d3-fbfb-47ca26956e6d", 0x4E, ["notify", "read"]),
]
# What Windows returned on most retries: only the standard services.
PARTIAL_TABLE = [row for row in FULL_TABLE if row[0].startswith("00002a")]


def _gatt(table) -> list[GattChar]:
    return [GattChar(uuid, handle, frozenset(props)) for uuid, handle, props in table]


def _status_reply(raw: int) -> bytes:
    return b"\x7e\x07\x09\x00\x00\x00\x00" + raw.to_bytes(4, "big")


class SlimeProtocolTests(unittest.TestCase):
    def test_imu_notification_becomes_a_finite_sample(self):
        sample = parse_imu(IMU_PACKET)
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(len(sample.rotation), 4)
        self.assertTrue(all(abs(part) < 5 for part in sample.rotation))
        self.assertGreater(sample.counter, 0)

    def test_slime_headers_match_the_server_packet_types(self):
        handshake = build_handshake(MAC, "1.0.0", 0)
        self.assertEqual(handshake[:4], struct.pack(">I", 3))
        info = build_sensor_info(1)
        self.assertEqual(info[:4], struct.pack(">I", 15))
        self.assertEqual(info[12:15], bytes([0, 1, 8]))
        rotation = build_rotation((1.0, 0.0, 0.0, 0.0), 2)
        self.assertEqual(rotation[:4], struct.pack(">I", 17))
        self.assertEqual(START_STREAM[:1], b"\x7e")

    def test_handshake_accepts_any_firmware_text(self):
        for firmware in ("", "1.2.3-beta", "v2", "9" * 30):
            packet = build_handshake(MAC, firmware, 0)
            self.assertEqual(packet[:4], struct.pack(">I", 3))

    def test_short_notification_is_ignored(self):
        self.assertIsNone(parse_imu(b"\x00\x01"))

    def test_long_status_reply_is_battery_not_motion(self):
        reply = _status_reply(67410 + 6501390 // 2) + b"\x00" * 30
        self.assertIsNone(parse_imu(reply))
        reading = battery_from_status(reply)
        assert reading is not None
        self.assertAlmostEqual(reading[1], 0.5, places=3)

    def test_battery_fraction_stays_between_0_and_1(self):
        low = battery_from_status(_status_reply(0))
        high = battery_from_status(_status_reply(0xFFFFFFFF))
        assert low is not None and high is not None
        self.assertEqual(low[1], 0.0)
        self.assertEqual(high[1], 1.0)

    def test_real_table_maps_imu_command_and_replies(self):
        channels = choose_channels(_gatt(FULL_TABLE))
        self.assertEqual(channels.imu, IMU_UUID)
        self.assertEqual(channels.command, COMMAND_UUID)
        self.assertEqual(channels.reply, REPLY_UUID)
        self.assertTrue(channels.status_with_response)

    def test_partial_table_is_a_failed_connection(self):
        with self.assertRaises(RuntimeError) as caught:
            choose_channels(_gatt(PARTIAL_TABLE))
        self.assertIn("without the IMU stream", str(caught.exception))

    def test_addresses_are_normalized_deduplicated_and_checked(self):
        config = {"addresses": ["3c-38-f4-aa-bb-cc", MAC, "3C:38:F4:XX:XX:XX", ""]}
        self.assertEqual(configured_addresses(config), [MAC])
        with self.assertRaises(SystemExit):
            configured_addresses({"addresses": ["not a mac"]})


class FakeSlime:
    """A UDP socket standing in for SlimeVR Server."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.address = self.sock.getsockname()

    def kinds(self) -> list[tuple[int, tuple]]:
        found = []
        self.sock.settimeout(0.05)
        try:
            while True:
                data, source = self.sock.recvfrom(2048)
                found.append((struct.unpack(">I", data[:4])[0], source))
        except socket.timeout:
            pass
        return found

    def reply(self, target: tuple, payload: bytes) -> None:
        self.sock.sendto(payload, target)

    def close(self) -> None:
        self.sock.close()


class TrackerSessionTests(unittest.TestCase):
    def setUp(self):
        self.slime = FakeSlime()
        self.session = TrackerSession(MAC, self.slime.address)

    def tearDown(self):
        self.session.close()
        self.slime.close()

    def _stream(self, now: float) -> None:
        for _ in range(WARMUP_PACKETS + 1):
            self.session.handle_notification(IMU_PACKET, now=now)

    def test_handshake_waits_for_motion_after_warmup(self):
        self.session.begin(now=0.0)
        for _ in range(WARMUP_PACKETS):
            self.session.handle_notification(IMU_PACKET, now=1.0)
        self.session.service_server(now=5.0)
        self.assertEqual(self.slime.kinds(), [])
        self.session.handle_notification(IMU_PACKET, now=5.0)
        self.assertEqual([kind for kind, _ in self.slime.kinds()], [3, 15, 17, 4])
        self.session.handle_notification(_status_reply(3_000_000), now=5.0)
        self.assertEqual([kind for kind, _ in self.slime.kinds()], [12])

    def test_handshake_repeats_until_slimevr_answers(self):
        self.session.begin(now=0.0)
        self._stream(now=1.0)
        tracker = self.slime.kinds()[0][1]
        self.assertIsNone(self.session.service_server(now=1.5))
        self.assertEqual(self.slime.kinds(), [])
        self.session.service_server(now=2.1)
        self.assertEqual([kind for kind, _ in self.slime.kinds()], [3, 15])

        self.slime.reply(tracker, b"\x03Hey OVR =D 5")
        note = self._service_until_note(now=2.2)
        self.assertIn("accepted", note or "")
        self.assertTrue(self.session.registered)
        self.session.service_server(now=4.0)
        self.assertEqual(self.slime.kinds(), [])

        note = self.session.service_server(now=2.2 + slime_link.SERVER_SILENCE_S + 0.1)
        self.assertIn("stopped answering", note or "")
        self.assertFalse(self.session.registered)
        self.assertEqual([kind for kind, _ in self.slime.kinds()], [3, 15])

    def test_missing_server_does_not_raise(self):
        self.slime.close()
        self.session.begin(now=0.0)
        self._stream(now=0.5)
        for step in range(5):
            self.session.service_server(now=1.0 + step)
        self.assertFalse(self.session.registered)

    def _service_until_note(self, now: float) -> str | None:
        for _ in range(50):
            note = self.session.service_server(now=now)
            if note:
                return note
            time.sleep(0.01)
        return None


class FakeChar:
    def __init__(self, uuid: str, handle: int, properties: list[str]):
        self.uuid = uuid
        self.handle = handle
        self.properties = properties


class FakeService:
    def __init__(self, characteristics: list[FakeChar]):
        self.characteristics = characteristics


class FakeClient:
    instances: list["FakeClient"] = []
    table = FULL_TABLE
    connect_error: Exception | None = None

    def __init__(self, address=None, timeout=None, winrt=None):
        self.winrt = winrt
        self.services = [FakeService([FakeChar(*row) for row in self.table])]
        self.subscribed: list[str] = []
        self.writes: list[tuple[str, bytes, bool]] = []
        self.mtu_size = 247
        self.is_connected = False
        FakeClient.instances.append(self)

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.is_connected = True

    async def disconnect(self):
        self.is_connected = False

    async def start_notify(self, uuid, _callback):
        self.subscribed.append(uuid)

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append((uuid, bytes(data), response))


class BleakFlowTests(unittest.TestCase):
    def setUp(self):
        self.slime = FakeSlime()
        FakeClient.instances = []
        FakeClient.table = FULL_TABLE
        FakeClient.connect_error = None
        quiet = logging.getLogger("slime_link.test")
        quiet.propagate = False
        quiet.addHandler(logging.NullHandler())
        self.patches = [
            mock.patch.object(slime_link.asyncio, "sleep", mock.AsyncMock()),
            mock.patch.object(slime_link, "_logger", return_value=quiet),
            mock.patch.dict(sys.modules, {"bleak": types.SimpleNamespace(BleakClient=FakeClient)}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.slime.close()

    def test_stream_subscribes_to_the_imu_stream_then_replies(self):
        session = TrackerSession(MAC, self.slime.address)
        client = FakeClient()
        try:
            asyncio.run(slime_link._start_stream(client, session, "a"))
        finally:
            session.close()
        self.assertEqual(client.subscribed, [IMU_UUID, REPLY_UUID])
        self.assertEqual(client.writes[0], (COMMAND_UUID, START_STREAM, True))
        self.assertTrue(client.writes[1][2])
        self.assertEqual(self.slime.kinds(), [])

    def test_sensor_is_dropped_after_the_attempt_limit(self):
        FakeClient.table = PARTIAL_TABLE

        async def run():
            return await slime_link.hold_tracker(
                MAC, "a", self.slime.address, asyncio.Event(), asyncio.Lock(), max_attempts=3
            )

        self.assertFalse(asyncio.run(run()))
        self.assertEqual(len(FakeClient.instances), 3)
        self.assertTrue(all(client.winrt == {"use_cached_services": False} for client in FakeClient.instances))
        self.assertTrue(all(not client.is_connected for client in FakeClient.instances))
        self.assertEqual(self.slime.kinds(), [])


if __name__ == "__main__":
    unittest.main()
