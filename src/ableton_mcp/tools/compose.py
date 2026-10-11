"""Phase 4: write and transform MIDI — notes, chords, basslines, drums, arpeggios."""

from __future__ import annotations

import math
import random
from typing import Any, Literal

from .. import resolve
from ..app import tool
from ..live_client import LiveClient, LiveError, get_client
from ..theory import NOTE_NAMES, analysis, generate, note_name, parse_pitch, pitch_class
from ..theory import patterns as pat
from ..theory import transform as tf
from ..theory.chords import Chord, _parse_symbol, chord_label, parse_progression, prefers_flats
from ..theory.scales import LIVE_SCALES, live_scale_name
from ..util import compact
from .midi import beats_per_bar, drum_pad_names

Mode = Literal["new", "replace", "add"]
REGISTERS = {"low": (40, 64), "mid": (52, 79), "high": (64, 91)}


# -- key, destination, writing ----------------------------------------------------


def resolve_key(client: LiveClient, key: str | None) -> tuple[int, tuple[int, ...], str]:
    """(root pitch class, scale intervals, label) from 'A minor' / 'Am' / 'F# dorian', or the song key."""
    if not key:
        s = client.get("song", "root_note", "scale_name", "scale_intervals")
        return s["root_note"], tuple(s["scale_intervals"]), f"{NOTE_NAMES[s['root_note']]} {s['scale_name']}"
    parts = key.strip().split(None, 1)
    root_txt, scale_txt = parts[0], parts[1] if len(parts) > 1 else "Major"
    if root_txt.endswith("m") and len(root_txt) in (2, 3) and len(parts) == 1:
        root_txt, scale_txt = root_txt[:-1], "Minor"
    name = live_scale_name(scale_txt)
    root = pitch_class(root_txt)
    return root, LIVE_SCALES[name], f"{NOTE_NAMES[root]} {name}"


def _clip_bounds(client: LiveClient, track_path: str) -> list[tuple[int, float, float]]:
    clips = client.query(f"{track_path}.arrangement_clips", {"props": ["start_time", "end_time"]})
    return [(c["_i"], c["start_time"], c["end_time"]) for c in clips]


def destination(client: LiveClient, track, slot: int | None, arrangement_start: float | None,
                length: float, mode: str) -> tuple[str, bool]:
    """Find or create the clip to write into. Returns (clip path, created)."""
    tpath = resolve.track_path(client, track)
    info = client.get(tpath, "has_midi_input", "name")
    if not info["has_midi_input"]:
        raise LiveError(f"Track {info['name']!r} is not a MIDI track")
    if arrangement_start is not None:
        if slot is not None:
            raise LiveError("Give slot or arrangement_start_beat, not both")
        end = arrangement_start + length
        for i, s, e in _clip_bounds(client, tpath):
            if abs(s - arrangement_start) < 1e-6 and mode != "new":
                return f"{tpath}.arrangement_clips.{i}", False
            if s < end and e > arrangement_start:
                raise LiveError(f"Arrangement clip {i} ({s:g}–{e:g} beats) overlaps {arrangement_start:g}–{end:g}. "
                                "Pick another position, or use mode='replace'/'add' with its exact start.")
        client.invoke(tpath, "create_midi_clip", float(arrangement_start), float(length))
        for i, s, _ in _clip_bounds(client, tpath):
            if abs(s - arrangement_start) < 1e-6:
                return f"{tpath}.arrangement_clips.{i}", True
        raise LiveError("Live created the arrangement clip but it couldn't be found again")
    slots = client.query(f"{tpath}.clip_slots", {"props": ["has_clip"]})
    if slot is None:
        empty = [s["_i"] for s in slots if not s["has_clip"]]
        if not empty:
            raise LiveError("No empty clip slot on this track; give a slot (and mode) or create a scene")
        slot = empty[0]
    if not 0 <= slot < len(slots):
        raise LiveError(f"Slot {slot} doesn't exist (the track has {len(slots)} slots)")
    spath = f"{tpath}.clip_slots.{slot}"
    if slots[slot]["has_clip"]:
        if mode == "new":
            empty = [s["_i"] for s in slots if not s["has_clip"]]
            raise LiveError(f"Slot {slot} already has a clip. Use mode='replace' to overwrite its notes, "
                            f"mode='add' to layer, or an empty slot ({empty[:4] or 'none free'}).")
        return f"{spath}.clip", False
    client.invoke(spath, "create_clip", float(length))
    return f"{spath}.clip", True


