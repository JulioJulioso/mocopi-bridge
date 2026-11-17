# mocopi bridge

Use mocopi sensors on Windows with [SlimeVR](https://slimevr.dev/). The PC talks to the sensors over Bluetooth. SlimeVR builds the body and can send it to VRChat, Unity, and Blender.

You do not keep a phone in the loop. Sony's phone app is only for a one-time firmware update. After that, turn the phone's Bluetooth off. If the phone stays connected, it takes the sensors away from the PC.

The Windows Bluetooth link follows the tracker packet layout published by [MoSlime](https://github.com/moslime/moslime) (MIT). That project is the Linux original, and its old Windows download is gone. This repository does not include MoSlime's source.

## What you need

- Windows 10 or 11
- Python 3.11 or newer from [python.org](https://www.python.org/downloads/), with **Add python.exe to PATH** checked
- [SlimeVR Server](https://slimevr.dev/) for Windows, installed with the SteamVR driver
- A TP-Link UB500, or another Bluetooth adapter that can hold several BLE connections
- The six mocopi sensors, charged

A Quest 3 is optional. SlimeVR keeps the headset and controllers as the head and hands. The mocopi sensors cover the body parts you assign.

## One-time setup

### Bluetooth adapter

1. Plug the UB500 into a rear USB 2.0 port, using its short cable. Keep it away from the router and from blue USB 3 ports. USB 3 noise knocks sensors offline.
2. Open Device Manager and disable every other Bluetooth adapter, including the one on the motherboard. Windows must use only the UB500.
3. Install the TP-Link driver if Windows does not install it, then restart the PC.

Wi-Fi on 2.4 GHz shares the band with Bluetooth. The link still works. If a sensor drops when you walk past the router, move the UB500 farther from it. Put the Quest on 5 GHz when the router has that band. The video stream suffers on 2.4 GHz more than the sensors do.

### Sensor firmware

1. Install Sony's mocopi app on a phone and update every sensor to the latest firmware.
2. Close that app.
3. Turn the phone's Bluetooth off and leave it off while you track.

Do not flash firmware by hand with the Renesas SUOTA app. Changing its default parameters can destroy a sensor.

### Mocopi-bridge (this program)

Mocopi-bridge is this repository. It is the program that connects the sensors to the PC and sends them to SlimeVR. It is not Sony's app and it is not SlimeVR itself.

**Download it**

1. Open [github.com/JulioJulioso/mocopi-bridge](https://github.com/JulioJulioso/mocopi-bridge).
2. Click the green **Code** button, then **Download ZIP**.
3. Extract the zip. You get a folder named `mocopi-bridge-main`. Put it somewhere you can find again, such as `Documents`.

If you already use Git, this is the same thing:

```bash
git clone https://github.com/JulioJulioso/mocopi-bridge.git
```

**Open Command Prompt in that folder**

1. Open the extracted folder in File Explorer. You should see `README.md` and a folder named `src`.
2. Click the address bar at the top, type `cmd`, and press Enter.
3. A black Command Prompt window opens, already inside this folder. The commands below are typed there, one at a time. Press Enter after each one.

```bash
pip install -r requirements.txt
set PYTHONPATH=src
python -m mocopi_bridge.slime_link
```

The first `slime_link` run creates `slime_link.json` and exits. That file stays on your PC. It is not uploaded to GitHub. Run the last command again to connect the sensors.

`set PYTHONPATH=src` lasts only while that Command Prompt window is open. Type it again each time you open a new window, before `python -m mocopi_bridge.slime_link`.

## Connect the sensors to the PC

There is no Windows "Add Bluetooth device" step. The sensors do not pair like headphones. This program connects to them while it is running, then drops the links when you close it.

1. Sensors out of the case, each one on. A short press turns a sensor on. The light blinks. Hold the button until the light stays green to turn it off.
2. Phone Bluetooth off. Sony's app closed.
3. SlimeVR Server already open.
4. In this folder:

```bash
set PYTHONPATH=src
python -m mocopi_bridge.slime_link --scan
```

The scan prints every nearby device for about eight seconds. A mocopi sensor is marked `<-- mocopi`. You want six of those.

5. Connect them:

```bash
python -m mocopi_bridge.slime_link
```

The console prints `Connecting`, then `Streaming`, once per sensor. `Streaming` appears only after motion data arrives, and SlimeVR lists a sensor only from that moment. Leave the sensors still on a table until all six say `Streaming`. Starting the stream zeroes each sensor's axes. Moving them during that second makes the first pose crooked.

A sensor that fails 8 connections in a row is dropped with `Gave up on ...`, and the others keep going. Run the command again to keep trying, or change the limit with `--attempts 20` (`--attempts 0` never gives up).

6. SlimeVR lists the trackers and a battery level. Shake one sensor. Its row is the one that moves, which is how you tell the physical sensor from the others.
7. Assign body parts in SlimeVR. The colored cap does not choose the part. With a Quest, leave the head and hands to the headset and controllers. A practical set is chest, hips, both thighs, and both ankles.
8. Stay still, look forward, and run SlimeVR's mounting and body-proportion steps. Mounting is the direction the sensor sits on the strap. A backwards mount looks like a bent limb even when Bluetooth is fine.
9. Turn on SlimeVR's SteamVR trackers. In SteamVR, open Settings, Startup / Shutdown, Manage Add-ons, and enable the SlimeVR add-on.

If the scan lists the six addresses and you want to skip the search next time, paste them into `addresses` in `slime_link.json`.

## Every session

1. Start SlimeVR Server.
2. Turn the six sensors on.
3. Run `python -m mocopi_bridge.slime_link` and wait for six `Streaming` lines.
4. In SlimeVR, stand still, look forward, and press Reset before you play. Then calibrate full-body in VRChat if you use it.

Closing the console disconnects the sensors. Start the command again next time. You do not re-enter the addresses.

## FAQ

**The scan shows no mocopi sensors.** Turn each sensor off and on. Confirm the phone's Bluetooth is off. In Device Manager, confirm the only enabled Bluetooth adapter is the UB500. Unplug the UB500, plug it into another rear USB 2.0 port, and scan again.

**The console says `Could not start notify` or `Unreachable`, or Device Manager fills with Bluetooth LE Generic Attribute Service.** Quit the program. Those extra rows are leftovers from failed connections, not extra adapters. Uninstall only the Generic Attribute Service entries, and leave the TP-Link adapter. Turn every sensor off. Turn the UB500 off and on in Windows Bluetooth settings. Turn the sensors on again and run the link once. It now connects one sensor at a time and writes the same messages to `slime_link.log`.

**One sensor streams and the others never do.** A full set should connect together. If one address never succeeds, factory-reset only that sensor (see the last question) and scan again.

**SlimeVR never lists the trackers.** The program repeats the handshake every second until SlimeVR answers, then prints `accepted by SlimeVR` once per sensor. Opening SlimeVR after this program is fine. If that line never appears, SlimeVR is not listening at the address printed on the first line (`SlimeVR at ...`). Open SlimeVR, allow it through the Windows firewall, and if it runs on another PC set `autodiscovery` to `false` and put that PC's address in `slime_ip`.

**SlimeVR shows a tracker as disconnected or timed out.** This program already drops a sensor that sends nothing for five seconds and connects it again. The console prints `went quiet. Reconnecting.` If the row stays dead, turn that one sensor off and on. Do not delete it in SlimeVR. When the stream returns, the same tracker comes back. If you removed it in SlimeVR, restart this program so the handshake is sent again, then assign the body part once more.

**The body is crooked, twisted, or facing the wrong way, but every tracker is still connected.** This is not a Bluetooth failure. Do these in order:

1. Stand straight, look forward, arms down, and press Reset in SlimeVR. That zeroes yaw.
2. If a limb is still bent the wrong way, the sensor is mounted backwards or on the wrong side. Redo that tracker's mounting orientation in SlimeVR. You do not re-pair it.
3. If the whole skeleton is a wrong shape after a new strap layout, run SlimeVR's body proportions again.

Reset and mounting do not forget the sensor. Factory reset does.

**One sensor will not connect after all of the above.** Factory-reset only that sensor: press its button quickly about 10 to 15 times, until the light blinks red and blue. Turn it on, run `--scan`, and connect again. Leave the other five alone.

**All six drop when you walk near the router.** The 2.4 GHz Wi-Fi is crowding Bluetooth. Move the UB500 away from the router. Only the radio was noisy.

**Command Prompt says python is not recognized.** Python was installed without **Add python.exe to PATH**. Install it again from [python.org](https://www.python.org/downloads/) and check that box, then close Command Prompt and open it again.

**The commands do nothing or say the path cannot be found.** Command Prompt is not inside the extracted folder. The address bar must show the folder that contains `src` before you type `cmd`.

## SlimeVR to VRChat, Unity, and Blender

SlimeVR is the live output. This Bluetooth program only feeds it.

- **VRChat on Steam.** Use SlimeVR's SteamVR trackers. Do not also enable SlimeVR's VRChat OSC at the same time.
- **Unity, live.** For a VRM avatar, enable VMC in SlimeVR (Settings, OSC, VMC), send to `127.0.0.1`, and note the outgoing port (often 39540). In Unity, put EVMC4U on the avatar and use that same port. For a VR scene that should see the Quest as a headset, skip VMC and read the SteamVR trackers with the SteamVR or OpenXR plugin.
- **Blender, live.** Install the VMC4B add-on. Set its address to `127.0.0.1` and the same port SlimeVR is sending, then press Connect.
- **Blender, recorded.** Record a BVH inside SlimeVR and import that file. That take is not live.

## Config file

`slime_link.json`:

```json
{
  "addresses": [],
  "slime_ip": "127.0.0.1",
  "slime_port": 6969,
  "autodiscovery": true,
  "max_attempts": 8
}
```

Leave `addresses` empty to connect every nearby mocopi sensor. If a neighbor's device is picked up, paste only your six addresses into that list. With `autodiscovery` on, the program looks for SlimeVR and otherwise uses `127.0.0.1:6969`. `max_attempts` is how many failed connections in a row a sensor gets before the program stops trying it. `0` means it keeps trying.

## Other paths

These are optional. They are not required for SlimeVR.

**Phone Wi-Fi bridge.** `Start mocopi bridge.bat` opens a window that receives the skeleton from Sony's phone app and forwards it to VRChat, Unity, and Blender. That path needs the phone on the same network for the whole session. Prefer the Bluetooth link above.

**Free placement.** `phone/` is a Unity project that assigns each sensor to a hip, an ankle, or a named object. `unity/` turns a scene object named `box` and does not move it through the room. The sensors measure rotation, not room position. Sony's sensor data reader, if you use it, is imported locally into `phone/Assets/Sony` and is not in this repository.
