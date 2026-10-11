"""Phase 11: OSC codec, the generated .amxd, and the analyzer tool against a fake device over UDP."""

import json
import socket
import struct
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp import m4l
from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import live_audio

from .fake_live import Device

BUILD = Path(__file__).resolve().parents[1] / "m4l" / "build_analyzer.py"


def test_osc_roundtrip():
    packet = m4l.encode("/meter", 0.5, 2, "x y")
    assert len(packet) % 4 == 0
    address, args = m4l.decode(packet)
    assert address == "/meter" and args[0] == pytest.approx(0.5) and args[1:] == [2, "x y"]
    assert m4l.decode(m4l.encode("/stopped")) == ("/stopped", [])


@pytest.mark.skipif(not __import__("os").path.exists(
    "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Misc/Max Devices/Max Audio Effect.amxd"),
    reason="needs Live Suite's Max template")
def test_generated_device_is_well_formed():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_analyzer", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raw = mod.build()
    assert raw[:4] == b"ampf" and raw[8:12] == b"aaaa" and raw[12:16] == b"meta"
    n = struct.unpack("<I", raw[28:32])[0]
    assert raw[24:28] == b"ptch" and len(raw) == 32 + n and raw.endswith(b"\n\x00")
    patcher = json.loads(raw[32:].rstrip(b"\x00\n"))["patcher"]
    boxes = {b["box"]["id"]: b["box"] for b in patcher["boxes"]}
    texts = " | ".join(b.get("text", "") for b in boxes.values())
    for needed in ("plugin~", "plugout~", "sfrecord~ 2", "udpreceive 9879", "udpsend 127.0.0.1 9878",
                   "route /open /start /stop /meter /ping /reply", "samptype float32"):
        assert needed in texts
    for line in patcher["lines"]:
        src, out = line["patchline"]["source"]
        dst, inlet = line["patchline"]["destination"]
        assert src in boxes and dst in boxes, line
        assert out < boxes[src]["numoutlets"] and inlet < boxes[dst]["numinlets"], line


class FakeDevice:
    """Speaks the analyzer's OSC protocol on a random UDP port."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply_port, self.path, self.metering = None, None, False
        threading.Thread(target=self.run, daemon=True).start()

    def reply(self, address, *args):
        self.sock.sendto(m4l.encode(address, *args), ("127.0.0.1", self.reply_port))

    def run(self):
        while True:
            address, args = m4l.decode(self.sock.recvfrom(4096)[0])
            if address == "/reply":
                self.reply_port = args[0]
            elif address == "/ping":
                self.reply("/pong", 48000.0)
            elif address == "/open":
                self.path = args[0]
            elif address == "/stop":
                t = np.arange(48000) / 48000
                tone = 0.1 * np.sin(2 * np.pi * 997 * t)
                sf.write(self.path, np.stack([tone, tone], 1).astype(np.float32), 48000, subtype="FLOAT")
                self.reply("/stopped")
            elif address == "/meter":
                self.metering = bool(args[0])
                if self.metering:
                    threading.Thread(target=self.meter, daemon=True).start()

    def meter(self):
        while self.metering:
            self.reply("/meter", 0.1, 0.1, 0.0707, 0.0707)
            time.sleep(0.05)


@pytest.fixture
def device(client, song, monkeypatch, tmp_path):
    monkeypatch.setenv("ABLETON_MCP_AGENT_DIR", str(tmp_path / "agent"))
    fake = FakeDevice()
    monkeypatch.setattr(m4l, "_link", m4l.AnalyzerLink(device_port=fake.port))
    song.master_track.devices.append(Device(m4l.DEVICE_NAME, "MxDeviceAudioEffect"))
    return fake


def test_status_and_capture(device, song):
    st = live_audio.analyzer("status")
    assert st["installed"] and st["responding"] and st["sample_rate"] == 48000.0
    r = live_audio.analyzer("capture", seconds=0.5, start_bar=3, name="my take")
    assert r["capture"].startswith("captures/my_take_") and " " not in r["capture"]
    assert r["loudness"]["integrated_lufs"] == pytest.approx(-20.0, abs=0.05)
    assert song.start_time == 0.0 and not song.is_playing  # play marker restored, playback stopped


def test_meter(device):
    m = live_audio.analyzer("meter", seconds=0.6)
    assert m["readings"] >= 5 and m["peak_dbfs"] == [-20.0, -20.0] and m["rms_dbfs_median"] == [-23.0, -23.0]


def test_warns_when_not_last_and_requires_install(device, song):
    song.master_track.devices.append(Device("Limiter", "Limiter"))
    assert "isn't the last device" in live_audio.analyzer("status")["warning"]
    song.master_track.devices.clear()
    assert live_audio.analyzer("status")["installed"] is False
    with pytest.raises(LiveError, match="install"):
        live_audio.analyzer("capture", seconds=1)


def test_master_chain_goes_before_the_analyzer(device, song):
    from ableton_mcp.tools.mixing import find_or_create_device
    from ableton_mcp.live_client import get_client
    find_or_create_device(get_client(), "song.master_track", "Limiter", "MCP Limiter")
    assert [d.name for d in song.master_track.devices] == ["MCP Limiter", m4l.DEVICE_NAME]