def write_clip(client: LiveClient, path: str, notes: list[dict], length: float, mode: str, name: str | None,
               created: bool, undo_name: str) -> dict:
    """Write notes in one undo step; extends an existing clip if the notes run past its end."""
    commands: list[dict] = []
    if mode == "replace" and not created:
        commands.append({"cmd": "remove_notes", "params": {"path": path}})
    if not created:
        info = client.get(path, "length", "looping", "loop_end", "end_marker")
        if length > info["length"] + 1e-6:
            values = {"end_marker": length}
            if info["looping"]:
                values["loop_end"] = length
            commands.append({"cmd": "lom_set", "params": {"path": path, "values": values}})
    if name:
        commands.append({"cmd": "lom_set", "params": {"path": path, "prop": "name", "value": name}})
    for i in range(0, len(notes), 2000):
        commands.append({"cmd": "add_notes", "params": {"path": path, "notes": notes[i:i + 2000]}})
    client.batch(commands, undo_name=undo_name)
    return {"clip": path, "created": created, "mode": mode, "notes": len(notes), "length_beats": length}


def _post(notes: list[dict], swing: float | None, humanize: float | None, seed: int | None) -> list[dict]:
    if swing:
        notes = tf.swing(notes, swing)
    if humanize:
        rng = random.Random(seed)
        notes = tf.humanize(notes, timing_beats=0.012 * humanize, velocity=8 * humanize, rng=rng)
    return notes


def _length(bars: float | None, natural_beats: float, bpb: float) -> float:
    if bars:
        return bars * bpb
    return math.ceil(natural_beats / bpb - 1e-9) * bpb


def chords_from_clip(client: LiveClient, track, slot: int | None, arrangement_clip: int | None,
                     bpb: float, window: float | None) -> tuple[list[Chord], float]:
    """Detect the chords in an existing clip so other parts can follow them."""
    path = resolve.any_clip_path(client, track, slot, arrangement_clip)
    length = client.get(path, "length")["length"]
    detected = analysis.detect_chords(client.call("get_notes", path=path), length, window or bpb)
    if not detected:
        raise LiveError(f"No chords recognised in {path}")
    chords = []
    for i, c in enumerate(detected):
        chord = _parse_symbol(c["chord"])
        chord.bass = None  # an inversion in the source voicing isn't a bass instruction: follow roots
        start = 0.0 if i == 0 else c["start"]
        end = detected[i + 1]["start"] if i + 1 < len(detected) else length
        chord.bars = (end - start) / bpb
        chords.append(chord)
    return chords, length


def _harmony(client: LiveClient, progression: str | None, chords_from_track, chords_from_slot,
             chords_from_arrangement_clip, key: str | None, bpb: float) -> tuple[list[Chord], float, str, bool]:
    root, intervals, label = resolve_key(client, key)
    if progression:
        chords = parse_progression(progression, root, intervals)
        return chords, sum(c.bars for c in chords) * bpb, label, prefers_flats(root, intervals)
    if chords_from_track is not None:
        chords, length = chords_from_clip(client, chords_from_track, chords_from_slot,
                                          chords_from_arrangement_clip, bpb, None)
        return chords, length, label, prefers_flats(root, intervals)
    raise LiveError("Give a progression (e.g. 'i VI III VII', 'Am F C G' or a preset like 'pop'), "
                    "or chords_from_track + chords_from_slot to follow an existing clip")


def _labels(chords: list[Chord], flats: bool) -> list[str]:
    return [chord_label(c, flats) + (f" ({c.bars:g} bars)" if c.bars != 1 else "") for c in chords]


# -- tools ------------------------------------------------------------------------


