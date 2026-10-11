"""Step-string rhythms and preset patterns.

A step string has one character per step (16th notes by default):
  x hit   X accent   g ghost   r roll (2 hits)   t triplet roll (3 hits)
  - hold the previous note through this step   . rest
Spaces and '|' (bar lines, as printed by read_clip) are ignored.
A pattern shorter than the clip repeats; longer patterns span several bars.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVELS = {"x": "normal", "X": "accent", "g": "ghost", "r": "normal", "t": "normal"}
HITS = {"r": 2, "t": 3}


@dataclass
class Event:
    start: float     # beats
    duration: float  # beats
    level: str       # normal | accent | ghost
    hits: int = 1    # >1 for rolls: evenly split within the step


def clean(pattern: str) -> str:
    out = "".join(ch for ch in pattern if ch not in " |\n\t")
    bad = set(out) - set("xXgrt-.")
    if bad:
        raise ValueError(f"Unknown step symbols {''.join(sorted(bad))!r} in {pattern!r}; use x X g r t - .")
    if not out:
        raise ValueError("Empty pattern")
    return out


def events(pattern: str, length_beats: float, steps_per_beat: int = 4) -> list[Event]:
    """Tile a step string over length_beats and return note events."""
    p = clean(pattern)
    step = 1.0 / steps_per_beat
    total = int(round(length_beats * steps_per_beat))
    out: list[Event] = []
    for i in range(total):
        ch = p[i % len(p)]
        if ch in LEVELS:
            out.append(Event(i * step, step, LEVELS[ch], HITS.get(ch, 1)))
        elif ch == "-" and out and abs(out[-1].start + out[-1].duration - i * step) < 1e-9:
            out[-1].duration += step
    return out


def fit_to_bar(pattern: str, steps_per_bar: int) -> str:
    """Adapt a 16-step 4/4 preset to other meters by cutting or repeating whole beats."""
    p = clean(pattern)
    if len(p) % steps_per_bar == 0 or steps_per_bar % len(p) == 0:
        return p
    return (p * (steps_per_bar // len(p) + 1))[:steps_per_bar]


CHORD_RHYTHMS = {
    "sustain": "x---------------",
    "half": "x-------x-------",
    "quarter": "x---x---x---x---",
    "eighths": "x-x-x-x-x-x-x-x-",
    "offbeat": "..x-..x-..x-..x-",
    "stabs": "x..x..x...x..x..",
    "syncopated": "x--x--x---x--x--",
    "charleston": "x-----x---------",
    "reggae": "....x-......x-..",
    "pulse16": "xxxxxxxxxxxxxxxx",
}

# Bass styles: (step pattern, note roles cycled per hit and restarted at each chord change).
# Roles: R root (or slash bass), 5 fifth, O octave, 3 chord third, 7 seventh (or octave), A approach next root.
BASS_STYLES: dict[str, tuple[str, list[str]]] = {
    "whole": ("x---------------", ["R"]),
    "half": ("x-------x-------", ["R"]),
    "pulse": ("x-x-x-x-x-x-x-x-", ["R"]),
    "eighths": ("x.x.x.x.x.x.x.x.", ["R"]),
    "offbeat": ("..x-..x-..x-..x-", ["R"]),
    "octaves": ("x.x.x.x.x.x.x.x.", ["R", "O"]),
    "root_fifth": ("x---x---x---x---", ["R", "5"]),
    "syncopated": ("x--x--x---x-x---", ["R", "R", "5", "R", "O"]),
    "funk": ("x..xx.x...x.x.x.", ["R", "O", "R", "5", "R", "O", "7"]),
    "arp": ("x.x.x.x.x.x.x.x.", ["R", "3", "5", "O"]),
    "walking": ("x---x---x---x---", ["R", "3", "5", "3"]),
    "sub": ("x---------------", ["R"]),
}

GM_DRUMS = {
    "kick": 36, "rim": 37, "snare": 38, "clap": 39, "closed_hat": 42, "pedal_hat": 44, "open_hat": 46,
    "low_tom": 45, "mid_tom": 47, "high_tom": 50, "crash": 49, "ride": 51, "tambourine": 54,
    "cowbell": 56, "conga": 63, "shaker": 82, "perc": 75,
}

# Keywords for finding a voice among Drum Rack pad names (first match wins, case-insensitive).
DRUM_KEYWORDS = {
    "kick": ["kick", "kik", "bd", "bass drum"],
    "snare": ["snare", "snr", "sd"],
    "clap": ["clap", "clp"],
    "closed_hat": ["closed", "chh", "ch ", "hihat", "hi-hat", "hi hat", "hh", "hat"],
    "open_hat": ["open", "ohh", "oh "],
    "pedal_hat": ["pedal"],
    "rim": ["rim", "stick", "side"],
    "ride": ["ride"],
    "crash": ["crash", "cymbal"],
    "shaker": ["shaker", "shk", "maraca"],
    "tambourine": ["tamb"],
    "low_tom": ["low tom", "floor", "tom l", "tom 1"],
    "mid_tom": ["mid tom", "tom m", "tom 2", "tom"],
    "high_tom": ["high tom", "hi tom", "tom h", "tom 3"],
    "cowbell": ["cowbell", "bell"],
    "conga": ["conga", "bongo"],
    "perc": ["perc", "clave", "block", "fx"],
}

DRUM_GENRES: dict[str, dict] = {
    "house": {"kick": "x...x...x...x...", "clap": "....x.......x...", "open_hat": "..x...x...x...x.",
              "closed_hat": "gg.ggg.ggg.ggg.g"},
    "techno": {"kick": "x...x...x...x...", "clap": "....x.......x...", "closed_hat": "xxXxxxXxxxXxxxXx",
               "rim": "...x..x....x..x."},
    "hiphop": {"kick": "x.....xx..x.....", "snare": "....x.......x...", "closed_hat": "x.x.x.x.x.x.x.x.",
               "swing": 0.56},
    "trap": {"kick": "x......x..x.....", "clap": "........x.......", "closed_hat": "xxxxxxxxxxxxrrxx",
             "open_hat": "..............x."},
    "dnb": {"kick": "x.........x.....", "snare": "....x..g....x...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "breakbeat": {"kick": "x.x.......x.....", "snare": "....x..g.g..x..g", "closed_hat": "x.x.x.x.x.x.x.x."},
    "garage": {"kick": "x......x..x.....", "snare": "....x.......x...", "closed_hat": "..x...x...x...x.",
               "shaker": "gxgxgxgxgxgxgxgx", "swing": 0.62},
    "lofi": {"kick": "x.....x...x..x..", "snare": "....x.......x...", "closed_hat": "x.xgx.xgx.xgx.xg",
             "swing": 0.58},
    "reggaeton": {"kick": "x...x...x...x...", "snare": "...x..x....x..x.", "closed_hat": "x.x.x.x.x.x.x.x."},
    "afro": {"kick": "x...x...x...x...", "clap": "....x.......x...", "rim": "..x..x....x..x..",
             "shaker": "gxgxgxgxgxgxgxgx", "conga": "x..x..x...x.x..."},
    "rock": {"kick": "x.......x.x.....", "snare": "....x.......x...", "closed_hat": "x.x.x.x.x.x.x.x."},
    "disco": {"kick": "x...x...x...x...", "snare": "....x.......x...", "open_hat": "..x...x...x...x.",
              "closed_hat": "x.x.x.x.x.x.x.x."},
}

ARP_RATES = {"1/4": 1.0, "1/8": 0.5, "1/8T": 1 / 3, "1/16": 0.25, "1/16T": 1 / 6, "1/32": 0.125}
