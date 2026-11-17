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
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from mocopi_bridge.slime_protocol import (
    COMMAND_UUID,
    IMU_UUID,
    MIN_MTU,
    REPLY_UUID,
    REQUEST_STATUS,
    START_STREAM,
    ImuSample,
    battery_from_status,
    build_acceleration,
    build_battery,
    build_handshake,
    build_rotation,
    build_sensor_info,
    is_server_hello,
    is_status_reply,
    parse_imu,
)

SLIME_PORT = 6969
NAME_HINTS = ("mocopi", "ss1", "qm-ss")
# The first quaternions after START_STREAM are unsettled. MoSlime drops the same count.
WARMUP_PACKETS = 40
HELLO_RETRY_S = 1.0
# SlimeVR sends a heartbeat to every tracker twice a second.
SERVER_SILENCE_S = 5.0
NO_DATA_S = 8.0
QUIET_S = 5.0
STATUS_EVERY_S = 60.0
MAX_ATTEMPTS = 8
_MAC = re.compile(r"^[0-9A-F]{2}([:-][0-9A-F]{2}){5}$")


def load_link_config(path: Path) -> dict:
    if not path.is_file():
        example = {
            "addresses": [],
            "slime_ip": "127.0.0.1",
            "slime_port": SLIME_PORT,
            "autodiscovery": True,
            "max_attempts": MAX_ATTEMPTS,
        }
        path.write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
        print(f"Created {path.name}. Turn the sensors on and run this again.")
        raise SystemExit(0)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} is not valid JSON (line {exc.lineno}): {exc.msg}") from exc


def configured_addresses(config: dict) -> list[str]:
    """Upper-case MACs from the config, placeholders skipped, duplicates dropped."""
    found: list[str] = []
    for item in config.get("addresses", []) or []:
        address = str(item).strip().upper().replace("-", ":")
        if not address or "XX" in address:
            continue
        if not _MAC.match(address):
            raise SystemExit(f"{item!r} in addresses is not a Bluetooth address like 3C:38:F4:AA:BB:CC.")
        if address not in found:
            found.append(address)
    return found


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
    """One tracker as SlimeVR sees it: its UDP socket, packet counter and handshake state.

    Packet numbers keep counting across Bluetooth reconnects. SlimeVR drops a packet
    whose number is not above the last one it accepted from this tracker.
    """

    def __init__(self, address: str, slime: tuple[str, int]):
        self.address = address
        self.slime = slime
        self.packet_id = 0
        self.firmware = "0"
        self.registered = False
        self.started = 0.0
        self.last_imu = 0.0
        self.imu_packets = 0
        self.packets = 0
        self.warmup = 0
        self.last_hello = 0.0
        self.last_server_rx = 0.0
        self._send_failing = False
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("0.0.0.0", 0))
        self._socket.setblocking(False)

    def close(self) -> None:
        self._socket.close()

    def begin(self, firmware: str = "0", now: float | None = None) -> None:
        """Reset per-connection counters. Call after each connect.

        The handshake waits for real motion, so SlimeVR only lists sensors that stream.
        """
        moment = time.monotonic() if now is None else now
        self.firmware = firmware
        self.started = moment
        self.last_imu = moment
        self.imu_packets = 0
        self.packets = 0
        self.warmup = WARMUP_PACKETS

    def send_hello(self, now: float | None = None) -> None:
        self.last_hello = time.monotonic() if now is None else now
        self._send(build_handshake(self.address, self.firmware, self._next_id()))
        self._send(build_sensor_info(self._next_id()))

    def handle_notification(self, payload: bytes, now: float | None = None) -> None:
        if is_status_reply(payload):
            self.send_battery(payload)
            return
        sample = parse_imu(payload)
        if sample is None:
            return
        self.last_imu = time.monotonic() if now is None else now
        self.imu_packets += 1
        if self.warmup > 0:
            self.warmup -= 1
            return
        self.packets += 1
        if self.packets == 1 and not self.registered:
            self.send_hello(self.last_imu)
        self.send_sample(sample)

    def service_server(self, now: float | None = None) -> str | None:
        """Read SlimeVR's replies and repeat the handshake until it answers.

        Returns a line worth logging when the registration state changes.
        """
        moment = time.monotonic() if now is None else now
        note = None
        while True:
            try:
                data = self._socket.recv(2048)
            except (BlockingIOError, InterruptedError):
                break
            except ConnectionResetError:
                # Windows reports an earlier ICMP "port unreachable" here: SlimeVR is not up yet.
                continue
            except OSError:
                break
            self.last_server_rx = moment
            if not self.registered and is_server_hello(data):
                self.registered = True
                note = "accepted by SlimeVR"
        if self.registered and moment - self.last_server_rx > SERVER_SILENCE_S:
            self.registered = False
            note = "SlimeVR stopped answering. Sending the handshake again."
        if self.packets and not self.registered and moment - self.last_hello >= HELLO_RETRY_S:
            self.send_hello(moment)
        return note

    def send_sample(self, sample: ImuSample) -> None:
        self._send(build_rotation(sample.rotation, self._next_id()))
        self._send(build_acceleration(sample.acceleration, self._next_id()))

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
        try:
            self._socket.sendto(payload, self.slime)
        except (BlockingIOError, ConnectionResetError):
            return
        except OSError as exc:
            if not self._send_failing:
                _logger().warning("Cannot reach SlimeVR at %s:%s: %s", self.slime[0], self.slime[1], exc)
            self._send_failing = True
            return
        self._send_failing = False


