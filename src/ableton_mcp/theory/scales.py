"""Scales by Live's own names (verified against Live 12.4 `song.scale_intervals`)."""

from __future__ import annotations

LIVE_SCALES: dict[str, tuple[int, ...]] = {
    "Major": (0, 2, 4, 5, 7, 9, 11),
    "Minor": (0, 2, 3, 5, 7, 8, 10),
    "Dorian": (0, 2, 3, 5, 7, 9, 10),
    "Mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "Lydian": (0, 2, 4, 6, 7, 9, 11),
    "Phrygian": (0, 1, 3, 5, 7, 8, 10),
    "Locrian": (0, 1, 3, 5, 6, 8, 10),
    "Harmonic Minor": (0, 2, 3, 5, 7, 8, 11),
    "Melodic Minor": (0, 2, 3, 5, 7, 9, 11),
    "Phrygian Dominant": (0, 1, 4, 5, 7, 8, 10),
    "Hungarian Minor": (0, 2, 3, 6, 7, 8, 11),
    "Minor Pentatonic": (0, 3, 5, 7, 10),
    "Major Pentatonic": (0, 2, 4, 7, 9),
    "Minor Blues": (0, 3, 5, 6, 7, 10),
    "Whole Tone": (0, 2, 4, 6, 8, 10),
}

ALIASES = {
    "ionian": "Major", "maj": "Major", "aeolian": "Minor", "natural minor": "Minor", "min": "Minor",
    "harmonic": "Harmonic Minor", "melodic": "Melodic Minor", "pentatonic": "Minor Pentatonic",
    "blues": "Minor Blues",
}


def live_scale_name(name: str) -> str:
    """Canonical Live scale name for user input ('aeolian' -> 'Minor'); raises if unknown."""
    key = name.strip().lower()
    for canonical in LIVE_SCALES:
        if canonical.lower() == key:
            return canonical
    if key in ALIASES:
        return ALIASES[key]
    raise ValueError(f"Unknown scale {name!r}. Live scales: {', '.join(LIVE_SCALES)}")


def heptatonic(intervals: tuple[int, ...]) -> tuple[int, ...]:
    """A 7-note scale for Roman numerals: the scale itself, or its major/minor parent."""
    if len(intervals) == 7:
        return intervals
    return LIVE_SCALES["Minor"] if 3 in intervals else LIVE_SCALES["Major"]


def snap_to_scale(pitch: int, root: int, intervals: tuple[int, ...], direction: int = 0) -> int:
    """Nearest pitch in the scale (ties go down unless direction=+1)."""
    pcs = {(root + i) % 12 for i in intervals}
    if pitch % 12 in pcs:
        return pitch
    for d in range(1, 7):
        candidates = [pitch + d, pitch - d] if direction > 0 else [pitch - d, pitch + d]
        for c in candidates:
            if c % 12 in pcs:
                return c
    return pitch


def scale_step(pitch: int, steps: int, root: int, intervals: tuple[int, ...]) -> int:
    """Move a pitch by scale degrees (diatonic transpose). Off-scale pitches snap first."""
    pitch = snap_to_scale(pitch, root, intervals)
    rel = (pitch - root) % 12
    degree = list(intervals).index(rel)
    octave = (pitch - root - rel) // 12
    n = len(intervals)
    target = degree + steps
    return root + (octave + target // n) * 12 + intervals[target % n]