@tool()
def write_notes(track: str | int, notes: list[list[str | float]], slot: int | None = None,
                arrangement_start_beat: float | None = None, length_bars: float | None = None,
                mode: Mode = "new", name: str | None = None) -> dict:
    """Write your own notes (melodies, hooks, anything) into a MIDI clip.

    notes: rows [pitch, start, duration, velocity?] with pitch as MIDI number or name in
    Live's convention ('C3' = 60), start/duration in beats from the clip start, velocity 1-127
    (default 100). Same shape read_clip returns, so you can read, edit and write back.
    Destination: slot (default: first empty slot) or arrangement_start_beat. mode: new (needs an
    empty slot), replace (clear the clip's notes first), add (layer on top)."""
    client = get_client()
    bpb = beats_per_bar(client)
    parsed = []
    for row in notes:
        if isinstance(row, dict):
            row = [row.get("pitch"), row.get("start", row.get("start_time")), row.get("duration"), row.get("velocity", 100)]
        if len(row) < 3:
            raise LiveError(f"Each note needs [pitch, start, duration, velocity?]; got {row!r}")
        # read_clip rows are [pitch, name, start, duration, velocity]: accept those too.
        if len(row) >= 5 and isinstance(row[1], str):
            row = [row[0], row[2], row[3], row[4]]
        parsed.append({"pitch": parse_pitch(row[0]), "start_time": float(row[1]), "duration": float(row[2]),
                       "velocity": float(row[3]) if len(row) > 3 and row[3] is not None else 100.0})
    end = max((n["start_time"] + n["duration"] for n in parsed), default=bpb)
    length = _length(length_bars, end, bpb)
    path, created = destination(client, track, slot, arrangement_start_beat, length, mode)
    return write_clip(client, path, parsed, length, mode, name, created, "Write notes")


@tool()
def write_chords(track: str | int, progression: str, key: str | None = None, bars: float | None = None,
                 rhythm: str = "sustain", voicing: Literal["close", "open", "spread"] = "close",
                 register: Literal["low", "mid", "high"] = "mid", include_bass: bool = False,
                 velocity: int = 90, humanize: float = 0.0, swing: float | None = None,
                 slot: int | None = None, arrangement_start_beat: float | None = None,
                 mode: Mode = "new", name: str | None = None, seed: int | None = None) -> dict:
    """Write a chord progression with smooth voice leading.

    progression: Roman numerals in the key ('i VI III VII', 'ii7 V7 Imaj7', 'bVII', 'V/vi'),
    chord symbols ('Am F C/E G7', 'Fmaj9'), or a preset: pop, sad, epic, dark, andalusian, jazz,
    jazz_minor, lofi, neo_soul, house, deep_house, techno, trap, dreamy, gospel, blues, royal_road,
    cinematic, phrygian. Each chord lasts 1 bar; 'Am:2' or 'F:0.5' sets bars per chord.
    key: 'A minor', 'F# dorian', 'Eb' (default: the song's key/scale).
    rhythm: sustain, half, quarter, eighths, offbeat, stabs, syncopated, charleston, reggae, pulse16,
    or a step string per bar ('x..x..x...x..x..': x hit, X accent, - hold, . rest).
    bars: clip length (default: one pass of the progression; it repeats to fill).
    humanize 0..1 adds timing/velocity variation; swing e.g. 0.58."""
    client = get_client()
    bpb = beats_per_bar(client)
    chords, natural, key_label, flats = _harmony(client, progression, None, None, None, key, bpb)
    length = _length(bars, natural, bpb)
    rhythm_steps = pat.fit_to_bar(pat.CHORD_RHYTHMS.get(rhythm, rhythm), int(bpb * 4))
    low, high = REGISTERS[register]
    notes = generate.chords_notes(chords, length, bpb, rhythm_steps, velocity, low, high, voicing,
                                  bass_octave=2 if include_bass else None)
    notes = _post(notes, swing, humanize, seed)
    path, created = destination(client, track, slot, arrangement_start_beat, length, mode)
    labels = _labels(chords, flats)
    result = write_clip(client, path, notes, length, mode, name or " ".join(chord_label(c, flats) for c in chords),
                        created, "Write chords")
    return compact({**result, "key": key_label, "chords": labels, "rhythm": rhythm_steps})