@dataclass(frozen=True)
class GattChar:
    uuid: str
    handle: int
    properties: frozenset[str]


@dataclass(frozen=True)
class Channels:
    command: str
    imu: str
    reply: str | None
    status_with_response: bool


def looks_like_mocopi(name: str | None) -> bool:
    if not name:
        return False
    folded = name.lower()
    return any(hint in folded for hint in NAME_HINTS)


def choose_channels(chars: list[GattChar]) -> Channels:
    """Find the IMU stream, the command input and the reply channel.

    Windows sometimes hands back a partial table (only the standard services) after a
    failed connection. That is not a sensor to subscribe to, it is a connection to redo.
    """
    by_uuid = {char.uuid.lower(): char for char in chars}
    imu = by_uuid.get(IMU_UUID)
    command = by_uuid.get(COMMAND_UUID)
    if imu is None or command is None:
        raise RuntimeError(
            f"Windows listed {len(chars)} characteristics without the IMU stream. "
            "Reconnecting and reading them from the sensor."
        )
    reply = by_uuid.get(REPLY_UUID)
    has_notify = reply is not None and ({"notify", "indicate"} & reply.properties)
    return Channels(
        command=command.uuid,
        imu=imu.uuid,
        reply=reply.uuid if has_notify else None,
        status_with_response="write-without-response" not in command.properties,
    )


def describe_channels(chars: list[GattChar]) -> str:
    lines = []
    for char in chars:
        props = ",".join(sorted(char.properties)) or "-"
        lines.append(f"handle {char.handle:#06x} {char.uuid} [{props}]")
    return "\n".join(lines)


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
    max_attempts: int = MAX_ATTEMPTS,
) -> bool:
    """Keep one sensor streaming. False once it fails max_attempts times in a row (0: never)."""
    from bleak import BleakClient

    log = _logger()
    session = TrackerSession(address, slime)
    failures = 0
    of_total = f" of {max_attempts}" if max_attempts else ""
    try:
        while not stop.is_set():
            if max_attempts and failures >= max_attempts:
                log.warning(
                    "Gave up on %s after %s failed attempts. Turn it off and on, then run this again.",
                    name,
                    failures,
                )
                return False
            client = None
            try:
                async with connect_lock:
                    if stop.is_set():
                        break
                    log.info("Connecting %s (%s), attempt %s%s", name, address, failures + 1, of_total)
                    # The Windows cache can hold a partial table from a failed connection.
                    client = BleakClient(address, timeout=20.0, winrt={"use_cached_services": False})
                    await client.connect()
                    channels = await _start_stream(client, session, name)
                    log.info("Waiting 2s before the next sensor.")
                    await asyncio.sleep(2)
                await _watch(client, session, channels, name, stop)
                failures = 0 if session.packets else failures + 1
            except Exception as exc:
                failures += 1
                log.warning("%s disconnected: %s", name, str(exc) or type(exc).__name__)
            finally:
                if client is not None and client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            if failures and not stop.is_set():
                wait = _backoff(failures)
                log.info("Retrying %s in %s seconds.", name, wait)
                await asyncio.sleep(wait)
        return True
    finally:
        session.close()


