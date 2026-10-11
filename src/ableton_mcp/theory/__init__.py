"""Music theory helpers: pitch names, scales, chords, progressions, rhythms."""

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"}


def pitch_class(name: str) -> int:
    """'C' -> 0, 'f#' -> 6, 'Bb' -> 10."""
    n = name.strip()
    n = n[0].upper() + n[1:]
    n = FLAT_TO_SHARP.get(n, n)
    if n not in NOTE_NAMES:
        raise ValueError(f"Unknown note name {name!r}")
    return NOTE_NAMES.index(n)


def note_name(pitch: int) -> str:
    """MIDI pitch -> name with octave, using Live's convention (60 = C3)."""
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 2}"


def parse_pitch(value: int | str) -> int:
    """MIDI number or a name in Live's convention: 'C3' = 60, 'F#1' = 42, 'Bb-1' = 22."""
    if isinstance(value, int) or (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
        pitch = int(value)
    else:
        v = value.strip()
        split = 2 if len(v) > 1 and v[1] in "#b" else 1
        pitch = pitch_class(v[:split]) + 12 * (int(v[split:]) + 2)
    if not 0 <= pitch <= 127:
        raise ValueError(f"Pitch {value!r} is outside MIDI range 0..127")
    return pitch
