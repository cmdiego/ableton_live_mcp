"""Analyze MIDI notes: key, chords, rhythm grid, step patterns, basic stats.

Notes are dicts with pitch, start_time, duration, velocity (beats are quarter notes).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean, pstdev

from . import NOTE_NAMES, note_name

# Krumhansl-Kessler key profiles.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

# Chord templates as intervals from the root; suffix used in the chord name.
CHORD_TEMPLATES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7), "m": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "5": (0, 7),
    "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "m7b5": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "add9": (0, 2, 4, 7),
    "madd9": (0, 2, 3, 7), "mMaj7": (0, 3, 7, 11),
    # Extended chords (folded into one octave for detection). The size penalty in identify_chord
    # keeps plain triads/7ths ahead when they explain the notes equally well.
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14), "6/9": (0, 4, 7, 9, 14),
    "11": (0, 4, 7, 10, 14, 17), "m11": (0, 3, 7, 10, 14, 17), "13": (0, 4, 7, 10, 14, 21),
    "7sus4": (0, 5, 7, 10),
}

# Same notes, different names (Am7 = C6, Em7 = G6): without a bass note, prefer the common reading.
RARER = {"6": 0.01, "m6": 0.01, "6/9": 0.01, "add9": 0.005, "madd9": 0.005}

GRIDS = [("1/4", 1.0), ("1/8", 0.5), ("1/8T", 1 / 3), ("1/16", 0.25), ("1/16T", 1 / 6), ("1/32", 0.125)]


def _active_notes(notes: list[dict]) -> list[dict]:
    return [n for n in notes if not n.get("mute")]


def pitch_class_weights(notes: list[dict], start: float | None = None, end: float | None = None) -> list[float]:
    """Duration-weighted pitch-class histogram, optionally clipped to [start, end)."""
    w = [0.0] * 12
    for n in notes:
        s, e = n["start_time"], n["start_time"] + n["duration"]
        if start is not None:
            s, e = max(s, start), min(e, end)
        if e > s:
            w[n["pitch"] % 12] += (e - s) * (0.5 + n.get("velocity", 100) / 254)
    return w


def _correlate(a: list[float], b: list[float]) -> float:
    ma, mb = mean(a), mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def detect_key(notes: list[dict]) -> dict | None:
    """Best-matching major/minor key with a 0..1 correlation and the runner-up."""
    weights = pitch_class_weights(_active_notes(notes))
    if sum(weights) == 0 or sum(1 for w in weights if w > 0) < 3:
        return None
    scores = []
    for root in range(12):
        rotated = weights[root:] + weights[:root]
        scores.append((_correlate(rotated, MAJOR_PROFILE), f"{NOTE_NAMES[root]} Major"))
        scores.append((_correlate(rotated, MINOR_PROFILE), f"{NOTE_NAMES[root]} Minor"))
    scores.sort(reverse=True)
    return {"key": scores[0][1], "confidence": round(scores[0][0], 2),
            "alternative": scores[1][1], "alternative_confidence": round(scores[1][0], 2)}


def identify_chord(weights: list[float], bass_pc: int | None = None) -> tuple[str, float] | None:
    """Best chord name for a pitch-class weight vector (slash chord if bass isn't the root)."""
    total = sum(weights)
    present = [pc for pc in range(12) if weights[pc] > 0.08 * total] if total else []
    if len(present) < 2:
        return None
    best: tuple[float, str] | None = None
    for root in range(12):
        for suffix, intervals in CHORD_TEMPLATES.items():
            tones = {(root + i) % 12 for i in intervals}
            covered = sum(weights[pc] for pc in tones) / total
            missing = sum(1 for pc in tones if weights[pc] <= 0.08 * total) / len(tones)
            score = covered - 0.6 * missing - 0.02 * len(tones) - RARER.get(suffix, 0.0)
            if bass_pc == root:
                score += 0.08
            if best is None or score > best[0]:
                name = NOTE_NAMES[root] + suffix
                if bass_pc is not None and bass_pc != root and bass_pc in tones:
                    name += "/" + NOTE_NAMES[bass_pc]
                best = (score, name)
    return (best[1], round(best[0], 2)) if best and best[0] > 0.45 else None


BASS_REGISTER = 48  # below C2 (Live naming) a lowest note counts as a bass note


def _bass_pc(sounding: list[dict]) -> int | None:
    """Pitch class of a real bass note, or None when the lowest note is just part of a voicing
    (otherwise every pad inversion would be reported as a slash chord)."""
    pitches = sorted({n["pitch"] for n in sounding})
    if not pitches:
        return None
    if len(pitches) == 1 or pitches[0] < BASS_REGISTER or pitches[1] - pitches[0] >= 7:
        return pitches[0] % 12
    return None


def detect_chords(notes: list[dict], length: float, window: float = 4.0) -> list[dict]:
    """Chord per window (default one 4/4 bar). Consecutive repeats are merged."""
    notes = _active_notes(notes)
    out: list[dict] = []
    t = 0.0
    while t < length:
        end = t + window
        weights = pitch_class_weights(notes, t, end)
        sounding = [n for n in notes if n["start_time"] < end and n["start_time"] + n["duration"] > t]
        chord = identify_chord(weights, _bass_pc(sounding))
        name = chord[0] if chord else None
        if out and out[-1]["chord"] == name:
            out[-1]["end"] = end
        else:
            out.append({"start": t, "end": end, "chord": name})
        t = end
    return [c for c in out if c["chord"]]


def rhythm_grid(notes: list[dict], tolerance: float = 0.03) -> dict | None:
    """Coarsest grid that explains >=90% of note starts, swing and off-grid share."""
    starts = [n["start_time"] for n in _active_notes(notes)]
    if not starts:
        return None

    def on_grid(t: float, step: float) -> bool:
        r = (t / step) - round(t / step)
        return abs(r * step) <= tolerance

    grid = None
    for name, step in GRIDS:
        share = sum(on_grid(t, step) for t in starts) / len(starts)
        if share >= 0.9:
            grid = {"grid": name, "on_grid_share": round(share, 2)}
            break
    if grid is None:
        share = sum(on_grid(t, 0.125) for t in starts) / len(starts)
        grid = {"grid": "free / unquantized", "on_grid_share_1_32": round(share, 2)}
    # Swing: offbeat 8ths played late (e.g. at 0.58-0.67 of the beat instead of 0.5).
    offbeats = [t % 1.0 for t in starts if 0.5 <= t % 1.0 <= 0.72]
    if len(offbeats) >= 4:
        avg = mean(offbeats)
        if avg > 0.54:
            grid["swing_8th"] = f"{round((avg - 0.5) / (1 / 6) * 100)}%"
    return grid


def step_pattern(notes: list[dict], beats_per_bar: float = 4.0, bars: int = 1,
                 steps_per_beat: int = 4) -> dict[int, str]:
    """Per pitch: a step string like 'x...x...x...x...' ('X' = accent, velocity >= 110)."""
    steps = int(beats_per_bar * bars * steps_per_beat)
    rows: dict[int, list[str]] = defaultdict(lambda: ["."] * steps)
    for n in _active_notes(notes):
        i = round(n["start_time"] * steps_per_beat)
        if 0 <= i < steps:
            rows[n["pitch"]][i] = "X" if n.get("velocity", 100) >= 110 else "x"
    return {p: "".join(r) for p, r in sorted(rows.items())}


def summarize(notes: list[dict], length: float, beats_per_bar: float = 4.0) -> dict:
    notes = _active_notes(notes)
    if not notes:
        return {"note_count": 0}
    pitches = [n["pitch"] for n in notes]
    vels = [n.get("velocity", 100) for n in notes]
    durs = [n["duration"] for n in notes]
    bars = max(length / beats_per_bar, 1e-9)
    # Polyphony: max notes sounding at once, sampled at every note start.
    poly = max(sum(1 for m in notes if m["start_time"] <= n["start_time"] < m["start_time"] + m["duration"])
               for n in notes)
    common = Counter(p % 12 for p in pitches).most_common(5)
    return {
        "note_count": len(notes),
        "notes_per_bar": round(len(notes) / bars, 1),
        "range": f"{note_name(min(pitches))}–{note_name(max(pitches))}",
        "lowest": min(pitches), "highest": max(pitches),
        "max_polyphony": poly,
        "velocity": {"min": min(vels), "max": max(vels), "mean": round(mean(vels)), "spread": round(pstdev(vels), 1)},
        "duration_beats": {"min": round(min(durs), 3), "max": round(max(durs), 3), "mean": round(mean(durs), 3)},
        "most_used_pitch_classes": [NOTE_NAMES[pc] for pc, _ in common],
    }
