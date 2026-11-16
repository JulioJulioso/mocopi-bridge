# mocopi bridge

Double-click **Start mocopi bridge.bat**. A window opens with the steps and this PC's address. Copy that address into the mocopi phone app.

The first launch downloads one library, so the PC needs an internet connection once. Python 3.11 or newer has to be installed from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH** checked. After that, opening the bridge does not require the terminal.

Personal settings go in `config.json` in this folder. That file is not part of the project. `config.example.json` is the Quest 3 preset: hips, feet, and head position. Chest, knees, and elbows stay off until you enable them there.

Motion leaves the phone unencrypted. Keep the phone and the PC on the same local network.

## Free sensors

`phone/` is a Unity project for the phone. Import Sony's mocopi sensor data reader into `phone/Assets/Sony` (that package is not in this repo) and forward each reading to `SonySensorReader.Forward`. Until that package is imported, press Play to simulate a hip, two ankles, and a box.

Assign each sensor on the phone. Hip and ankles drive VRChat. A named object only rotates. Open `unity/`, press Play, and the box named `box` turns. Place your own model in that scene and give it the same name. Reset on the phone makes the current orientation the zero pose. The sensor does not track position in the room.

To run it from a terminal instead:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
set PYTHONPATH=src
.venv\Scripts\python -m mocopi_bridge --console
```
