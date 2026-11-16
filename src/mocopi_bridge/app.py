"""UDP receive loop, OSC output, and the actions behind the window buttons."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

from mocopi_bridge.body_estimate import stance_world
from mocopi_bridge.bvh_writer import BvhRecorder, scaled_locals
from mocopi_bridge.config import forward_targets, hip_bone_name, resolve_config
from mocopi_bridge.forward import UdpForwarder
from mocopi_bridge.osc_out import OscSender, build_messages
from mocopi_bridge.parser import PacketError, SkeletonDef, parse_packet
from mocopi_bridge.sensors import (
    SensorReset,
    SensorSample,
    encode_prop,
    parse_sensor_datagram,
    relative_rotation,
)
from mocopi_bridge.skeleton import (
    PoseAdjust,
    WorldBone,
    bone_id,
    capture_reset,
    forward_kinematics,
    local_transforms,
    present_world,
)

INSTRUCTIONS = """\
1. Put this PC and the phone on the same Wi-Fi.
2. In the mocopi app, calibrate, then open Motion and switch to SEND.
3. Settings, PC connection: paste the address above, port {listen_port}, format mocopi (UDP).
   Do not choose VRChat (OSC). That format does not include the full skeleton.
4. Tap the green send button. If Windows asks, allow this app on the network.

Quest 3: open VRChat from Steam on this PC (Link, Air Link, Steam Link, or Virtual Desktop).
Turn OSC on, then Calibrate FBT. The headset and controllers keep the head and hands.
This app sends the hips and feet, plus head position so the body stays under the headset.

Unity listens on {unity_port}. Blender listens on {blender_port}.
Use Sony's official receiver plugins and set those ports.

Free sensors: use the phone project in phone/ instead of Sony's body app.
Assign each sensor to hip, left ankle, right ankle, or a named object.
Objects are rotation only. Place the model in the Unity scene, then Reset on the phone
to make the current orientation the zero pose. Unity listens for objects on port {prop_port}.

If the body drifts:
- Reset position puts you back at the origin.
- Lock hip holds the hips still while you sit. The feet keep moving.
- Mirror swaps left and right.
- On the phone, use Reset pose. If that is not enough, Recalibrate:
  stand still, take one step forward when the phone asks, stand still again,
  and do not look at the phone.
- In VRChat, Calibrate FBT again. If you are only facing backwards, use Auto-center OSC Trackers.

