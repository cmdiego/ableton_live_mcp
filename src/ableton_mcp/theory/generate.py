"""Turn progressions and patterns into notes. Pure functions, no Live access."""

from __future__ import annotations

import random

from . import patterns as pat
from .chords import Chord, chord_spans, voice_progression
from .patterns import Event

LEVEL_VELOCITY = {"normal": 0, "accent": 22, "ghost": -50}


def tiled_spans(chords: list[Chord], length: float, beats_per_bar: float) -> list[tuple[float, float, Chord]]:
    """Repeat the progression to fill `length` beats."""
    one = chord_spans(chords, beats_per_bar)
    period = one[-1][1]
    spans, offset = [], 0.0
    while offset < length - 1e-9:
        for s, e, c in one:
            if offset + s >= length - 1e-9:
                break
            spans.append((offset + s, min(offset + e, length), c))
        offset += period
    return spans


def _split(events: list[Event], spans) -> list[tuple[Event, int]]:
    """Split events at chord boundaries (held notes retrigger on a chord change)."""
    out = []
    for ev in events:
        end = ev.start + ev.duration
        for i, (s, e, _) in enumerate(spans):
            if s < end - 1e-9 and e > ev.start + 1e-9:
                part_start = max(s, ev.start)
                out.append((Event(part_start, min(e, end) - part_start, ev.level, ev.hits), i))
    return out


def _note(pitch: int, start: float, dur: float, vel: float) -> dict:
    return {"pitch": int(pitch), "start_time": round(start, 6), "duration": round(max(dur, 0.01), 6),
            "velocity": float(max(1, min(127, round(vel))))}


def _hit_notes(ev: Event, pitch: int, velocity: float, gate: float) -> list[dict]:
    vel = velocity + LEVEL_VELOCITY[ev.level]
    if ev.hits == 1:
        dur = ev.duration * (gate if ev.duration <= 0.25 + 1e-9 else 1.0)
        return [_note(pitch, ev.start, dur, vel)]
    sub = ev.duration / ev.hits
    return [_note(pitch, ev.start + k * sub, sub * gate, vel - 8 * (ev.hits - 1 - k)) for k in range(ev.hits)]


def chords_notes(chords: list[Chord], length: float, beats_per_bar: float, rhythm: str, velocity: float = 90,
                 low: int = 52, high: int = 79, style: str = "close", bass_octave: int | None = None,
                 gate: float = 0.85) -> list[dict]:
    spans = tiled_spans(chords, length, beats_per_bar)
    voicings = voice_progression([c for _, _, c in spans], low, high, style, bass_octave)
    notes = []
    for ev, i in _split(pat.events(rhythm, length), spans):
        for p in voicings[i]:
            notes += _hit_notes(ev, p, velocity, gate)
    return notes


def _root_in_register(pc: int, low: int) -> int:
    return low + (pc - low) % 12


def _role_pitch(role: str, chord: Chord, next_chord: Chord | None, low: int) -> int:
    root = _root_in_register(chord.root, low)
    if role == "R":
        return _root_in_register(chord.bass, low) if chord.bass is not None else root
    if role == "5":
        return root + 7 if 7 in chord.intervals or 6 not in chord.intervals else root + 6
    if role == "O":
        return root + 12
    if role == "3":
        third = next((i for i in chord.intervals if i in (3, 4)), 7)
        return root + third
    if role == "7":
        seventh = next((i for i in chord.intervals if i in (10, 11)), 12)
        return root + seventh
    if role == "A":  # chromatic approach from below into the next chord's root
        target = _root_in_register((next_chord or chord).root, low)
        if target == root:
            return root + 7
        return target - 1 if target - 1 > root else target + 1
    raise ValueError(f"Unknown bass role {role!r}")


