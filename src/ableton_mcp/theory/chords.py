"""Chord progressions: parse Roman numerals or chord symbols, voice them smoothly.

Progression syntax (tokens separated by spaces, '-', '|' or ','):
  Roman numerals relative to the key: "i VI III VII", "ii7 V7 Imaj7", "bVII", "vii°", "iiø7", "V/vi"
  Chord symbols: "Am F C/E G7", "F#m7b5", "Bbmaj9"
  A preset name: "pop", "epic", "jazz" ... (see PRESETS)
  Optional duration in bars after a colon: "Am:2 F:0.5 G:1.5" (default 1 bar each)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import NOTE_NAMES, pitch_class
from .analysis import CHORD_TEMPLATES
from .scales import heptatonic

CHORDS: dict[str, tuple[int, ...]] = {
    **CHORD_TEMPLATES,
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14),
    "m11": (0, 3, 7, 10, 14, 17), "11": (0, 4, 7, 10, 14, 17), "13": (0, 4, 7, 10, 14, 21),
    "7sus4": (0, 5, 7, 10), "6/9": (0, 4, 7, 9, 14), "maj7#11": (0, 4, 7, 11, 18), "7b9": (0, 4, 7, 10, 13),
}
SUFFIX_ALIASES = {
    "maj": "", "M": "", "min": "m", "M7": "maj7", "Δ": "maj7", "Δ7": "maj7", "min7": "m7", "ø": "m7b5", "ø7": "m7b5", "°": "dim", "o": "dim", "°7": "dim7", "o7": "dim7", "+": "aug",
    "sus": "sus4", "mmaj7": "mMaj7", "m(maj7)": "mMaj7", "add2": "add9", "69": "6/9",
}

PRESETS: dict[str, str] = {
    "pop": "I V vi IV",
    "sad": "vi IV I V",
    "epic": "i VI III VII",
    "dark": "i iv VI V",
    "andalusian": "i VII VI V",
    "jazz": "ii7 V7 Imaj7 Imaj7",
    "jazz_minor": "iiø7 V7 i7 i7",
    "lofi": "Imaj7 vi7 ii7 V7",
    "neo_soul": "ii9 V13 Imaj9 vi9",
    "house": "i VII VI VII",
    "deep_house": "i7 iv7 i7 VImaj7",
    "techno": "i i VI VII",
    "trap": "i VI iv V",
    "dreamy": "Imaj7 IVmaj7 Imaj7 IVmaj7",
    "gospel": "IV iii vi ii V",
    "blues": "I7:4 IV7:2 I7:2 V7 IV7 I7 V7",
    "royal_road": "IVmaj7 V7 iii7 vi",
    "axis_minor": "vi IV I V",
    "cinematic": "i bVI bIII bVII",
    "phrygian": "i bII i bII",
}

ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}
_ROMAN_RE = re.compile(r"^([b#]?)(vii|vi|v|iv|iii|ii|i|VII|VI|V|IV|III|II|I)(.*)$")
_SYMBOL_RE = re.compile(r"^([A-Ga-g][#b]?)(.*)$")


@dataclass
class Chord:
    name: str              # display name, e.g. "Am7" or "C/E"
    root: int              # pitch class
    intervals: tuple[int, ...]
    bass: int | None = None  # pitch class of a slash bass
    bars: float = 1.0

    def pitch_classes(self) -> set[int]:
        return {(self.root + i) % 12 for i in self.intervals}


def _suffix(raw: str) -> str:
    s = SUFFIX_ALIASES.get(raw, raw)
    if s not in CHORDS:
        raise ValueError(f"Unknown chord quality {raw!r}. Known: {', '.join(k or 'maj' for k in CHORDS)}")
    return s


def _parse_roman(token: str, key_root: int, scale: tuple[int, ...]) -> Chord | None:
    m = _ROMAN_RE.match(token)
    if not m:
        return None
    accidental, numeral, rest = m.groups()
    degree = ROMAN[numeral.lower()]
    # Plain numerals follow the key's scale; b/# numerals (borrowed chords) are relative to major.
    reference = heptatonic(scale) if not accidental else (0, 2, 4, 5, 7, 9, 11)
    root = (key_root + reference[degree] + {"b": -1, "#": 1, "": 0}[accidental]) % 12
    if rest in ("°", "o", "dim"):
        return Chord(token, root, CHORDS["dim"])
    if rest in ("°7", "o7", "dim7"):
        return Chord(token, root, CHORDS["dim7"])
    if rest in ("ø", "ø7", "m7b5"):
        return Chord(token, root, CHORDS["m7b5"])
    if rest == "+":
        return Chord(token, root, CHORDS["aug"])
    if numeral.islower():
        minor_suffix = {"": "m", "7": "m7", "9": "m9", "11": "m11", "6": "m6", "add9": "madd9", "maj7": "mMaj7"}
        if rest not in minor_suffix:
            raise ValueError(f"Can't read {token!r}: lowercase numerals are minor; use e.g. ii7, vi9, or uppercase for major")
        return Chord(token, root, CHORDS[minor_suffix[rest]])
    return Chord(token, root, CHORDS[_suffix(rest)])


def _parse_symbol(token: str) -> Chord:
    main, _, bass = token.partition("/")
    m = _SYMBOL_RE.match(main)
    if not m:
        raise ValueError(f"Can't read chord {token!r}")
    root_name, quality = m.groups()
    root = pitch_class(root_name)
    chord = Chord(token, root, CHORDS[_suffix(quality)])
    if bass:
        chord.bass = pitch_class(bass)
    return chord


def parse_progression(spec: str, key_root: int, scale: tuple[int, ...], bars_per_chord: float = 1.0) -> list[Chord]:
    """Parse a progression string (or preset name) into chords with durations in bars."""
    spec = PRESETS.get(spec.strip().lower().replace(" ", "_").replace("-", "_"), spec)
    chords = []
    for token in filter(None, re.split(r"[\s|,\-]+", spec.strip())):
        token, _, dur = token.partition(":")
        if not token:
            continue
        # Secondary dominants like V/vi: build V of the target degree.
        if "/" in token and _ROMAN_RE.match(token.split("/")[1] or "x"):
            head, target = token.split("/", 1)
            tchord = _parse_roman(target, key_root, scale)
            chord = _parse_roman(head, tchord.root, (0, 2, 4, 5, 7, 9, 11))
            chord.name = token
        else:
            chord = _parse_roman(token, key_root, scale) or _parse_symbol(token)
        chord.bars = float(dur) if dur else bars_per_chord
        chords.append(chord)
    if not chords:
        raise ValueError("Empty progression")
    return chords


FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def prefers_flats(key_root: int, scale: tuple[int, ...]) -> bool:
    """Flat keys: F Bb Eb Ab Db (major) and their relative minors D G C F Bb."""
    relative_major = key_root if 4 in heptatonic(scale)[:3] else (key_root + 3) % 12
    return relative_major in (5, 10, 3, 8, 1)


def chord_label(chord: Chord, flats: bool = False) -> str:
    """Absolute chord symbol, e.g. 'Am7' for 'vi7' in C, 'Bb' for 'bVII' in C."""
    names = FLAT_NAMES if flats or chord.name.startswith("b") or "b" in chord.name[1:3] else NOTE_NAMES
    suffix = next((k for k, v in CHORDS.items() if v == chord.intervals), "?")
    label = names[chord.root] + suffix
    if chord.bass is not None and chord.bass != chord.root:
        label += "/" + names[chord.bass]
    return label


# -- voicing ------------------------------------------------------------------------

def _candidates(chord: Chord, low: int, high: int, style: str) -> list[tuple[int, ...]]:
    tones = sorted({i % 12 for i in chord.intervals})  # close position: extensions fold into the octave
    if len(tones) > 5 and 7 in tones:
        tones.remove(7)  # the 5th is the first note to leave a crowded chord
    out = set()
    for inv in range(len(tones)):
        order = tones[inv:] + [t + 12 for t in tones[:inv]]
        for octave in range(-1, 10):
            base = chord.root + 12 * octave
            stack = sorted(base + t for t in order)
            if style == "open" and len(stack) >= 4:
                stack = sorted(stack[:-2] + [stack[-2] - 12] + stack[-1:])  # drop-2
            elif style == "spread" and len(stack) >= 3:
                stack = sorted([stack[0] - 12] + stack[1:] + ([stack[0] + 12] if len(stack) < 5 else []))
            if stack[0] >= low and stack[-1] <= high:
                out.add(tuple(stack))
    return sorted(out)


def _distance(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    if len(a) == len(b):
        return sum(abs(x - y) for x, y in zip(a, b))
    return sum(min(abs(x - y) for y in b) for x in a) + sum(min(abs(x - y) for x in a) for y in b)


def voice_progression(chords: list[Chord], low: int = 52, high: int = 79, style: str = "close",
                      bass_octave: int | None = None) -> list[list[int]]:
    """Pick a voicing per chord with smooth voice leading inside [low, high].

    style: close | open (drop-2) | spread. bass_octave adds the root (or slash bass)
    in that MIDI octave below the voicing (e.g. 2 -> notes 36..47 in Live's C1=36 naming).
    """
    center = (low + high) / 2
    voicings: list[list[int]] = []
    prev: tuple[int, ...] | None = None
    for chord in chords:
        options = _candidates(chord, low, high, style) or _candidates(chord, low - 12, high + 12, "close")
        if not options:
            raise ValueError(f"Can't voice {chord.name} between {low} and {high}")
        def cost(v: tuple[int, ...]) -> float:
            c = abs(sum(v) / len(v) - center) * (1.0 if prev is None else 0.3)
            if prev is not None:
                c += _distance(prev, v)
            if chord.bass is not None and bass_octave is None and v[0] % 12 != chord.bass:
                c += 12  # slash chords: put the named bass note at the bottom when possible
            return c

        choice = min(options, key=cost)
        notes = list(choice)
        if bass_octave is not None:
            pc = chord.bass if chord.bass is not None else chord.root
            notes = [12 * bass_octave + pc] + notes
        voicings.append(notes)
        prev = choice
    return voicings


def chord_spans(chords: list[Chord], beats_per_bar: float) -> list[tuple[float, float, Chord]]:
    """(start, end, chord) in beats for one pass through the progression."""
    spans, t = [], 0.0
    for c in chords:
        spans.append((t, t + c.bars * beats_per_bar, c))
        t += c.bars * beats_per_bar
    return spans