Record BVH saves a take on this PC without stopping VRChat, Unity, or Blender.
Motion is sent unencrypted. Use it on your local network only.
"""


class Session:
    """Bridge state, kept separate from the socket so tests can drive it."""

    def __init__(self, config: dict, forwarder: UdpForwarder | None = None, recorder: BvhRecorder | None = None):
        self.config = config
        self.forwarder = forwarder
        self.recorder = recorder or BvhRecorder(config["bvh"]["directory"])
        self.definition: SkeletonDef | None = None
        self.frame_bones = None
        self.paused = False
        self.mirror = False
        self.reset_xz_yaw: tuple[float, float, float] | None = None
        self.locked_hip: tuple[float, float, float] | None = None
        self._definition_signature = None
        self.last_rx: float | None = None
        self.ever_received = False
        self._stream_lost = False
        self.frames_in_window = 0
        self.window_started = time.monotonic()
        self.fps = 0.0
        self.free_sensors: dict[str, SensorSample] = {}
        self._prop_reference: dict[str, tuple[float, float, float, float]] = {}
        self._prop_packets: list[bytes] = []

    def feed(self, data: bytes, now: float | None = None) -> str | None:
        sensor = parse_sensor_datagram(data)
        moment = time.monotonic() if now is None else now
        self.last_rx = moment
        self.ever_received = True
        recovered = None
        if self._stream_lost:
            self._stream_lost = False
            recovered = "UDP is back."
        if sensor is not None:
            note = self._ingest_sensor(sensor)
            self._roll_fps(moment)
            return note or recovered
        if self.forwarder is not None:
            self.forwarder.send(data)
        try:
            packet = parse_packet(data)
        except PacketError as exc:
            return f"Ignored packet: {exc}"
        if packet.skeleton is not None:
            self._note_definition(packet.skeleton)
        if packet.frame is not None:
            self.frame_bones = packet.frame.bones
            self.frames_in_window += 1
            if self.recorder.active and self.definition is not None:
                self.recorder.add(scaled_locals(self.current_locals(), float(self.config["scale"])))
        self._roll_fps(moment)
        return recovered

    def _ingest_sensor(self, event: SensorSample | SensorReset) -> str | None:
        if isinstance(event, SensorReset):
            return self._reset_props(event)
        self.free_sensors[event.key] = event
        self.frames_in_window += 1
        if event.role == "prop" and event.name:
            reference = self._prop_reference.get(event.name, (0.0, 0.0, 0.0, 1.0))
            turned = relative_rotation(event.rotation, reference)
            self._prop_packets = [encode_prop(event.name, turned)]
            return None
        self._prop_packets = []
        return None

    def _reset_props(self, event: SensorReset) -> str:
        names = [event.name] if event.name else [
            sample.name for sample in self.free_sensors.values() if sample.role == "prop" and sample.name
        ]
        saved: list[str] = []
        packets: list[bytes] = []
        for name in names:
            sample = self.free_sensors.get(f"prop:{name}")
            if sample is None:
                continue
            self._prop_reference[name] = sample.rotation
            packets.append(encode_prop(name, (0.0, 0.0, 0.0, 1.0)))
            saved.append(name)
        self._prop_packets = packets
        if not saved:
            return "No object to reset yet."
        return f"Object zero saved for {', '.join(saved)}."

    def prop_datagrams(self) -> list[bytes]:
        packets = self._prop_packets
        self._prop_packets = []
        return packets

    def current_locals(self):
        if self.definition is None:
            return {}
        return local_transforms(self.definition, self.frame_bones)

    def presented(self) -> dict[int, WorldBone]:
        if self.definition is not None:
            world = forward_kinematics(self.definition, self.current_locals())
        else:
            world = stance_world(self._body_samples(), float(self.config.get("body_height_m", 1.7)))
        if not world:
            return {}
        return present_world(
            world,
            self.config["axes"],
            float(self.config["scale"]) if self.definition is not None else 1.0,
            PoseAdjust(self.mirror, self.reset_xz_yaw, self.locked_hip),
            hip_bone_id=bone_id(hip_bone_name(self.config)) if self.definition is not None else 0,
        )

    def osc_messages(self) -> list[tuple[str, list[float]]]:
        if self.paused:
            return []
        if self.definition is None and not self._body_samples():
            return []
        return build_messages(self.presented(), self.config["trackers"])

    def _body_samples(self) -> dict[str, SensorSample]:
        return {
            key: sample
            for key, sample in self.free_sensors.items()
            if sample.role in ("hip", "left_ankle", "right_ankle")
        }

    def handle_key(self, key: str) -> str | None:
        if key == "p":
            self.paused = not self.paused
            return "OSC paused." if self.paused else "OSC live."
        if key == "m":
            self.mirror = not self.mirror
            self.reset_xz_yaw = None
            self.locked_hip = None
            return "Mirror on." if self.mirror else "Mirror off."
        if key == "r":
            hip = self._hip()
            if hip is None or self.definition is None:
                return "Nothing to reset yet."
            unreset = present_world(
                forward_kinematics(self.definition, self.current_locals()),
                self.config["axes"],
                float(self.config["scale"]),
                PoseAdjust(self.mirror, None, None),
                hip_bone_id=bone_id(hip_bone_name(self.config)),
            )
            source = unreset.get(bone_id(hip_bone_name(self.config)))
            if source is None:
                return "No hip to reset yet."
            self.reset_xz_yaw = capture_reset(source)
            self.locked_hip = None
            return "Position reset."
        if key == "h":
            if self.locked_hip is not None:
                self.locked_hip = None
                return "Hip unlocked."
            hip = self._hip()
            if hip is None:
                return "No hip to lock yet."
            self.locked_hip = hip.pos
            return "Hip locked."
        if key == "b":
            return self._toggle_recording()
        return None

    def take_stream_warning(self, now: float) -> str | None:
        timeout = float(self.config["stream_timeout_s"])
        stale = (
            self.ever_received
            and self.last_rx is not None
            and (now - self.last_rx) > timeout
        )
        if stale and not self._stream_lost:
            self._stream_lost = True
            return (
                "No UDP data. On the phone: SEND, format mocopi (UDP), "
                "this PC address, and the listen port. Check the firewall."
            )
        return None

    def status_line(self) -> str:
        bones = len(self.definition.bones) if self.definition else 0
        osc = "paused" if self.paused else "live"
        hip = "locked" if self.locked_hip is not None else "free"
        mirror = "on" if self.mirror else "off"
        recording = "recording" if self.recorder.active else "off"
        link = "waiting" if not self.ever_received or self._stream_lost else "receiving"
        roles = ", ".join(sorted(self.free_sensors)) if self.free_sensors else "none"
        return (
            f"{link}   fps={self.fps:5.1f}   bones={bones:2d}   osc={osc}   "
            f"hip={hip}   mirror={mirror}   bvh={recording}   sensors={roles}"
        )

    def _hip(self) -> WorldBone | None:
        presented = self.presented()
        return presented.get(bone_id(hip_bone_name(self.config)))

    def _note_definition(self, definition: SkeletonDef) -> None:
        signature = definition.signature()
        if self._definition_signature is not None and signature != self._definition_signature:
            self.reset_xz_yaw = None
            self.locked_hip = None
        self._definition_signature = signature
        self.definition = definition

    def _toggle_recording(self) -> str:
        if self.recorder.active:
            path = self.recorder.stop()
            return f"BVH saved to {path}." if path else "Nothing was recorded."
        if self.definition is None:
            return "No skeleton to record yet."
        path = self.recorder.start(self.definition)
        self.recorder.add(scaled_locals(self.current_locals(), float(self.config["scale"])))
        return f"Recording BVH to {path}."

    def _roll_fps(self, now: float) -> None:
        elapsed = now - self.window_started
        if elapsed >= 1.0:
            self.fps = self.frames_in_window / elapsed
            self.frames_in_window = 0
            self.window_started = now


class BridgeService:
    """Owns the UDP socket so the window can stay responsive."""

    def __init__(self, config: dict):
        self.config = config
        self.session = Session(config, forwarder=UdpForwarder(forward_targets(config)))
        self.lock = threading.Lock()
        self.notes: deque[str] = deque(maxlen=12)
        self.bind_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._osc = OscSender(config["osc"]["host"], int(config["osc"]["port"]))
        self._props = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._prop_target = _prop_target(config)
        self._autostart_pending = bool(config["bvh"].get("autostart"))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="mocopi-udp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        with self.lock:
            if self.session.recorder.active:
                self.session.recorder.stop()
        if self.session.forwarder is not None:
            self.session.forwarder.close()
        self._props.close()

    def press(self, key: str) -> str | None:
        with self.lock:
            note = self.session.handle_key(key)
            if note:
                self.notes.append(note)
        return note

    def drain_notes(self) -> list[str]:
        with self.lock:
            found = list(self.notes)
            self.notes.clear()
            return found

    def status_line(self) -> str:
        with self.lock:
            return self.session.status_line()

    def _loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.bind((self.config["listen_host"], int(self.config["listen_port"])))
        except OSError as exc:
            self.bind_error = (
                f"Cannot listen on port {self.config['listen_port']}. "
                f"Close the other copy of this app. ({exc})"
            )
            with self.lock:
                self.notes.append(self.bind_error)
            return
        sock.settimeout(0.2)
        try:
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(65535)
                except TimeoutError:
                    data = None
                except OSError:
                    break
                now = time.monotonic()
                messages: list[tuple[str, list[float]]] = []
                note = None
                warning = None
                props: list[bytes] = []
                with self.lock:
                    if data:
                        note = self.session.feed(data, now)
                        self._maybe_autostart()
                        messages = self.session.osc_messages()
                        props = self.session.prop_datagrams()
                    warning = self.session.take_stream_warning(now)
                if note or warning:
                    with self.lock:
                        if note:
                            self.notes.append(note)
                        if warning:
                            self.notes.append(warning)
                if messages:
                    self._osc.send(messages)
                for blob in props:
                    self._props.sendto(blob, self._prop_target)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _maybe_autostart(self) -> None:
        session = self.session
        if not self._autostart_pending or session.definition is None or session.recorder.active:
            return
        path = session.recorder.start(session.definition)
        session.recorder.add(scaled_locals(session.current_locals(), float(self.config["scale"])))
        self._autostart_pending = False
        self.notes.append(f"Recording BVH to {path}.")


def local_ipv4s() -> list[str]:
    """IPv4 addresses of this PC. The routed one is first. No packet is sent."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    routed = None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        routed = probe.getsockname()[0]
        found.add(routed)
    except OSError:
        pass
    finally:
        probe.close()
    found.discard("127.0.0.1")
    if not found:
        return ["127.0.0.1"]
    ordered = sorted(found)
    if routed in ordered:
        ordered.remove(routed)
        ordered.insert(0, routed)
    return ordered