@tool()
def write_bassline(track: str | int, progression: str | None = None, chords_from_track: str | int | None = None,
                   chords_from_slot: int | None = None, chords_from_arrangement_clip: int | None = None,
                   style: str = "pulse", pattern: str | None = None, roles: list[str] | None = None,
                   key: str | None = None, bars: float | None = None, octave: int = 0, velocity: int = 100,
                   humanize: float = 0.0, swing: float | None = None, slot: int | None = None,
                   arrangement_start_beat: float | None = None, mode: Mode = "new", name: str | None = None,
                   seed: int | None = None) -> dict:
    """Write a bassline that follows chords: a progression (same syntax as write_chords) or the
    chords detected in an existing clip (chords_from_track + chords_from_slot/arrangement_clip).

    style: whole, half, pulse, eighths, offbeat, octaves, root_fifth, syncopated, funk, arp,
    walking, sub. Or a custom step pattern ('x--x--x---x-x---') with roles cycled per hit:
    R root, 5 fifth, O octave, 3 third, 7 seventh, A approach to the next chord.
    Roots sit around A0–G#1 (55–104 Hz); octave shifts by octaves."""
    client = get_client()
    bpb = beats_per_bar(client)
    chords, natural, key_label, flats = _harmony(client, progression, chords_from_track, chords_from_slot,
                                                 chords_from_arrangement_clip, key, bpb)
    length = _length(bars, natural, bpb)
    rhythm = pat.fit_to_bar(pattern, int(bpb * 4)) if pattern else None
    notes = generate.bass_notes(chords, length, bpb, style, rhythm, roles, velocity, low=33 + 12 * octave)
    notes = _post(notes, swing, humanize, seed)
    path, created = destination(client, track, slot, arrangement_start_beat, length, mode)
    result = write_clip(client, path, notes, length, mode, name or f"Bass ({style})", created, "Write bassline")
    lo, hi = min(n["pitch"] for n in notes), max(n["pitch"] for n in notes)
    return compact({**result, "key": key_label, "chords": _labels(chords, flats),
                    "range": f"{note_name(lo)}–{note_name(hi)}"})


@tool()
def write_arpeggio(track: str | int, progression: str | None = None, chords_from_track: str | int | None = None,
                   chords_from_slot: int | None = None, chords_from_arrangement_clip: int | None = None,
                   style: Literal["up", "down", "updown", "downup", "random", "chord"] = "up",
                   order: list[int] | None = None, rate: Literal["1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"] = "1/16",
                   octaves: int = 1, gate: float = 0.7, register: Literal["low", "mid", "high"] = "mid",
                   key: str | None = None, bars: float | None = None, velocity: int = 90,
                   humanize: float = 0.0, swing: float | None = None, slot: int | None = None,
                   arrangement_start_beat: float | None = None, mode: Mode = "new", name: str | None = None,
                   seed: int | None = None) -> dict:
    """Write an arpeggio over chords (a progression, or chords detected in an existing clip).
    style: up, down, updown, downup, random, chord (repeated chord). order: explicit
    1-based chord-tone sequence instead, e.g. [1, 3, 2, 4] (numbers past the chord size go up
    an octave). rate: note grid. octaves: range 1-3. gate: note length share 0.1-1."""
    client = get_client()
    bpb = beats_per_bar(client)
    chords, natural, key_label, flats = _harmony(client, progression, chords_from_track, chords_from_slot,
                                                 chords_from_arrangement_clip, key, bpb)
    length = _length(bars, natural, bpb)
    low, high = REGISTERS[register]
    notes = generate.arp_notes(chords, length, bpb, style, rate, max(1, min(octaves, 3)),
                               max(0.1, min(gate, 1.0)), velocity, low, high, order, seed)
    notes = _post(notes, swing, humanize, seed)
    path, created = destination(client, track, slot, arrangement_start_beat, length, mode)
    result = write_clip(client, path, notes, length, mode, name or f"Arp {style} {rate}", created,
                        "Write arpeggio")
    return compact({**result, "key": key_label, "chords": _labels(chords, flats)})


