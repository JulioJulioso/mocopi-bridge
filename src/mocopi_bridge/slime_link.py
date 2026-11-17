"""Connect mocopi sensors over the PC's Bluetooth radio and feed SlimeVR.

Windows cannot run the original MoSlime script:
https://github.com/moslime/moslime
This uses Bleak, so the TP-Link UB500 works when it is the only active
Bluetooth adapter.

Connections are opened one at a time. Opening all six at once overflows the
adapter and Windows then lists a pile of dead GATT services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from mocopi_bridge.slime_protocol import (
    COMMAND_UUID,
    DATA_UUIDS,
    REQUEST_STATUS,
    START_STREAM,
    ImuSample,
    battery_from_status,
    build_acceleration,
    build_battery,
    build_handshake,
    build_rotation,
    build_sensor_info,
    parse_imu,
)

SLIME_PORT = 6969
NAME_HINTS = ("mocopi", "ss1", "qm-ss")


def load_link_config(path: Path) -> dict:
    if not path.is_file():
        example = {
            "addresses": [],
            "slime_ip": "127.0.0.1",
            "slime_port": SLIME_PORT,
            "autodiscovery": True,
        }
        path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
        print(f"Created {path.name}. Turn the sensors on and run this again.")
        raise SystemExit(0)
    return json.loads(path.read_text(encoding="utf-8"))


def discover_slime(timeout_s: float = 3.0) -> tuple[str, int]:
    """Listen for SlimeVR's broadcast. Fall back to this PC if the port is busy."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", SLIME_PORT))
    except OSError:
        sock.close()
        return "127.0.0.1", SLIME_PORT
    sock.settimeout(0.25)
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            try:
                _data, source = sock.recvfrom(1024)
            except socket.timeout:
                continue
            return source[0], source[1]
    finally:
        sock.close()
    return "127.0.0.1", SLIME_PORT


class TrackerSession:
    def __init__(self, address: str, slime: tuple[str, int]):
        self.address = address
        self.slime = slime
        self.packet_id = 0
        self.last_sample = 0.0
        self.packets = 0
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def close(self) -> None:
        self._socket.close()

    def send_hello(self, firmware: str = "0") -> None:
        self._send(build_handshake(self.address, firmware, self._next_id()))
        self._send(build_sensor_info(self._next_id()))

    def send_sample(self, sample: ImuSample) -> None:
        rotation, x, y, z = sample.rotation
        self._send(build_rotation((rotation, x, y, z), self._next_id()))
        self._send(build_acceleration(sample.acceleration, self._next_id()))
        self.last_sample = time.monotonic()

    def send_battery(self, payload: bytes) -> None:
        reading = battery_from_status(payload)
        if reading is None:
            return
        voltage, fraction = reading
        self._send(build_battery(voltage, fraction, self._next_id()))

    def _next_id(self) -> int:
        current = self.packet_id
        self.packet_id += 1
        return current

    def _send(self, payload: bytes) -> None:
        self._socket.sendto(payload, self.slime)


@dataclass(frozen=True)
class GattChar:
    uuid: str
    handle: int
    properties: frozenset[str]


def looks_like_mocopi(name: str | None) -> bool:
    if not name:
        return False
    folded = name.lower()
    return any(hint in folded for hint in NAME_HINTS)


def choose_channels(chars: list[GattChar]) -> tuple[str, list[str]]:
    """Pick the command characteristic and notification candidates, best first.

    Handle 0x0021 on these sensors is a GATT declaration, not the IMU stream.
    Subscribing to it makes Windows return Unreachable and drop the link.
    """
    command = COMMAND_UUID
    writers = [char for char in chars if "write" in char.properties or "write-without-response" in char.properties]
    preferred = [char for char in writers if char.uuid.lower() == COMMAND_UUID]
    if preferred:
        command = preferred[0].uuid
    elif writers:
        command = writers[0].uuid

    notify = [
        char
        for char in chars
        if "notify" in char.properties or "indicate" in char.properties
        if not _reserved_uuid(char.uuid)
    ]
    notify.sort(key=lambda char: (0 if char.uuid.lower() in DATA_UUIDS else 1, char.handle))
    if not notify:
        raise RuntimeError("this tracker has no IMU notification")
    return command, [char.uuid for char in notify]


def describe_channels(chars: list[GattChar]) -> str:
    lines = []
    for char in chars:
        props = ",".join(sorted(char.properties)) or "-"
        lines.append(f"handle {char.handle:#06x} {char.uuid} [{props}]")
    return "\n".join(lines)


def _reserved_uuid(uuid: str) -> bool:
    short = uuid.lower().replace("-", "")
    return short[4:8] in {"2800", "2801", "2803", "2901", "2902"} if short.startswith("0000") else False


def _backoff(attempt: int) -> int:
    return min(30, 5 * (2 ** max(attempt - 1, 0)))