async def _watch(client, session: TrackerSession, channels: Channels, name: str, stop: asyncio.Event) -> None:
    log = _logger()
    next_status = time.monotonic() + STATUS_EVERY_S
    while client.is_connected and not stop.is_set():
        await asyncio.sleep(0.5)
        now = time.monotonic()
        note = session.service_server(now)
        if note:
            log.info("%s %s", name, note)
        if session.imu_packets == 0 and now - session.started > NO_DATA_S:
            log.warning("%s sent no IMU data for %ss. Reconnecting.", name, int(NO_DATA_S))
            return
        if session.imu_packets and now - session.last_imu > QUIET_S:
            log.warning("%s went quiet. Reconnecting.", name)
            return
        if now >= next_status:
            next_status = now + STATUS_EVERY_S
            await _request_status(client, channels, name)
    if not stop.is_set():
        log.warning("%s dropped the Bluetooth link.", name)


async def _request_status(client, channels: Channels, name: str) -> None:
    try:
        await client.write_gatt_char(channels.command, REQUEST_STATUS, response=channels.status_with_response)
    except Exception as exc:
        _logger().info("%s status request skipped: %s", name, exc)


async def _start_stream(client, session: TrackerSession, name: str) -> Channels:
    log = _logger()
    chars = [
        GattChar(char.uuid, int(getattr(char, "handle", 0)), frozenset(item.lower() for item in char.properties))
        for service in client.services
        for char in service.characteristics
    ]
    try:
        channels = choose_channels(chars)
    except RuntimeError:
        log.info("%s GATT table:\n%s", name, describe_channels(chars))
        raise
    mtu = int(getattr(client, "mtu_size", 0) or 0)
    if mtu and mtu < MIN_MTU:
        log.warning("%s link MTU is %s. IMU packets need %s, so they may never arrive.", name, mtu, MIN_MTU)
    session.begin()

    def on_notify(_sender, data: bytearray) -> None:
        first = session.packets == 0
        session.handle_notification(bytes(data))
        if first and session.packets:
            log.info("Streaming %s.", name)

    await client.start_notify(channels.imu, on_notify)
    if channels.reply:
        try:
            await client.start_notify(channels.reply, on_notify)
        except Exception as exc:
            log.info("%s battery replies unavailable: %s", name, exc)
    await client.write_gatt_char(channels.command, START_STREAM, response=True)
    log.info("%s stream started (MTU %s). Keep it still.", name, mtu or "unknown")
    await asyncio.sleep(1.0)
    await _request_status(client, channels, name)
    return channels


async def run_link(config: dict, scan_only: bool, max_attempts: int | None = None) -> None:
    addresses = configured_addresses(config)
    if max_attempts is None:
        max_attempts = int(config.get("max_attempts", MAX_ATTEMPTS))
    if scan_only:
        await scan_addresses()
        return
    if config.get("autodiscovery", True):
        slime = discover_slime()
    else:
        slime = (str(config.get("slime_ip", "127.0.0.1")), int(config.get("slime_port", SLIME_PORT)))
    print(f"SlimeVR at {slime[0]}:{slime[1]}")
    print("Turn off the phone's Bluetooth so it does not take the sensors.")
    named = [(item, item) for item in addresses] or await scan_addresses()
    if not named:
        print("No mocopi sensors found. Turn them on, then run again.")
        return
    stop = asyncio.Event()
    connect_lock = asyncio.Lock()
    log = _logger()
    log.info("Connecting %s sensor(s), one at a time.", len(named))
    # Ctrl+C cancels these tasks. Each one disconnects its sensor in a finally block.
    results = await asyncio.gather(
        *(hold_tracker(address, name, slime, stop, connect_lock, max_attempts) for address, name in named)
    )
    failed = [name for (_address, name), ok in zip(named, results) if not ok]
    if failed:
        log.warning("Stopped trying: %s. Run this again to keep trying.", ", ".join(failed))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send mocopi sensors to SlimeVR over the UB500.")
    parser.add_argument("--config", default="slime_link.json")
    parser.add_argument("--scan", action="store_true", help="List nearby Bluetooth devices and exit.")
    parser.add_argument(
        "--attempts",
        type=int,
        help=f"Failed connections in a row before giving up on a sensor. 0 never gives up. Default {MAX_ATTEMPTS}.",
    )
    args = parser.parse_args(argv)
    config = load_link_config(Path(args.config))
    try:
        asyncio.run(run_link(config, args.scan, args.attempts))
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
