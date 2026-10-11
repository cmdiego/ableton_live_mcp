"""Build 'AbletonMCP Analyzer.amxd' (a Max for Live audio effect) from Ableton's own template.

    uv run python m4l/build_analyzer.py

The device passes audio through unchanged and adds:
- capture: OSC on UDP 9879  /open <path>  /start  /stop  -> sfrecord~ (32-bit float WAV); replies /stopped
- meter:   /meter 1|0 -> sends /meter peakL peakR rmsL rmsR (linear) about every 100 ms
- ping:    /ping -> replies /pong <sample rate>
- reply:   /reply <port> -> send replies to that localhost port (default 9878)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

TEMPLATE = Path("/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Misc/Max Devices/Max Audio Effect.amxd")
OUT = Path(__file__).with_name("AbletonMCP Analyzer.amxd")
CONTROL_PORT, REPLY_PORT = 9879, 9878


def read_template() -> tuple[bytes, dict]:
    raw = TEMPLATE.read_bytes()
    assert raw[:4] == b"ampf" and raw[8:12] == b"aaaa", "unexpected .amxd header"
    i, chunks = 12, {}
    while i < len(raw):
        tag, n = raw[i:i + 4], struct.unpack("<I", raw[i + 4:i + 8])[0]
        chunks[tag] = raw[i + 8:i + 8 + n]
        i += 8 + n
    return chunks[b"meta"], json.loads(chunks[b"ptch"].rstrip(b"\x00\n"))


def build() -> bytes:
    meta, doc = read_template()
    p = doc["patcher"]
    boxes, lines = [], []
    y = [20.0]

    def obj(oid: str, text: str, ins: int, outs: int, outtypes: list[str] | None = None, x: float = 20.0,
            cls: str = "newobj") -> None:
        box = {"id": oid, "maxclass": cls, "text": text, "numinlets": ins, "numoutlets": outs,
               "patching_rect": [x, y[0], max(60.0, 7.0 * len(text)), 20.0], "fontname": "Arial Bold", "fontsize": 10.0}
        if outs:
            box["outlettype"] = outtypes or [""] * outs
        boxes.append({"box": box})
        y[0] += 30.0

    def link(src: str, out: int, dst: str, inlet: int) -> None:
        lines.append({"patchline": {"source": [src, out], "destination": [dst, inlet]}})

    sig2 = ["signal", "signal"]
    # Audio path (unchanged pass-through)
    obj("in", "plugin~", 2, 2, sig2)
    obj("out", "plugout~", 2, 2, sig2)
    link("in", 0, "out", 0)
    link("in", 1, "out", 1)
    # Capture
    obj("rec", "sfrecord~ 2", 2, 1, ["signal"], x=260)
    link("in", 0, "rec", 0)
    link("in", 1, "rec", 1)
    obj("udpin", f"udpreceive {CONTROL_PORT}", 1, 1, [""], x=260)
    obj("route", "route /open /start /stop /meter /ping /reply", 1, 7, [""] * 7, x=260)
    link("udpin", 0, "route", 0)
    # sfrecord~ fixes the sample format when the file is opened: set float32 first (trigger fires right to left).
    obj("opentrig", "t l b", 1, 2, ["", "bang"], x=260)
    link("route", 0, "opentrig", 0)
    obj("samptype", "samptype float32", 2, 1, [""], x=420, cls="message")
    link("opentrig", 1, "samptype", 0)
    link("samptype", 0, "rec", 0)
    obj("openmsg", "prepend open", 1, 1, [""], x=260)
    link("opentrig", 0, "openmsg", 0)
    link("openmsg", 0, "rec", 0)
    obj("startmsg", "1", 2, 1, [""], x=260, cls="message")
    link("route", 1, "startmsg", 0)
    link("startmsg", 0, "rec", 0)
    obj("stoptrig", "t b b", 1, 2, ["bang", "bang"], x=260)
    link("route", 2, "stoptrig", 0)
    obj("stopmsg", "0", 2, 1, [""], x=420, cls="message")
    link("stoptrig", 1, "stopmsg", 0)  # right outlet fires first: stop recording...
    link("stopmsg", 0, "rec", 0)
    obj("stoppedmsg", "/stopped", 2, 1, [""], x=260, cls="message")
    link("stoptrig", 0, "stoppedmsg", 0)  # ...then tell the server
    obj("udpout", f"udpsend 127.0.0.1 {REPLY_PORT}", 1, 0, x=260)
    link("stoppedmsg", 0, "udpout", 0)
    # /reply <port>: where to send replies (each server process listens on its own port)
    obj("replyport", "prepend port", 1, 1, [""], x=420)
    link("route", 5, "replyport", 0)
    link("replyport", 0, "udpout", 0)
    # Ping -> sample rate
    obj("dsp", "dspstate~", 1, 4, ["int", "float", "int", "int"], x=520)
    obj("sr", "f", 2, 1, ["float"], x=520)
    link("dsp", 1, "sr", 1)
    link("route", 4, "sr", 0)
    obj("pong", "prepend /pong", 1, 1, [""], x=520)
    link("sr", 0, "pong", 0)
    link("pong", 0, "udpout", 0)
    # Meter: peak and RMS per channel, ~10 Hz, gated by /meter 1|0
    obj("pkL", "peakamp~ 100", 2, 1, ["float"], x=20)
    obj("pkR", "peakamp~ 100", 2, 1, ["float"], x=20)
    obj("avL", "average~ 4800 rms", 1, 1, ["signal"], x=20)
    obj("avR", "average~ 4800 rms", 1, 1, ["signal"], x=20)
    obj("snL", "snapshot~ 100", 2, 1, ["float"], x=20)
    obj("snR", "snapshot~ 100", 2, 1, ["float"], x=20)
    for src, dst in (("in", "pkL"), ("in", "avL")):
        link(src, 0, dst, 0)
    for src, dst in (("in", "pkR"), ("in", "avR")):
        link(src, 1, dst, 0)
    link("avL", 0, "snL", 0)
    link("avR", 0, "snR", 0)
    obj("pack", "pack f f f f", 4, 1, [""], x=20)
    link("pkR", 0, "pack", 1)
    link("snL", 0, "pack", 2)
    link("snR", 0, "pack", 3)
    link("pkL", 0, "pack", 0)  # left inlet is hot: one message per peakamp~ L report
    obj("meterpre", "prepend /meter", 1, 1, [""], x=20)
    link("pack", 0, "meterpre", 0)
    obj("gate", "gate 1 0", 2, 1, [""], x=20)
    link("route", 3, "gate", 0)
    link("meterpre", 0, "gate", 1)
    link("gate", 0, "udpout", 0)
    # Visible label
    boxes.append({"box": {"id": "label", "maxclass": "live.comment", "numinlets": 1, "numoutlets": 0,
                          "text": "AbletonMCP Analyzer: capture + meter", "patching_rect": [20.0, 600.0, 220.0, 18.0],
                          "presentation": 1, "presentation_rect": [4.0, 4.0, 220.0, 18.0], "textjustification": 0}})
    p["boxes"], p["lines"] = boxes, lines
    p["description"] = "Capture and meter for the ableton-mcp server (OSC on UDP 9879, replies to 9878)."
    p["openinpresentation"] = 1
    p["devicewidth"] = 230.0
    ptch = json.dumps(doc, indent="\t").encode() + b"\n\x00"
    body = b"aaaa" + b"meta" + struct.pack("<I", len(meta)) + meta + b"ptch" + struct.pack("<I", len(ptch)) + ptch
    return b"ampf" + struct.pack("<I", 4) + body


if __name__ == "__main__":
    OUT.write_bytes(build())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
