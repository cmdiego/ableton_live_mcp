"""Pure note transforms. Notes are dicts: pitch, start_time, duration, velocity (+ optional extras)."""

from __future__ import annotations

import random

from .scales import scale_step, snap_to_scale

GRIDS = {"1/4": 1.0, "1/8": 0.5, "1/8T": 1 / 3, "1/16": 0.25, "1/16T": 1 / 6, "1/32": 0.125}


def grid_beats(grid: str | float) -> float:
    if isinstance(grid, (int, float)):
        return float(grid)
    if grid not in GRIDS:
        raise ValueError(f"Unknown grid {grid!r}; use one of {', '.join(GRIDS)}")
    return GRIDS[grid]


def _clamp_velocity(v: float) -> float:
    return float(max(1, min(127, round(v))))


def transpose(notes, semitones: int):
    return [{**n, "pitch": n["pitch"] + semitones} for n in notes if 0 <= n["pitch"] + semitones <= 127]


def transpose_diatonic(notes, steps: int, root: int, intervals):
    return [{**n, "pitch": scale_step(n["pitch"], steps, root, intervals)} for n in notes]


def fit_to_scale(notes, root: int, intervals):
    return [{**n, "pitch": snap_to_scale(n["pitch"], root, intervals)} for n in notes]


def quantize(notes, grid="1/16", strength: float = 1.0, ends: bool = False):
    g = grid_beats(grid)
    out = []
    for n in notes:
        target = round(n["start_time"] / g) * g
        start = n["start_time"] + (target - n["start_time"]) * strength
        dur = n["duration"]
        if ends:
            end = n["start_time"] + dur
            dur = max(g, round(end / g) * g - start)
        out.append({**n, "start_time": max(0.0, start), "duration": dur})
    return out


def swing(notes, amount: float = 0.58, grid="1/16"):
    """Delay every second grid step. amount is the pair split: 0.5 straight, 0.67 triplet feel."""
    g = grid_beats(grid)
    shift = (amount - 0.5) * 2 * g
    out = []
    for n in notes:
        pos = n["start_time"] / g
        if abs(pos - round(pos)) < 0.05 and round(pos) % 2 == 1:
            out.append({**n, "start_time": n["start_time"] + shift, "duration": max(0.01, n["duration"] - shift / 2)})
        else:
            out.append(n)
    return out


def humanize(notes, timing_beats: float = 0.01, velocity: float = 6, rng: random.Random | None = None):
    rng = rng or random.Random()
    out = []
    for n in notes:
        start = max(0.0, n["start_time"] + rng.uniform(-timing_beats, timing_beats))
        out.append({**n, "start_time": start, "velocity": _clamp_velocity(n["velocity"] + rng.uniform(-velocity, velocity))})
    return out


def velocity(notes, scale: float = 1.0, offset: float = 0.0, set_to: float | None = None,
             ramp: tuple[float, float] | None = None):
    if not notes:
        return notes
    t0 = min(n["start_time"] for n in notes)
    t1 = max(n["start_time"] for n in notes)
    out = []
    for n in notes:
        if set_to is not None:
            v = set_to
        elif ramp is not None:
            f = (n["start_time"] - t0) / (t1 - t0) if t1 > t0 else 0.0
            v = ramp[0] + (ramp[1] - ramp[0]) * f
        else:
            v = n["velocity"] * scale + offset
        out.append({**n, "velocity": _clamp_velocity(v)})
    return out


def legato(notes, gap: float = 0.0):
    """Extend each note to the next note start (per chord-onset, so chords stay together)."""
    starts = sorted({round(n["start_time"], 6) for n in notes})
    nxt = {s: starts[i + 1] if i + 1 < len(starts) else None for i, s in enumerate(starts)}
    out = []
    for n in notes:
        following = nxt[round(n["start_time"], 6)]
        if following is not None:
            out.append({**n, "duration": max(0.01, following - n["start_time"] - gap)})
        else:
            out.append(n)
    return out


def scale_length(notes, factor: float):
    return [{**n, "duration": max(0.01, n["duration"] * factor)} for n in notes]


def stretch(notes, factor: float):
    """Time-stretch positions and lengths: 2 = half-time, 0.5 = double-time."""
    return [{**n, "start_time": n["start_time"] * factor, "duration": max(0.01, n["duration"] * factor)} for n in notes]


def reverse(notes, length: float):
    return [{**n, "start_time": max(0.0, length - n["start_time"] - n["duration"])} for n in notes]


def invert(notes, axis: int | None = None):
    if not notes:
        return notes
    if axis is None:
        axis = (min(n["pitch"] for n in notes) + max(n["pitch"] for n in notes)) // 2
    return [{**n, "pitch": 2 * axis - n["pitch"]} for n in notes if 0 <= 2 * axis - n["pitch"] <= 127]


def thin(notes, keep: float, rng: random.Random | None = None):
    """Randomly keep a share of notes (0..1)."""
    rng = rng or random.Random()
    return [n for n in notes if rng.random() < keep]