def format_banner(config: dict) -> str:
    forwards = ", ".join(f"{host}:{port}" for host, port in forward_targets(config))
    addresses = ", ".join(local_ipv4s())
    osc = config["osc"]
    unity_port, blender_port = _plugin_ports(config)
    help_text = INSTRUCTIONS.format(
        listen_port=config["listen_port"],
        unity_port=unity_port,
        blender_port=blender_port,
        prop_port=_prop_target(config)[1],
    )
    return (
        "mocopi bridge\n"
        f"Listening on UDP {config['listen_host']}:{config['listen_port']}\n"
        f"This PC address: {addresses}\n"
        f"Raw forward: {forwards}\n"
        f"VRChat OSC: {osc['host']}:{osc['port']}\n\n"
        f"{help_text}\n"
        "Keys: p pause OSC | r reset position | h lock hip | m mirror | b record BVH | q quit\n"
    )


def _prop_target(config: dict) -> tuple[str, int]:
    props = config.get("props") or {}
    return str(props.get("host", "127.0.0.1")), int(props.get("port", 12354))


def _plugin_ports(config: dict) -> tuple[int, int]:
    targets = forward_targets(config)
    unity = targets[0][1] if targets else 12352
    blender = targets[1][1] if len(targets) > 1 else 12353
    return unity, blender


