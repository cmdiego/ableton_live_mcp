"""Talk to the 'AbletonMCP Analyzer' Max for Live device over OSC/UDP.

Requests go to the device on 127.0.0.1:9879. Replies (/pong, /stopped, /meter) come back to a
port this process picks and announces with /reply, so several server processes can coexist.
"""

from __future__ import annotations

import collections
import os
import socket
import struct
import threading
import time

DEVICE_PORT = int(os.environ.get("ABLETON_MCP_ANALYZER_PORT", "9879"))
DEVICE_NAME = "AbletonMCP Analyzer"


# -- minimal OSC (strings, int32, float32) ---------------------------------------------------


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (4 - len(b) % 4)


def encode(address: str, *args) -> bytes:
    tags, data = ",", b""
    for a in args:
        if isinstance(a, bool) or isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", int(a))
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode())
    return _pad(address.encode()) + _pad(tags.encode()) + data


def _read_str(b: bytes, i: int) -> tuple[str, int]:
    end = b.index(b"\x00", i)
    return b[i:end].decode(errors="replace"), (end // 4 + 1) * 4


def decode(packet: bytes) -> tuple[str, list]:
    address, i = _read_str(packet, 0)
    if i >= len(packet):
        return address, []
    tags, i = _read_str(packet, i)
    args = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", packet[i:i + 4])[0])
            i += 4
        elif t == "f":
            args.append(struct.unpack(">f", packet[i:i + 4])[0])
            i += 4
        elif t == "d":
            args.append(struct.unpack(">d", packet[i:i + 8])[0])
            i += 8
        elif t == "s":
            s, i = _read_str(packet, i)
            args.append(s)
    return address, args


# -- link -----------------------------------------------------------------------------------------


class AnalyzerLink:
    def __init__(self, device_port: int = DEVICE_PORT) -> None:
        self.device_port = device_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.meter: collections.deque[tuple[float, list[float]]] = collections.deque(maxlen=6000)
        self._events: dict[str, threading.Event] = collections.defaultdict(threading.Event)
        self._last: dict[str, list] = {}
        threading.Thread(target=self._listen, name="analyzer-udp", daemon=True).start()

    def _listen(self) -> None:
        while True:
            try:
                packet, _ = self.sock.recvfrom(65536)
                address, args = decode(packet)
            except (OSError, ValueError, struct.error):
                continue
            if address == "/meter":
                self.meter.append((time.time(), [float(a) for a in args]))
            else:
                self._last[address] = args
                self._events[address].set()

    def send(self, address: str, *args) -> None:
        # Announce our reply port first: the device keeps the last one it heard.
        self.sock.sendto(encode("/reply", self.port), ("127.0.0.1", self.device_port))
        self.sock.sendto(encode(address, *args), ("127.0.0.1", self.device_port))

    def request(self, address: str, reply: str, *args, timeout: float = 2.0) -> list | None:
        """Send and wait for a reply message; None on timeout."""
        event = self._events[reply]
        event.clear()
        self.send(address, *args)
        if not event.wait(timeout):
            return None
        return self._last.get(reply)


_link: AnalyzerLink | None = None


def link() -> AnalyzerLink:
    global _link
    if _link is None:
        _link = AnalyzerLink()
    return _link
