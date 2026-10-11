"""Phase 2: inspect MIDI notes and patterns."""

from __future__ import annotations

from typing import Literal

from .. import resolve
from ..app import tool
from ..live_client import LiveClient, get_client
from ..theory import analysis, note_name
from ..util import compact

NOTE_COLUMNS = ["pitch", "name", "start", "duration", "velocity"]


def beats_per_bar(client: LiveClient) -> float:
    s = client.get("song", "signature_numerator", "signature_denominator")
    return s["signature_numerator"] * 4.0 / s["signature_denominator"]


def load_midi_clip(client: LiveClient, track, slot, arrangement_clip) -> tuple[str, dict, list[dict]]:
    path = resolve.any_clip_path(client, track, slot, arrangement_clip)
    info = client.get(path, "name", "length", "is_midi_clip", "loop_start", "loop_end", "looping")
    if not info["is_midi_clip"]:
        raise ValueError(f"{path} is an audio clip; use analyze_audio(track=..., slot=...) for audio")
    notes = client.call("get_notes", path=path)
    return path, info, notes


def drum_pad_names(client: LiveClient, track_path: str) -> dict[int, str]:
    """Pad names by MIDI note if the track's first rack-like device is a Drum Rack."""
    devices = client.query(f"{track_path}.devices", {"props": ["class_name"]})
    for d in devices:
        if d.get("class_name") == "DrumGroupDevice":
            pads = client.query(f"{track_path}.devices.{d['_i']}.drum_pads",
                                {"props": ["note", "name"], "children": {"chains": {"props": ["name"]}}})
            return {p["note"]: p["name"] for p in pads if p.get("chains")}
    return {}


def _track_of(path: str) -> str:
    return path.split(".clip_slots.")[0].split(".arrangement_clips.")[0]


def get_clip_notes(track: str | int, slot: int | None = None, arrangement_clip: int | None = None,
                   start_beat: float | None = None, end_beat: float | None = None) -> dict:
    """Read a MIDI clip's notes as rows [pitch, name, start, duration, velocity] (beats from clip start).
    Address a session clip with slot, or an arrangement clip with arrangement_clip (its index).
    Use start_beat/end_beat to read part of a long clip. Muted notes are flagged."""
    client = get_client()
    path, info, notes = load_midi_clip(client, track, slot, arrangement_clip)
    if start_beat is not None or end_beat is not None:
        lo, hi = start_beat or 0.0, end_beat if end_beat is not None else float("inf")
        notes = [n for n in notes if lo <= n["start_time"] < hi]
    rows, muted = [], []
    for n in notes:
        rows.append([n["pitch"], note_name(n["pitch"]), round(n["start_time"], 4), round(n["duration"], 4),
                     round(n["velocity"])])
        if n.get("mute"):
            muted.append(len(rows) - 1)
    return compact({
        "clip": path, "name": info["name"] or None, "length_beats": info["length"],
        "loop": [info["loop_start"], info["loop_end"]] if info["looping"] else None,
        "columns": NOTE_COLUMNS, "notes": rows, "muted_rows": muted,
    })


def analyze_clip_midi(track: str | int, slot: int | None = None, arrangement_clip: int | None = None,
                      chord_window_beats: float | None = None) -> dict:
    """Musical summary of a MIDI clip: likely key, chords per bar (or per chord_window_beats),
    rhythm grid and swing, range, density, polyphony, velocity spread. Drum clips (Drum Rack)
    get per-pad step patterns instead of harmony ('x' hit, 'X' accent, '|' bar line)."""
    client = get_client()
    path, info, notes = load_midi_clip(client, track, slot, arrangement_clip)
    bpb = beats_per_bar(client)
    length = info["length"]
    result: dict = {"clip": path, "name": info["name"] or None, "length_bars": round(length / bpb, 2),
                    "summary": analysis.summarize(notes, length, bpb), "rhythm": analysis.rhythm_grid(notes)}
    pads = drum_pad_names(client, _track_of(path))
    bars = max(1, min(4, round(length / bpb)))
    steps_per_bar = int(bpb * 4)
    patterns = analysis.step_pattern(notes, bpb, bars)

    def with_bar_lines(s: str) -> str:
        return "|".join(s[i:i + steps_per_bar] for i in range(0, len(s), steps_per_bar))

    if pads:
        result["drum_patterns"] = {f"{pads.get(p, note_name(p))} ({p})": with_bar_lines(s) for p, s in patterns.items()}
        result["pattern_bars_shown"] = bars
    else:
        song = client.get("song", "root_note", "scale_name")
        result["key"] = analysis.detect_key(notes)
        result["song_key"] = f"{analysis.NOTE_NAMES[song['root_note']]} {song['scale_name']}"
        result["chords"] = analysis.detect_chords(notes, length, chord_window_beats or bpb)
        if result["summary"].get("max_polyphony", 0) <= 1:
            result["melody_steps"] = {note_name(p): with_bar_lines(s) for p, s in patterns.items()}
    return compact(result)


@tool(read_only=True)
def read_clip(track: str | int, slot: int | None = None, arrangement_clip: int | None = None,
              include: Literal["both", "notes", "analysis"] = "both", start_beat: float | None = None,
              end_beat: float | None = None, chord_window_beats: float | None = None) -> dict:
    """Read a MIDI clip (session slot, or arrangement_clip index from get_track_details).

    notes: rows [pitch, name, start, duration, velocity] in beats from the clip start (the same
    shape write_notes accepts, so you can read, edit and write back); start_beat/end_beat read
    part of a long clip. analysis: likely key vs the song key, chords per bar (or per
    chord_window_beats), rhythm grid and swing, range, density, polyphony, velocity spread; drum
    clips (Drum Rack) get per-pad step patterns instead ('x' hit, 'X' accent, '|' bar line)."""
    out: dict = {}
    if include in ("both", "notes"):
        out["notes"] = get_clip_notes(track, slot, arrangement_clip, start_beat, end_beat)
    if include in ("both", "analysis"):
        out["analysis"] = analyze_clip_midi(track, slot, arrangement_clip, chord_window_beats)
    return out
