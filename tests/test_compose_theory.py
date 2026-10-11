"""Phase 4 theory: progressions, voicing, generators, transforms."""

import random

import pytest

from ableton_mcp.theory import analysis, generate, parse_pitch, patterns
from ableton_mcp.theory import transform as tf
from ableton_mcp.theory.chords import PRESETS, chord_label, parse_progression, voice_progression
from ableton_mcp.theory.scales import LIVE_SCALES, live_scale_name, scale_step

MAJOR, MINOR = LIVE_SCALES["Major"], LIVE_SCALES["Minor"]


def labels(spec, root=0, scale=MAJOR):
    return [chord_label(c) for c in parse_progression(spec, root, scale)]


def test_roman_numerals_follow_the_key_scale():
    assert labels("i VI III VII", 9, MINOR) == ["Am", "F", "C", "G"]
    assert labels("ii7 V7 Imaj7") == ["Dm7", "G7", "Cmaj7"]
    assert labels("vii° iiø7 I+") == ["Bdim", "Dm7b5", "Caug"]


def test_borrowed_and_secondary_chords():
    assert labels("I bVII bVI") == ["C", "Bb", "Ab"]
    assert labels("i bVI", 2, MINOR) == ["Dm", "Bb"]  # flats are relative to major
    assert labels("V/vi vi") == ["E", "Am"]


def test_symbols_slash_and_durations():
    chords = parse_progression("Am:2 F:0.5 C/E G7", 0, MAJOR)
    assert [chord_label(c) for c in chords] == ["Am", "F", "C/E", "G7"]
    assert [c.bars for c in chords] == [2, 0.5, 1, 1]


def test_every_preset_parses_in_major_and_minor():
    for name in PRESETS:
        assert parse_progression(name, 0, MAJOR) and parse_progression(name, 9, MINOR)


def test_bad_tokens_raise_readable_errors():
    with pytest.raises(ValueError, match="quality"):
        parse_progression("Cfoo", 0, MAJOR)
    with pytest.raises(ValueError, match="lowercase numerals are minor"):
        parse_progression("vimaj9x", 0, MAJOR)


def test_voice_leading_is_smooth_and_in_register():
    voicings = voice_progression(parse_progression("pop", 0, MAJOR), 52, 79)
    for a, b in zip(voicings, voicings[1:]):
        assert max(abs(x - y) for x, y in zip(sorted(a), sorted(b))) <= 5
    assert all(52 <= p <= 79 for v in voicings for p in v)


def test_slash_chord_bass_on_bottom():
    v = voice_progression(parse_progression("Am F C/E G", 0, MAJOR))
    assert v[2][0] % 12 == 4


def test_generated_chords_are_recognised_again():
    chords = parse_progression("ii7 V7 Imaj7 vi7", 0, MAJOR)
    notes = generate.chords_notes(chords, 16, 4, patterns.CHORD_RHYTHMS["stabs"])
    found = [c["chord"].split("/")[0] for c in analysis.detect_chords(notes, 16)]
    assert found == ["Dm7", "G7", "Cmaj7", "Am7"]


def test_sustained_chords_retrigger_on_chord_change():
    chords = parse_progression("Am:0.5 F:0.5", 0, MAJOR)
    notes = generate.chords_notes(chords, 4, 4, "x---------------")
    assert sorted({n["start_time"] for n in notes}) == [0.0, 2.0]


def test_progression_repeats_to_fill_length():
    notes = generate.chords_notes(parse_progression("I V", 0, MAJOR), 16, 4, "sustain".replace("sustain", "x---------------"))
    assert len({n["start_time"] for n in notes}) == 4


def test_walking_bass_approaches_next_root():
    notes = generate.bass_notes(parse_progression("C F", 0, MAJOR), 8, 4, "walking")
    pitches = [n["pitch"] for n in sorted(notes, key=lambda n: n["start_time"])]
    assert pitches[0] % 12 == 0 and pitches[4] % 12 == 5
    assert abs(pitches[3] - pitches[4]) == 1  # chromatic approach into F


def test_bass_styles_and_register():
    for style in patterns.BASS_STYLES:
        notes = generate.bass_notes(parse_progression("i iv", 9, MINOR), 8, 4, style)
        assert notes and all(28 <= n["pitch"] <= 60 for n in notes)


def test_arp_modes():
    chords = parse_progression("C", 0, MAJOR)
    up = [n["pitch"] for n in generate.arp_notes(chords, 1, 4, "up", "1/16")]
    assert up == [64, 67, 72, 64]  # 4 sixteenths over a 3-note voicing wrap around
    order = [n["pitch"] for n in generate.arp_notes(chords, 1, 4, "up", "1/16", order=[1, 3, 2, 4])]
    assert order[0] < order[2] < order[1] and order[3] == order[0] + 12


def test_drum_rolls_and_fill():
    notes = generate.drum_notes({"closed_hat": "r..............."}, {"closed_hat": 42, "snare": 38, "kick": 36}, 4, 4)
    assert [n["start_time"] for n in notes] == [0.0, 0.125]
    filled = generate.drum_notes({"kick": "x...x...x...x...", "closed_hat": "xxxxxxxxxxxxxxxx"},
                                 {"kick": 36, "closed_hat": 42, "snare": 38}, 8, 4, fill_every=2)
    last_beat = [n for n in filled if n["start_time"] >= 7]
    assert {n["pitch"] for n in last_beat} == {36, 38}


def test_step_strings_ignore_bar_lines_and_reject_junk():
    assert len(patterns.events("x...|x...", 2)) == 2
    with pytest.raises(ValueError, match="Unknown step symbols"):
        patterns.events("x..q", 1)


def test_transforms():
    n = lambda p, t, d=0.25, v=100: {"pitch": p, "start_time": t, "duration": d, "velocity": v}
    assert [x["start_time"] for x in tf.quantize([n(60, 0.27), n(60, 0.49)], "1/8")] == [0.5, 0.5]
    assert tf.swing([n(60, 0.25)], 0.66)[0]["start_time"] == pytest.approx(0.33)
    assert [x["duration"] for x in tf.legato([n(60, 0), n(62, 1)])][0] == 1.0
    assert tf.transpose_diatonic([n(64, 0)], 2, 0, MAJOR)[0]["pitch"] == 67  # E -> G
    assert tf.reverse([n(60, 0, 1)], 4)[0]["start_time"] == 3
    assert tf.velocity([n(60, 0), n(60, 1)], ramp=(40, 120))[1]["velocity"] == 120
    h = tf.humanize([n(60, 1)] * 20, 0.02, 10, random.Random(1))
    assert all(abs(x["start_time"] - 1) <= 0.02 for x in h) and len({x["velocity"] for x in h}) > 1


def test_scales_and_pitches():
    assert live_scale_name("aeolian") == "Minor" and live_scale_name("dorian") == "Dorian"
    with pytest.raises(ValueError, match="Live scales"):
        live_scale_name("bebop")
    assert scale_step(71, 1, 0, MAJOR) == 72 and scale_step(61, 0, 0, MAJOR) == 60
    assert parse_pitch("C3") == 60 and parse_pitch("F#1") == 42 and parse_pitch("Bb-1") == 22 and parse_pitch(64) == 64