def _drum_map(client: LiveClient, track_path: str, voices: list[str]) -> tuple[dict[str, int], dict]:
    """Map voice names to pads by name when the track has a Drum Rack, else General MIDI."""
    pads = drum_pad_names(client, track_path)
    mapping, report, missing = {}, {}, []
    used: set[int] = set()
    for voice in voices:
        note = None
        if pads:
            for kw in pat.DRUM_KEYWORDS.get(voice, [voice.replace("_", " ")]):
                for pitch, pad in sorted(pads.items()):
                    pad_l = pad.lower()
                    if kw in pad_l and pitch not in used and not (voice == "closed_hat" and "open" in pad_l):
                        note = pitch
                        break
                if note is not None:
                    break
        if note is None:
            if voice not in pat.GM_DRUMS:
                raise LiveError(f"Unknown drum voice {voice!r}; use {', '.join(pat.GM_DRUMS)}")
            note = pat.GM_DRUMS[voice]
            if pads:
                missing.append(voice)
        used.add(note)
        mapping[voice] = note
        report[voice] = f"{pads.get(note, 'GM')} ({note})" if pads else f"GM {note}"
    return mapping, {"pads": report, "no_matching_pad": missing or None, "drum_rack": bool(pads)}


@tool()
def write_drums(track: str | int, genre: str = "house", pattern: dict[str, str] | None = None,
                bars: float = 2, swing: float | None = None, humanize: float = 0.0, fill_every: int = 0,
                velocity: int = 100, slot: int | None = None, arrangement_start_beat: float | None = None,
                mode: Mode = "new", name: str | None = None, seed: int | None = None) -> dict:
    """Write a drum groove. Voices are matched to the track's Drum Rack pads by name (Kick, Snare,
    Hat…), falling back to General MIDI notes.

    genre: house, techno, hiphop, trap, dnb, breakbeat, garage, lofi, reggaeton, afro, rock, disco.
    pattern: voice -> step string, merged over the genre (use genre='none' for only your pattern).
    Voices: kick, snare, clap, rim, closed_hat, open_hat, pedal_hat, ride, crash, shaker, tambourine,
    low_tom, mid_tom, high_tom, cowbell, conga, perc. Steps (16ths): x hit, X accent, g ghost,
    r 2-hit roll, t 3-hit roll, . rest; '|' bar lines are ignored.
    swing overrides the genre default (0.5 straight .. 0.67 triplet). fill_every: snare fill in
    the last beat of every N bars."""
    client = get_client()
    bpb = beats_per_bar(client)
    if genre != "none" and genre not in pat.DRUM_GENRES:
        raise LiveError(f"Unknown genre {genre!r}; use {', '.join(pat.DRUM_GENRES)} or 'none' with a pattern")
    preset = dict(pat.DRUM_GENRES.get(genre, {}))
    genre_swing = preset.pop("swing", None)
    voices = {**preset, **(pattern or {})}
    if not voices:
        raise LiveError("No drum voices: give a genre or a pattern")
    steps = int(bpb * 4)
    voices = {v: pat.fit_to_bar(p, steps) for v, p in voices.items()}
    tpath = resolve.track_path(client, track)
    mapping, report = _drum_map(client, tpath, list(voices))
    if fill_every and "snare" not in mapping:
        # Fills play on the snare even when the groove doesn't use one (house: clap only).
        fill_map, _ = _drum_map(client, tpath, ["snare"])
        mapping["snare"] = fill_map["snare"]
    length = bars * bpb
    notes = generate.drum_notes(voices, mapping, length, bpb, velocity, fill_every)
    notes = _post(notes, swing if swing is not None else genre_swing, humanize, seed)
    path, created = destination(client, track, slot, arrangement_start_beat, length, mode)
    result = write_clip(client, path, notes, length, mode, name or f"Drums ({genre})", created, "Write drums")
    return compact({**result, **report, "swing": swing if swing is not None else genre_swing,
                    "pattern": {v: "|".join(p[i:i + steps] for i in range(0, len(p), steps)) for v, p in voices.items()}})


