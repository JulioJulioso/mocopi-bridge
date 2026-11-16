"""Connect mocopi sensors over the PC's Bluetooth radio and feed SlimeVR.

Windows cannot run the original MoSlime script:
https://github.com/moslime/moslime
This uses Bleak, so the TP-Link UB500 works when it is the only active
Bluetooth adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time
from pathlib import Path

from mocopi_bridge.slime_protocol import (
    COMMAND_UUID,
    REQUEST_STATUS,
    SERVICE_UUID,
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


def looks_like_mocopi(name: str | None) -> bool:
    if not name:
        return False
    folded = name.lower()
    return any(hint in folded for hint in NAME_HINTS)


async def scan_addresses() -> list[tuple[str, str]]:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=8.0)
    chosen: list[tuple[str, str]] = []
    print("Nearby Bluetooth devices:")
    for device in found:
        label = device.name or "(no name)"
        mark = "  <-- mocopi" if looks_like_mocopi(device.name) else ""
        print(f"  {device.address}  {label}{mark}")
        if looks_like_mocopi(device.name):
            chosen.append((device.address, device.name or device.address))
    return chosen


async def hold_tracker(address: str, name: str, slime: tuple[str, int], stop: asyncio.Event) -> None:
    from bleak import BleakClient

    session = TrackerSession(address, slime)
    try:
        while not stop.is_set():
            try:
                print(f"Connecting {name} ({address})")
                async with BleakClient(address, timeout=20.0) as client:
                    await _start_stream(client, session)
                    print(f"Streaming {name}")
                    while client.is_connected and not stop.is_set():
                        await asyncio.sleep(0.5)
                        if session.last_sample and time.monotonic() - session.last_sample > 2.0:
                            print(f"{name} went quiet. Reconnecting.")
                            break
            except Exception as exc:
                print(f"{name} disconnected: {exc}")
            if stop.is_set():
                break
            print(f"Retrying {name} in 3 seconds.")
            await asyncio.sleep(3)
    finally:
        session.close()


async def _start_stream(client, session: TrackerSession) -> None:
    notify_uuid, command_uuid = _characteristics(client)
            session.send_hello("0")
    session.last_sample = time.monotonic()

    def on_notify(_sender, data: bytearray) -> None:
        payload = bytes(data)
        sample = parse_imu(payload)
        if sample is not None and len(payload) >= 30:
            session.send_sample(sample)
            return
        session.send_battery(payload)

    await client.start_notify(notify_uuid, on_notify)
    await client.write_gatt_char(command_uuid, START_STREAM, response=True)
    await asyncio.sleep(1.0)
    try:
        await client.write_gatt_char(command_uuid, REQUEST_STATUS, response=False)
    except Exception:
        pass


def _characteristics(client) -> tuple[str, str]:
    notify_uuid = None
    command_uuid = COMMAND_UUID
    for service in client.services:
        if service.uuid.lower() != SERVICE_UUID:
            continue
        for char in service.characteristics:
            props = {item.lower() for item in char.properties}
            if "notify" in props or "indicate" in props:
                notify_uuid = char.uuid
            if "write" in props or "write-without-response" in props:
                command_uuid = char.uuid
    if notify_uuid is None:
        raise RuntimeError("this tracker has no IMU notification")
    return notify_uuid, command_uuid


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
    tasks = [
        asyncio.create_task(hold_tracker(address, name, slime, stop))
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
