"""Phase 4 tools against the fake set (song key: A minor; Drums track has a Drum Rack)."""

import pytest

from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import compose, midi, session


def test_write_chords_into_first_empty_slot(client, song):
    r = compose.write_chords("bass", "i VI III VII")
    assert r["clip"] == "song.tracks.1.clip_slots.1.clip"  # slot 0 already holds "Bassline"
    assert r["key"] == "A Minor" and r["chords"] == ["Am", "F", "C", "G"]
    clip = song.tracks[1].clip_slots[1].clip
    assert clip.length == 16 and clip.name == "Am F C G" and len(clip.notes) == 12


def test_never_overwrites_without_mode(client, song):
    with pytest.raises(LiveError, match="already has a clip"):
        compose.write_chords("bass", "pop", slot=0)
    compose.write_chords("bass", "pop", key="C", slot=0, mode="replace")
    assert {n.pitch % 12 for n in song.tracks[1].clip_slots[0].clip.notes} <= {0, 2, 4, 5, 7, 9, 11}


def test_replace_extends_short_clip(client, song):
    compose.write_chords("bass", "i iv", bars=4, slot=0, mode="replace")
    clip = song.tracks[1].clip_slots[0].clip
    assert clip.end_marker == 16 and clip.loop_end == 16


def test_audio_track_rejected(client):
    with pytest.raises(LiveError, match="not a MIDI track"):
        compose.write_chords("vox", "pop")


def test_drums_use_rack_pad_names(client, song):
    r = compose.write_drums("drums", genre="house", bars=1)
    assert r["pads"]["kick"] == "Kick (36)" and r["pads"]["closed_hat"] == "Closed Hat (42)"
    assert "open_hat" in r["no_matching_pad"] and "clap" in r["no_matching_pad"]
    pitches = {n.pitch for n in song.tracks[0].clip_slots[0].clip.notes}
    assert {36, 42} <= pitches


def test_fill_uses_snare_pad_even_if_groove_has_none(client, song):
    compose.write_drums("drums", genre="house", bars=2, fill_every=2)
    last_beat = [n for n in song.tracks[0].clip_slots[0].clip.notes if n.start_time >= 7]
    assert [n.pitch for n in last_beat if n.pitch == 38] == [38, 38, 38, 38]


def test_drums_custom_pattern_only(client, song):
    r = compose.write_drums("drums", genre="none", pattern={"kick": "x.x.|x.x.", "snare": "....x..."}, bars=1)
    assert set(r["pattern"]) == {"kick", "snare"}
    with pytest.raises(LiveError, match="Unknown genre"):
        compose.write_drums("drums", genre="polka")


def test_bassline_follows_existing_chord_clip(client, song):
    compose.write_chords("bass", "Am F C G", slot=1)
    session.create("track", "midi", name="Sub")
    r = compose.write_bassline("sub", chords_from_track="bass", chords_from_slot=1, style="whole")
    assert [c.split("/")[0] for c in r["chords"]] == ["Am", "F", "C", "G"]
    roots = [n.pitch % 12 for n in sorted(song.tracks[3].clip_slots[0].clip.notes, key=lambda n: n.start_time)]
    assert roots == [9, 5, 0, 7]


def test_bassline_needs_harmony(client):
    with pytest.raises(LiveError, match="Give a progression"):
        compose.write_bassline("bass")


def test_arpeggio(client, song):
    r = compose.write_arpeggio("bass", "i VI", style="updown", rate="1/8", octaves=2)
    assert r["notes"] == 16 and r["length_beats"] == 8


def test_write_notes_roundtrips_get_clip_notes(client, song):
    compose.write_notes("bass", [["A2", 0, 1, 110], [60, 1, 0.5], {"pitch": "E3", "start": 2, "duration": 1}], slot=2)
    rows = midi.get_clip_notes("bass", slot=2)["notes"]
    assert rows == [[57, "A2", 0.0, 1.0, 110], [60, "C3", 1.0, 0.5, 100], [64, "E3", 2.0, 1.0, 100]]
    compose.write_notes("bass", rows, slot=3)  # rows straight from get_clip_notes
    assert midi.get_clip_notes("bass", slot=3)["notes"] == rows


def test_arrangement_destination_and_overlap(client, song):
    r = compose.write_chords("bass", "i iv", arrangement_start_beat=32)
    assert r["clip"].startswith("song.tracks.1.arrangement_clips.") and r["created"]
    with pytest.raises(LiveError, match="overlaps"):
        compose.write_chords("bass", "i iv", arrangement_start_beat=8)  # "Bass Arr" spans 0-16


def test_transform_notes(client, song):
    compose.write_notes("bass", [[57, 0, 1], [60, 1, 1], [64, 2, 1]], slot=2)
    compose.transform_notes("bass", [{"op": "transpose", "semitones": 12}], slot=2, pitch_range=["C3", "E3"])
    assert sorted(n.pitch for n in song.tracks[1].clip_slots[2].clip.notes) == [57, 72, 76]
    compose.transform_notes("bass", [{"op": "transpose_diatonic", "steps": 1}], slot=2, pitch_range=[57, 57])
    assert sorted(n.pitch for n in song.tracks[1].clip_slots[2].clip.notes) == [59, 72, 76]  # A -> B in A minor
    compose.transform_notes("bass", [{"op": "remove"}], slot=2, start_beat=1)
    assert len(song.tracks[1].clip_slots[2].clip.notes) == 1
    with pytest.raises(LiveError, match="Unknown op"):
        compose.transform_notes("bass", [{"op": "explode"}], slot=2)


def test_set_song_rejects_unknown_scale(client, song):
    with pytest.raises(Exception, match="Live scales"):
        session.set_song(scale="bebop")
    session.set_song(scale="aeolian")
    assert song.scale_name == "Minor"
