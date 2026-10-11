"""Arrangement analysis: per-bar activity, energy, sections, labels and observations.

Input is plain data (no Live access): tracks with arrangement clips (beats) and MIDI note
start times in song beats. Bars are 1-based in everything returned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ROLE_WEIGHT = {"kick": 1.5, "drums": 1.5, "bass": 1.3, "snare": 1.0, "clap": 1.0, "hats": 0.8, "perc": 0.8,
               "vocal": 1.2, "lead": 1.0, "keys": 0.9, "pad": 0.7, "guitar": 0.9, "fx": 0.4, "other": 0.9}
PHRASE = 4          # bars: section boundaries snap to this grid
MIN_SECTION = 4     # bars
BOUNDARY = 0.25     # change score that starts a new section


@dataclass
class TrackActivity:
    name: str
    role: str
    clips: list[tuple[float, float]]                    # (start, end) beats of unmuted clips
    note_times: list[float] = field(default_factory=list)  # song-beat positions of MIDI notes


@dataclass
class Grid:
    bars: int
    beats_per_bar: float
    active: list[set[int]]        # per bar: indices of tracks playing
    notes: list[int]              # per bar: MIDI notes started
    energy: list[float]           # per bar: 0..1
    loudness: list[float | None]  # per bar: mean short-term LUFS of an exported mix, if given


def build_grid(tracks: list[TrackActivity], song_beats: float, beats_per_bar: float,
               loudness_by_bar: list[float | None] | None = None) -> Grid:
    bars = max(1, math.ceil(song_beats / beats_per_bar - 1e-9))
    active: list[set[int]] = [set() for _ in range(bars)]
    notes = [0] * bars
    for i, t in enumerate(tracks):
        for start, end in t.clips:
            first = int(start // beats_per_bar)
            last = min(bars - 1, int(math.ceil(end / beats_per_bar - 1e-9)) - 1)
            for b in range(max(0, first), last + 1):
                # count the bar if the clip covers at least a quarter of it
                overlap = min(end, (b + 1) * beats_per_bar) - max(start, b * beats_per_bar)
                if overlap >= beats_per_bar / 4 - 1e-9:
                    active[b].add(i)
        for nt in t.note_times:
            b = int(nt // beats_per_bar)
            if 0 <= b < bars:
                notes[b] += 1
    weights = [sum(ROLE_WEIGHT.get(tracks[i].role, 0.9) for i in a) for a in active]
    max_w = max(weights) or 1.0
    max_n = max(notes) or 1
    loud = loudness_by_bar or [None] * bars
    valid = [v for v in loud if v is not None]
    lo, hi = (min(valid), max(valid)) if valid else (0.0, 0.0)
    energy = []
    for b in range(bars):
        e = 0.65 * weights[b] / max_w + 0.35 * notes[b] / max_n
        if loud[b] is not None and hi > lo:
            e = 0.5 * e + 0.5 * (loud[b] - lo) / (hi - lo)
        energy.append(round(e, 3))
    return Grid(bars, beats_per_bar, active, notes, energy, list(loud) + [None] * (bars - len(loud)))


def _jaccard_distance(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 0.0
    return 1 - len(a & b) / len(a | b)


def detect_boundaries(grid: Grid) -> list[int]:
    """0-based bar indices where sections start (always includes 0)."""
    # Smooth one-bar fills, pickups and drop-outs: a bar whose neighbours agree takes their state.
    active, energy = list(grid.active), list(grid.energy)
    for b in range(1, grid.bars - 1):
        if grid.active[b - 1] == grid.active[b + 1] != grid.active[b]:
            active[b] = grid.active[b - 1]
            energy[b] = (grid.energy[b - 1] + grid.energy[b + 1]) / 2
    scores = [0.0] + [_jaccard_distance(active[b - 1], active[b]) + abs(energy[b] - energy[b - 1])
                      for b in range(1, grid.bars)]
    candidates = [b for b in range(1, grid.bars) if scores[b] >= BOUNDARY]
    snapped = set()
    for b in candidates:
        nearest = round(b / PHRASE) * PHRASE
        snapped.add(nearest if abs(nearest - b) <= 1 and 0 < nearest < grid.bars else b)
    bounds = sorted({0} | snapped)
    # Merge sections shorter than MIN_SECTION into the more similar neighbour.
    changed = True
    while changed and len(bounds) > 1:
        changed = False
        for k, start in enumerate(bounds):
            end = bounds[k + 1] if k + 1 < len(bounds) else grid.bars
            if end - start >= MIN_SECTION:
                continue
            if k == 0:
                bounds.pop(1)
            elif k == len(bounds) - 1:
                bounds.pop(k)
            else:
                mean = lambda s, e: sum(grid.energy[s:e]) / max(1, e - s)
                prev_e = mean(bounds[k - 1], start)
                next_end = bounds[k + 2] if k + 2 < len(bounds) else grid.bars
                next_e = mean(end, next_end)
                here = mean(start, end)
                bounds.pop(k if abs(here - prev_e) <= abs(here - next_e) else k + 1)
            changed = True
            break
    return bounds


def label_sections(energies: list[float], trends: list[str] | None = None) -> list[str]:
    """intro / verse / build / drop / breakdown / outro from section energies (0..1).
    A section before a drop is a build only if it rises (when trends are known); else a verse."""
    n = len(energies)
    if n == 0:
        return []
    top = max(energies) or 1.0
    rel = [e / top for e in energies]
    labels = ["verse"] * n
    for k, r in enumerate(rel):
        if r >= 0.8:
            labels[k] = "drop"
    for k in range(n):
        if labels[k] == "drop":
            continue
        nxt = rel[k + 1] if k + 1 < n else None
        prev = rel[k - 1] if k > 0 else None
        rising = trends is None or trends[k] == "rising"
        if rel[k] < 0.55 and prev is not None and nxt is not None and prev >= 0.7 and k != n - 1:
            labels[k] = "breakdown"  # a dip right after a high section
        elif nxt is not None and labels[k + 1] == "drop" and rel[k] < nxt - 0.15 and rising:
            labels[k] = "build"
    if n > 1 and rel[0] < 0.8:
        labels[0] = "intro"
    if n > 2 and rel[-1] < 0.8 and rel[-1] <= rel[-2]:
        labels[-1] = "outro"
    counts: dict[str, int] = {}
    totals = {lab: labels.count(lab) for lab in set(labels)}
    out = []
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
        out.append(f"{lab} {counts[lab]}" if totals[lab] > 1 else lab)
    return out


def bar_ranges(bars: list[int]) -> str:
    """[0,1,2,5,6] (0-based) -> '1-3, 6-7' (1-based)."""
    if not bars:
        return ""
    out, start, prev = [], bars[0], bars[0]
    for b in bars[1:] + [None]:
        if b is not None and b == prev + 1:
            prev = b
            continue
        out.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
        if b is not None:
            start = prev = b
    return ", ".join(out)


def sections(grid: Grid, tracks: list[TrackActivity], bounds: list[int] | None = None,
             names: list[str] | None = None) -> list[dict]:
    bounds = bounds if bounds is not None else detect_boundaries(grid)
    spans = [(s, bounds[k + 1] if k + 1 < len(bounds) else grid.bars) for k, s in enumerate(bounds)]
    energies = [sum(grid.energy[s:e]) / max(1, e - s) for s, e in spans]
    labels = label_sections(energies, [_trend(grid.energy[s:e]) for s, e in spans])
    out, prev_set = [], set()
    for k, ((s, e), energy) in enumerate(zip(spans, energies)):
        playing = set().union(*grid.active[s:e]) if e > s else set()
        # "Core" parts play in at least half the section's bars.
        core = {i for i in playing if sum(i in grid.active[b] for b in range(s, e)) >= (e - s) / 2}
        entry = {
            "name": names[k] if names and k < len(names) else labels[k],
            "start_bar": s + 1, "end_bar": e, "bars": e - s,
            "energy": round(energy, 2),
            "trend": _trend(grid.energy[s:e]),
            "playing": [tracks[i].name for i in sorted(core)],
            "enters": [tracks[i].name for i in sorted(core - prev_set)] if k else None,
            "leaves": [tracks[i].name for i in sorted(prev_set - core)] if k else None,
            "notes_per_bar": round(sum(grid.notes[s:e]) / max(1, e - s), 1),
        }
        if names:
            entry["detected_as"] = labels[k]
        loud = [v for v in grid.loudness[s:e] if v is not None]
        if loud:
            entry["mix_lufs"] = round(sum(loud) / len(loud), 1)
        out.append(entry)
        prev_set = core
    return out


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "steady"
    half = len(values) // 2
    a, b = sum(values[:half]) / half, sum(values[half:]) / (len(values) - half)
    return "rising" if b - a > 0.1 else "falling" if a - b > 0.1 else "steady"


def observations(grid: Grid, tracks: list[TrackActivity], secs: list[dict], tempo: float) -> list[str]:
    obs = []
    # Long stretches where nothing enters or leaves.
    run_start = 0
    for b in range(1, grid.bars + 1):
        if b == grid.bars or grid.active[b] != grid.active[run_start]:
            length = b - run_start
            if length >= 16 and grid.active[run_start]:
                obs.append(f"Bars {run_start + 1}-{b} ({length} bars): the same {len(grid.active[run_start])} parts play "
                           "with no change. Bring something in or out every 8 bars to keep interest.")
            run_start = b
    energies = [s["energy"] for s in secs]
    if len(secs) >= 3 and max(energies) - min(energies) < 0.25:
        obs.append("Energy stays flat across sections; there's little contrast between parts of the song.")
    for k in range(1, len(secs)):
        if secs[k]["name"].startswith("drop") and secs[k]["energy"] - secs[k - 1]["energy"] < 0.1:
            obs.append(f"{secs[k]['name']} at bar {secs[k]['start_bar']} isn't prepared: the section before it is "
                       "almost as intense. A build or a short drop-out before it makes it land harder.")
    if secs and secs[0]["bars"] > max(16, 0.25 * grid.bars):
        obs.append(f"Intro is {secs[0]['bars']} bars ({secs[0]['bars'] / grid.bars:.0%} of the song).")
    if grid.bars >= 8 and grid.energy[-1] >= 0.75:
        obs.append("The song ends at full energy; an outro or a final break would let it resolve.")
    if len(secs) >= 4 and not any(s["name"].startswith("breakdown") for s in secs) and \
            sum(s["name"].startswith("drop") for s in secs) >= 2:
        obs.append("There are several drops but no breakdown between them.")
    for i, t in enumerate(tracks):
        share = sum(i in a for a in grid.active) / grid.bars
        if share >= 0.9 and grid.bars >= 32:
            obs.append(f"{t.name} plays in {share:.0%} of bars; giving it some rests would add contrast.")
    seconds = grid.bars * grid.beats_per_bar * 60 / tempo
    obs.append(f"Length: {grid.bars} bars, {int(seconds // 60)}:{int(seconds % 60):02d} at {tempo:g} BPM.")
    return obs
