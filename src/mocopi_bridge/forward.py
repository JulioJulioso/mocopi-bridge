"""Forward the original datagram unchanged to Unity and Blender."""

from __future__ import annotations

import socket


class UdpForwarder:
    def __init__(self, targets: list[tuple[str, int]]):
        self.targets = targets
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, payload: bytes) -> None:
        """One target that cannot be reached must not stop the others."""
        for target in self.targets:
            try:
                self._socket.sendto(payload, target)
            except OSError:
                pass

    def close(self) -> None:
        self._socket.close()
