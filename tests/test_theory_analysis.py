from ableton_mcp.theory import analysis, note_name, pitch_class


def n(pitch, start, dur=1.0, vel=100):
    return {"pitch": pitch, "start_time": start, "duration": dur, "velocity": vel}


def chord(pitches, start, dur=4.0):
    return [n(p, start, dur) for p in pitches]


def test_note_names_follow_live_convention():
    assert note_name(60) == "C3" and note_name(36) == "C1" and note_name(69) == "A3"
    assert pitch_class("Bb") == 10 and pitch_class("f#") == 6


def test_detect_key_c_major_scale():
    notes = [n(p, i) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72, 67, 64, 60])]
    assert analysis.detect_key(notes)["key"] == "C Major"


def test_detect_key_a_minor_progression():
    notes = (chord([57, 60, 64], 0) + chord([53, 57, 60], 4) + chord([52, 55, 59], 8) + chord([57, 60, 64], 12)
             + [n(45, 0, 4), n(45, 12, 4)])
    assert analysis.detect_key(notes)["key"] == "A Minor"


def test_identify_chords_with_inversions_and_sevenths():
    prog = chord([57, 60, 64], 0) + chord([53, 57, 60, 64], 4) + chord([52, 60, 67], 8) + chord([55, 59, 62, 65], 12)
    names = [c["chord"] for c in analysis.detect_chords(prog, 16.0)]
    assert names == ["Am", "Fmaj7", "C/E", "G7"]


def test_repeated_chords_merge():
    prog = chord([60, 64, 67], 0) + chord([60, 64, 67], 4)
    assert analysis.detect_chords(prog, 8.0) == [{"start": 0.0, "end": 8.0, "chord": "C"}]


def test_rhythm_grid_and_swing():
    straight = [n(36, i * 0.25, 0.1) for i in range(16)]
    assert analysis.rhythm_grid(straight)["grid"] == "1/16"
    triplets = [n(42, i / 3, 0.1) for i in range(12)]
    assert analysis.rhythm_grid(triplets)["grid"] == "1/8T"
    swung = [n(42, b + off, 0.1) for b in range(4) for off in (0.0, 0.62)]
    assert "swing_8th" in analysis.rhythm_grid(swung)


def test_step_pattern_accents():
    notes = [n(36, 0, 0.1, 120), n(36, 1, 0.1), n(38, 1, 0.1)]
    assert analysis.step_pattern(notes) == {36: "X...x...........", 38: "....x..........."}


def test_summary_polyphony():
    s = analysis.summarize(chord([60, 64, 67], 0), 4.0)
    assert s["max_polyphony"] == 3 and s["range"] == "C3–G3"