@tool()
def transform_notes(track: str | int, operations: list[dict[str, Any]], slot: int | None = None,
                    arrangement_clip: int | None = None, pitch_range: list[int | str] | None = None,
                    start_beat: float | None = None, end_beat: float | None = None,
                    key: str | None = None, seed: int | None = None) -> dict:
    """Edit notes in a MIDI clip with a list of operations applied in order, e.g.
    [{"op": "transpose", "semitones": -12}, {"op": "quantize", "grid": "1/16", "strength": 0.7}].

    Ops: transpose {semitones} | transpose_diatonic {steps} (in key) | fit_to_scale |
    quantize {grid, strength, ends} | swing {amount, grid} | humanize {timing_ms, velocity} |
    velocity {scale|offset|set|ramp:[from,to]} | legato {gap} | length {factor} |
    stretch {factor: 2 = half-time, 0.5 = double-time} | reverse | invert {axis} |
    thin {keep: 0..1} | remove. Grids: 1/4 1/8 1/8T 1/16 1/16T 1/32.
    Only notes inside pitch_range [low, high] and start_beat..end_beat are affected."""
    client = get_client()
    path = resolve.any_clip_path(client, track, slot, arrangement_clip)
    info = client.get(path, "length", "is_midi_clip")
    if not info["is_midi_clip"]:
        raise LiveError(f"{path} is not a MIDI clip")
    tempo = client.get("song", "tempo")["tempo"]
    all_notes = client.call("get_notes", path=path)
    lo = parse_pitch(pitch_range[0]) if pitch_range else 0
    hi = parse_pitch(pitch_range[1]) if pitch_range else 127
    t0 = start_beat if start_beat is not None else -math.inf
    t1 = end_beat if end_beat is not None else math.inf
    selected = [n for n in all_notes if lo <= n["pitch"] <= hi and t0 <= n["start_time"] < t1]
    if not selected:
        raise LiveError("No notes match the selection")
    rng = random.Random(seed)
    notes = [dict(n) for n in selected]
    root, intervals = None, None
    for op in operations:
        kind = op.get("op")
        if kind in ("transpose_diatonic", "fit_to_scale") and root is None:
            root, intervals, _ = resolve_key(client, key)
        if kind == "transpose":
            notes = tf.transpose(notes, int(op.get("semitones", 0)))
        elif kind == "transpose_diatonic":
            notes = tf.transpose_diatonic(notes, int(op.get("steps", 0)), root, intervals)
        elif kind == "fit_to_scale":
            notes = tf.fit_to_scale(notes, root, intervals)
        elif kind == "quantize":
            notes = tf.quantize(notes, op.get("grid", "1/16"), float(op.get("strength", 1.0)), bool(op.get("ends", False)))
        elif kind == "swing":
            notes = tf.swing(notes, float(op.get("amount", 0.58)), op.get("grid", "1/16"))
        elif kind == "humanize":
            timing_beats = float(op.get("timing_ms", 10)) / 1000 * tempo / 60
            notes = tf.humanize(notes, timing_beats, float(op.get("velocity", 6)), rng)
        elif kind == "velocity":
            notes = tf.velocity(notes, float(op.get("scale", 1.0)), float(op.get("offset", 0.0)), op.get("set"),
                                tuple(op["ramp"]) if op.get("ramp") else None)
        elif kind == "legato":
            notes = tf.legato(notes, float(op.get("gap", 0.0)))
        elif kind == "length":
            notes = tf.scale_length(notes, float(op.get("factor", 1.0)))
        elif kind == "stretch":
            notes = tf.stretch(notes, float(op.get("factor", 1.0)))
        elif kind == "reverse":
            notes = tf.reverse(notes, info["length"])
        elif kind == "invert":
            notes = tf.invert(notes, parse_pitch(op["axis"]) if op.get("axis") is not None else None)
        elif kind == "thin":
            notes = tf.thin(notes, float(op.get("keep", 0.5)), rng)
        elif kind == "remove":
            notes = []
        else:
            raise LiveError(f"Unknown op {kind!r}")
    keep_fields = ("pitch", "start_time", "duration", "velocity", "mute", "probability", "velocity_deviation",
                   "release_velocity")
    new_notes = [{k: n[k] for k in keep_fields if n.get(k) is not None} for n in notes]
    commands = [{"cmd": "remove_notes", "params": {"path": path, "note_ids": [n["note_id"] for n in selected]}}]
    if new_notes:
        commands.append({"cmd": "add_notes", "params": {"path": path, "notes": new_notes}})
    client.batch(commands, undo_name="Transform notes")
    end = max((n["start_time"] + n["duration"] for n in new_notes), default=0)
    return compact({"clip": path, "changed": len(selected), "now": len(new_notes),
                    "ops": [o.get("op") for o in operations],
                    "warning": f"notes now run to beat {end:g}, past the clip end ({info['length']:g})"
                    if end > info["length"] + 1e-6 else None})