def poll_key() -> str | None:
    if sys.platform != "win32":
        return None
    import msvcrt

    if not msvcrt.kbhit():
        return None
    raw = msvcrt.getwch()
    if not raw:
        return None
    return raw.lower()


def run(config: dict) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(format_banner(config), flush=True)
    service = BridgeService(config)
    service.start()
    try:
        while True:
            key = poll_key()
            if key == "q":
                print("\nBridge closed.", flush=True)
                return
            if key:
                note = service.press(key)
                if note:
                    print(f"\n{note}", flush=True)
            if service.bind_error:
                print(f"\n{service.bind_error}", flush=True)
                return
            time.sleep(0.05)
            print("\r" + service.status_line(), end="", flush=True)
    finally:
        service.stop()


def _report_startup_error(exc: BaseException) -> None:
    log_path = Path.cwd() / "bridge.log"
    log_path.write_text(traceback.format_exc(), encoding="utf-8")
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("mocopi bridge", f"{exc}\n\nDetails were saved to bridge.log.")
        root.destroy()
    except Exception:
        print(exc, file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Send mocopi motion from a phone to VRChat, Unity, and Blender.")
    parser.add_argument("--config", help="JSON file with ports, trackers, scale, and axes.")
    parser.add_argument("--console", action="store_true", help="Run in the terminal instead of the window.")
    args = parser.parse_args(argv)
    try:
        config = resolve_config(args.config)
        if args.console:
            run(config)
            return
        from mocopi_bridge.gui import run_gui

        run_gui(config)
    except Exception as exc:
        _report_startup_error(exc)
        raise SystemExit(1) from exc
