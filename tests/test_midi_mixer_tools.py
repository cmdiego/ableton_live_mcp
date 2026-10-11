"""Phase 2 and 3 tools against the fake set."""

import pytest

from AbletonMCP.handlers import parse_display
from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import midi, mixer

from .fake_live import Clip, MidiNoteSpecification as N


def test_parse_display_units():
    assert parse_display("-6.0 dB") == -6.0
    assert parse_display("1.20 kHz") == 1200.0
    assert parse_display("2.00 s") == 2000.0
    assert parse_display("25L") == -25.0 and parse_display("C") == 0.0 and parse_display("10R") == 10.0
    assert parse_display("-inf dB") == float("-inf")
    assert parse_display("Off") is None


def test_clip_notes_rows(client, song):
    clip = song.tracks[1].clip_slots[0].clip
    clip.add_new_notes([N(45, 0.0, 0.5, 110), N(48, 1.0, 0.5, 90, mute=True)])
    r = midi.get_clip_notes("bass", slot=0)
    assert r["notes"] == [[45, "A1", 0.0, 0.5, 110], [48, "C2", 1.0, 0.5, 90]]
    assert r["muted_rows"] == [1]


def test_arrangement_clip_addressing(client, song):
    song.tracks[1].arrangement_clips[0].add_new_notes([N(40, 2.0, 1.0)])
    assert midi.get_clip_notes("bass", arrangement_clip=0)["notes"][0][0] == 40
    with pytest.raises(LiveError, match="either slot"):
        midi.get_clip_notes("bass")


def test_analyze_harmony(client, song):
    clip = song.tracks[1].clip_slots[0].clip  # 8 beats = 2 bars
    clip.add_new_notes([N(p, 0.0, 4.0) for p in (57, 60, 64)] + [N(p, 4.0, 4.0) for p in (53, 57, 60)])
    r = midi.analyze_clip_midi("bass", slot=0)
    assert [c["chord"] for c in r["chords"]] == ["Am", "F"]
    assert r["song_key"] == "A Minor"
    assert r["summary"]["max_polyphony"] == 3


def test_analyze_drums_uses_pad_names(client, song):
    slot = song.tracks[0].clip_slots[0]
    slot.clip = Clip(4.0)
    slot.clip.add_new_notes([N(36, b, 0.1) for b in range(4)] + [N(38, 1.0, 0.1, 120), N(38, 3.0, 0.1, 120)])
    r = midi.analyze_clip_midi("drums", slot=0)
    assert r["drum_patterns"] == {"Kick (36)": "x...x...x...x...", "Snare (38)": "....X.......X..."}
    assert "chords" not in r


def test_set_mixer_volume_pan_sends(client, song):
    r = mixer.set_mixer("bass", volume_db=-6, pan="25L", sends={"A": -12, "delay": -20})
    assert r["volume"] == "-6.0 dB" and r["pan"] == "25L"
    assert r["sends"] == {"A-Reverb": "-12.0 dB", "B-Delay": "-20.0 dB"}
    r = mixer.set_mixer("bass", change_db=-3)
    assert r["volume"] == "-9.0 dB"


def test_set_mixer_clamps_to_range(client, song):
    assert mixer.set_mixer("vox", volume_db=20)["volume"] == "6.0 dB"


def test_mix_state_lists_sends_by_return_name(client, song):
    mixer.set_mixer("drums", sends={"reverb": -10})
    state = mixer.get_mix_state()
    assert state["tracks"][0]["sends"] == {"A-Reverb": "-10.0 dB"}
    assert state["master"]["name"] == "Main"
