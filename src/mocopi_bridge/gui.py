"""Window with the setup steps, this PC's address, and the live controls."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from mocopi_bridge.app import INSTRUCTIONS, BridgeService, _plugin_ports, local_ipv4s


def run_gui(config: dict) -> None:
    app = BridgeWindow(config)
    app.mainloop()


class BridgeWindow(tk.Tk):
    def __init__(self, config: dict):
        super().__init__()
        self.title("mocopi bridge")
        self.geometry("760x640")
        self.minsize(640, 520)
        self._config = config
        self._service = BridgeService(config)
        self._closed = False

        self._build()
        self._service.start()
        self.after(200, self._refresh)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", **pad)
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="This PC address").grid(row=0, column=0, sticky="w")
        self._address = tk.StringVar(value=", ".join(local_ipv4s()))
        entry = ttk.Entry(header, textvariable=self._address, state="readonly")
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(header, text="Copy", command=self._copy_address).grid(row=0, column=2)

        unity_port, blender_port = _plugin_ports(self._config)
        steps = INSTRUCTIONS.format(
            listen_port=self._config["listen_port"],
            unity_port=unity_port,
            blender_port=blender_port,
        )
        text = tk.Text(self, wrap="word", height=18, relief="flat", padx=8, pady=8)
        text.insert("1.0", steps.strip() + "\n")
        text.configure(state="disabled")
        text.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 6))

        buttons = ttk.Frame(self)
        buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=6)
        for label, key in (
            ("Pause OSC", "p"),
            ("Reset position", "r"),
            ("Lock hip", "h"),
            ("Mirror", "m"),
            ("Record BVH", "b"),
        ):
            ttk.Button(buttons, text=label, command=lambda key=key: self._press(key)).pack(
                side="left", padx=(0, 6)
            )
        ttk.Button(buttons, text="Quit", command=self._close).pack(side="right")

        self._status = tk.StringVar(value="Starting")
        ttk.Label(self, textvariable=self._status).grid(row=4, column=0, sticky="w", padx=12)
        self._log = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._log, wraplength=720).grid(
            row=5, column=0, sticky="ew", padx=12, pady=(0, 12)
        )

    def _copy_address(self) -> None:
        address = self._address.get().split(",")[0].strip()
        self.clipboard_clear()
        self.clipboard_append(address)
        self._log.set(f"Copied {address}. Paste it into the mocopi app.")

    def _press(self, key: str) -> None:
        note = self._service.press(key)
        if note:
            self._log.set(note)

    def _refresh(self) -> None:
        if self._closed:
            return
        self._status.set(self._service.status_line())
        notes = self._service.drain_notes()
        if notes:
            self._log.set(notes[-1])
        if self._service.bind_error:
            self._log.set(self._service.bind_error)
        self.after(200, self._refresh)

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._service.stop()
        self.destroy()
