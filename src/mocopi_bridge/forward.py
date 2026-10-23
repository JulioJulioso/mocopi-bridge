"""Forward the original datagram unchanged to Unity and Blender."""

from __future__ import annotations

import socket


class UdpForwarder:
    def __init__(self, targets: list[tuple[str, int]]):
        self.targets = targets
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, payload: bytes) -> None:
        for host, port in self.targets:
            self._socket.sendto(payload, (host, port))

    def close(self) -> None:
        self._socket.close()