def _logger() -> logging.Logger:
    log = logging.getLogger("slime_link")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
    file_handler = logging.FileHandler("slime_link.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    log.addHandler(file_handler)
    log.addHandler(stream)
    return log


async def scan_addresses() -> list[tuple[str, str]]:
    from bleak import BleakScanner

    log = _logger()
    found = await BleakScanner.discover(timeout=8.0)
    chosen: list[tuple[str, str]] = []
    log.info("Nearby Bluetooth devices:")
    for device in found:
        label = device.name or "(no name)"
        mark = "  <-- mocopi" if looks_like_mocopi(device.name) else ""
        log.info("  %s  %s%s", device.address, label, mark)
        if looks_like_mocopi(device.name):
            chosen.append((device.address, device.name or device.address))
    return chosen


async def hold_tracker(
    address: str,
    name: str,
    slime: tuple[str, int],
    stop: asyncio.Event,
    connect_lock: asyncio.Lock,
) -> None:
    from bleak import BleakClient

    log = _logger()
    session = TrackerSession(address, slime)
    attempt = 0
    try:
        while not stop.is_set():
            client = None
            try:
                async with connect_lock:
                    if stop.is_set():
                        return
                    log.info("Connecting %s (%s), attempt %s", name, address, attempt + 1)
                    client = BleakClient(address, timeout=20.0)
                    await client.connect()
                    await _start_stream(client, session, name)
                    log.info("Streaming %s. Waiting 2s before the next sensor.", name)
                    await asyncio.sleep(2)
                attempt = 0
                while client.is_connected and not stop.is_set():
                    await asyncio.sleep(0.5)
                    silent_for = time.monotonic() - session.last_sample
                    if session.packets == 0 and silent_for > 8:
                        log.warning("%s sent no IMU data for 8s. Reconnecting.", name)
                        break
                    if session.packets and silent_for > 5:
                        log.warning("%s went quiet. Reconnecting.", name)
                        break
            except Exception as exc:
                attempt += 1
                wait = _backoff(attempt)
                log.warning("%s disconnected: %s", name, exc)
                log.info("Retrying %s in %s seconds.", name, wait)
                await asyncio.sleep(wait)
            finally:
                if client is not None and client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            if stop.is_set():
                break
    finally:
        session.close()


async def _start_stream(client, session: TrackerSession, name: str) -> None:
    log = _logger()
    chars = [
        GattChar(char.uuid, int(getattr(char, "handle", 0)), frozenset(item.lower() for item in char.properties))
        for service in client.services
        for char in service.characteristics
    ]
    log.info("%s GATT table:\n%s", name, describe_channels(chars))
    command_uuid, notify_uuids = choose_channels(chars)
    log.info("%s command %s; notify order %s", name, command_uuid, ", ".join(notify_uuids))
    session.send_hello("0")
    session.last_sample = time.monotonic()
    session.packets = 0

    def on_notify(_sender, data: bytearray) -> None:
        payload = bytes(data)
        sample = parse_imu(payload)
        if sample is not None and len(payload) >= 30:
            session.packets += 1
            if session.packets == 1:
                log.info("%s first IMU packet, %s bytes", name, len(payload))
            session.send_sample(sample)
            return
        session.send_battery(payload)

    last_error = "no notification characteristic accepted"
    for notify_uuid in notify_uuids:
        try:
            await client.start_notify(notify_uuid, on_notify)
            log.info("%s notifications started on %s", name, notify_uuid)
            last_error = ""
            break
        except Exception as exc:
            last_error = str(exc)
            log.warning("%s could not notify on %s: %s", name, notify_uuid, exc)
    if last_error:
        raise RuntimeError(last_error)
    await client.write_gatt_char(command_uuid, START_STREAM, response=True)
    log.info("%s stream command sent", name)
    await asyncio.sleep(1.0)
    try:
        await client.write_gatt_char(command_uuid, REQUEST_STATUS, response=False)
    except Exception as exc:
        log.info("%s status request skipped: %s", name, exc)


async def run_link(config: dict, scan_only: bool) -> None:
    if config.get("autodiscovery", True):
        slime = discover_slime()
    else:
        slime = (str(config.get("slime_ip", "127.0.0.1")), int(config.get("slime_port", SLIME_PORT)))
    print(f"SlimeVR at {slime[0]}:{slime[1]}")
    print("Turn off the phone's Bluetooth so it does not take the sensors.")
    addresses = [item.upper() for item in config.get("addresses", []) if item and "XX" not in item.upper()]
    named = [(item, item) for item in addresses]
    if not named:
        named = await scan_addresses()
    elif scan_only:
        await scan_addresses()
        return
    if scan_only:
        return
    if not named:
        print("No mocopi sensors found. Turn them on, then run again.")
        return
    stop = asyncio.Event()
    connect_lock = asyncio.Lock()
    log = _logger()
    log.info("Connecting %s sensor(s), one at a time.", len(named))
    tasks = [
        asyncio.create_task(hold_tracker(address, name, slime, stop, connect_lock))
        for address, name in named
    ]
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        stop.set()
        for task in tasks:
            task.cancel()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send mocopi sensors to SlimeVR over the UB500.")
    parser.add_argument("--config", default="slime_link.json")
    parser.add_argument("--scan", action="store_true", help="List nearby Bluetooth devices and exit.")
    args = parser.parse_args(argv)
    config = load_link_config(Path(args.config))
    try:
        asyncio.run(run_link(config, args.scan))
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
