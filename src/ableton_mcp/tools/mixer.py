"""Phase 3: mixer levels, panning, sends."""

from __future__ import annotations

import math
import re
import string

from .. import resolve
from ..app import tool
from ..live_client import LiveError, get_client
from ..util import compact

MIXER_SPEC = {
    "props": ["name", "mute", "solo", "output_meter_left", "output_meter_right"],
    "children": {"mixer_device": {"children": {
        "volume": {"props": ["$display"]},
        "panning": {"props": ["$display"]},
        "sends": {"props": ["$display"]},
    }}},
}


def parse_db(text: str | None) -> float | None:
    if not text:
        return None
    if "inf" in text:
        return -math.inf
    m = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _meter_db(level: float | None) -> float | None:
    """Live's output meters are 0..1 peak levels; express as dBFS (approximate)."""
    if not level:
        return None
    return round(20 * math.log10(level), 1)


def _shape(t: dict, returns: list[str], playing: bool) -> dict:
    m = t.get("mixer_device") or {}
    out = {
        "index": t.get("_i"),
        "name": t.get("name"),
        "volume": (m.get("volume") or {}).get("$display"),
        "pan": (m.get("panning") or {}).get("$display"),
        "sends": {returns[i]: s.get("$display") for i, s in enumerate(m.get("sends") or []) if i < len(returns)
                  and s.get("$display") != "-inf dB"},
        "mute": t.get("mute") or None,
        "solo": t.get("solo") or None,
    }
    if playing:
        out["meter_peak_db"] = [_meter_db(t.get("output_meter_left")), _meter_db(t.get("output_meter_right"))]
    return out


@tool(read_only=True)
def get_mix_state() -> dict:
    """Mixer for every track, return and the master: volume, pan, active sends (by return name),
    mute/solo. While the song plays, also a momentary output peak per side in dBFS (a rough
    snapshot from Live's meters, not a loudness measurement)."""
    client = get_client()
    data = client.query("song", {"props": ["is_playing"], "children": {
        "tracks": MIXER_SPEC, "return_tracks": MIXER_SPEC, "master_track": MIXER_SPEC}})
    returns = [r["name"] for r in data["return_tracks"]]
    playing = bool(data.get("is_playing"))
    return compact({
        "playing": playing,
        "tracks": [_shape(t, returns, playing) for t in data["tracks"]],
        "returns": [_shape(t, returns, playing) for t in data["return_tracks"]],
        "master": _shape(data["master_track"], returns, playing),
    })


def _send_index(client, send: str | int) -> int:
    names = [r["name"] for r in client.query("song.return_tracks", {"props": ["name"]})]
    if isinstance(send, str):
        key = send.strip().lower().removeprefix("send").removeprefix("return").strip()
        if len(key) == 1 and key in string.ascii_lowercase:
            idx = string.ascii_lowercase.index(key)
            if idx < len(names):
                return idx
    return resolve.match_name(names, send, "return/send")


@tool()
def set_mixer(track: str | int, volume_db: float | None = None, change_db: float | None = None,
              pan: str | float | None = None, sends: dict[str, float] | None = None) -> dict:
    """Set one track's mixer. volume_db is absolute (0 = unity, max +6), change_db is relative
    (e.g. -3). pan is -50..50 or '25L'/'C'/'10R'. sends maps a return (name, letter 'A', or
    index) to a level in dB (-inf/-70 = off, max 0). Works on returns and 'master' too."""
    client = get_client()
    path = resolve.track_path(client, track)
    mixer = f"{path}.mixer_device"
    commands = []
    if volume_db is not None and change_db is not None:
        raise LiveError("Give volume_db or change_db, not both")
    if change_db is not None:
        current = parse_db(client.get(f"{mixer}.volume", "$display")["$display"])
        volume_db = max(current if current != -math.inf else -70.0, -70.0) + change_db
    if volume_db is not None:
        commands.append({"cmd": "param_set_display", "params": {"path": f"{mixer}.volume", "target": volume_db}})
    if pan is not None:
        commands.append({"cmd": "param_set_display", "params": {"path": f"{mixer}.panning", "target": pan}})
    for send, db in (sends or {}).items():
        idx = _send_index(client, send)
        commands.append({"cmd": "param_set_display", "params": {"path": f"{mixer}.sends.{idx}", "target": db}})
    if not commands:
        raise LiveError("Nothing to change")
    client.batch(commands, undo_name="Mixer change")
    state = client.query(path, MIXER_SPEC)
    returns = [r["name"] for r in client.query("song.return_tracks", {"props": ["name"]})]
    return compact(_shape(state, returns, False))