def bass_notes(chords: list[Chord], length: float, beats_per_bar: float, style: str = "pulse",
               pattern: str | None = None, roles: list[str] | None = None, velocity: float = 100,
               low: int = 33, gate: float = 0.8) -> list[dict]:
    if style not in pat.BASS_STYLES and not pattern:
        raise ValueError(f"Unknown bass style {style!r}; use one of {', '.join(pat.BASS_STYLES)} or a pattern")
    preset_pattern, preset_roles = pat.BASS_STYLES.get(style, ("x", ["R"]))
    rhythm = pattern or preset_pattern
    roles = roles or preset_roles
    spans = tiled_spans(chords, length, beats_per_bar)
    notes = []
    hit_in_chord: dict[int, int] = {}
    for ev, i in _split(pat.events(rhythm, length), spans):
        chord = spans[i][2]
        next_chord = spans[i + 1][2] if i + 1 < len(spans) else spans[0][2]
        k = hit_in_chord.get(i, 0)
        role = roles[k % len(roles)]
        if style == "walking" and not pattern:
            # Approach note only on the last beat before a chord change.
            beats_left = spans[i][1] - ev.start
            role = "A" if beats_left <= 1.0 + 1e-9 and k > 0 else roles[k % len(roles)]
        hit_in_chord[i] = k + 1
        notes += _hit_notes(ev, _role_pitch(role, chord, next_chord, low), velocity, gate)
    return notes


def arp_notes(chords: list[Chord], length: float, beats_per_bar: float, mode: str = "up", rate: str = "1/16",
              octaves: int = 1, gate: float = 0.7, velocity: float = 90, low: int = 55, high: int = 79,
              order: list[int] | None = None, seed: int | None = None) -> list[dict]:
    if rate not in pat.ARP_RATES:
        raise ValueError(f"Unknown rate {rate!r}; use one of {', '.join(pat.ARP_RATES)}")
    step = pat.ARP_RATES[rate]
    spans = tiled_spans(chords, length, beats_per_bar)
    voicings = voice_progression([c for _, _, c in spans], low, high, "close")
    rng = random.Random(seed)
    notes = []
    for (s, e, _), voicing in zip(spans, voicings):
        tones = sorted(voicing)
        tones = [p + 12 * o for o in range(max(1, octaves)) for p in tones]
        if order:
            seq = [tones[(d - 1) % len(tones)] + 12 * ((d - 1) // len(tones)) for d in order]
        elif mode == "up":
            seq = tones
        elif mode == "down":
            seq = tones[::-1]
        elif mode == "updown":
            seq = tones + tones[-2:0:-1]
        elif mode == "downup":
            seq = tones[::-1] + tones[1:-1]
        elif mode in ("random", "chord"):
            seq = tones
        else:
            raise ValueError(f"Unknown arp mode {mode!r}; use up, down, updown, downup, random, chord or order")
        t, k = s, 0
        while t < e - 1e-9:
            dur = min(step * gate, e - t)
            accent = 10 if abs((t / beats_per_bar) - round(t / beats_per_bar)) < 1e-9 else 0
            if mode == "chord":
                notes += [_note(p, t, dur, velocity + accent) for p in tones]
            else:
                p = rng.choice(seq) if mode == "random" else seq[k % len(seq)]
                notes.append(_note(p, t, dur, velocity + accent))
            t += step
            k += 1
    return notes


def drum_notes(voices: dict[str, str], pitch_map: dict[str, int], length: float, beats_per_bar: float,
               velocity: float = 100, fill_every: int = 0, gate: float = 0.5) -> list[dict]:
    notes = []
    for voice, rhythm in voices.items():
        for ev in pat.events(rhythm, length):
            notes += _hit_notes(ev, pitch_map[voice], velocity, gate)
    if fill_every and "snare" in pitch_map:
        bar_len = beats_per_bar
        bar = fill_every
        while bar * bar_len <= length + 1e-9:
            fill_start = bar * bar_len - 1.0
            notes = [n for n in notes if not (fill_start <= n["start_time"] < bar * bar_len)
                     or n["pitch"] == pitch_map.get("kick")]
            notes += [_note(pitch_map["snare"], fill_start + k * 0.25, 0.2, 70 + 15 * k) for k in range(4)]
            bar += fill_every
    return notes
