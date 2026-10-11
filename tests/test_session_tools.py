"""Phase 1 tools against the fake set."""

import pytest

from ableton_mcp import resolve
from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import session


def test_overview_shape(client):
    o = session.get_session_overview(include_arrangement=True)
    assert o["song"]["key"] == "A Minor"
    assert o["song"]["time_signature"] == "4/4"
    assert [t["kind"] for t in o["tracks"]] == ["midi", "midi", "audio"]
    bass = o["tracks"][1]
    assert bass["clips"] == [{"slot": 0, "name": "Bassline", "length_beats": 8.0, "type": "midi", "color": "#ff0000"}]
    assert bass["arrangement_clips"][0]["end"] == 16.0
    assert o["return_tracks"][0]["kind"] == "return"
    assert o["locators"][1] == {"name": "Drop", "time": 32.0}
    assert "mute" not in o["tracks"][0]  # False values are compacted away


def test_resolve_names(client):
    assert resolve.track_path(client, "bass") == "song.tracks.1"
    assert resolve.track_path(client, "Drms") == "song.tracks.0"  # fuzzy
    assert resolve.track_path(client, "Return B") == "song.return_tracks.1"
    assert resolve.track_path(client, "reverb") == "song.return_tracks.0"
    assert resolve.track_path(client, "master") == "song.master_track"
    assert resolve.track_path(client, 2) == "song.tracks.2"
    with pytest.raises(LiveError, match="No track matches"):
        resolve.track_path(client, "synth lead")


def test_ambiguous_name(client, song):
    song.tracks.append(song.tracks[0].__class__("Bass 2"))
    with pytest.raises(LiveError, match="ambiguous"):
        resolve.track_path(client, "bas")


def test_set_track_and_color(client, song):
    session.set_track("vox", name="Lead Vox", color="blue", mute=True)
    assert (song.tracks[2].name, song.tracks[2].mute, song.tracks[2].color) == ("Lead Vox", True, 0x1065E0)


def test_create_and_delete_track(client, song):
    r = session.create("track", "midi", name="Keys", color="#00ff00")
    assert r == {"created": "song.tracks.3", "name": "Keys"}
    with pytest.raises(LiveError, match="confirm=true"):
        session.delete("track", track="keys")
    session.delete("track", track="keys", confirm=True)
    assert [t.name for t in song.tracks] == ["Drums", "Bass", "Vox"]


def test_set_clip_loop_order(client, song):
    session.set_clip("bass", 0, loop_start=10.0, loop_end=12.0, name="B")
    clip = song.tracks[1].clip_slots[0].clip
    assert (clip.loop_start, clip.loop_end, clip.name) == (10.0, 12.0, "B")


def test_set_song_key_and_signature(client, song):
    session.set_song(tempo=140, time_signature="6/8", key="F#", scale="Dorian")
    assert (song.tempo, song.signature_numerator, song.signature_denominator) == (140.0, 6, 8)
    assert (song.root_note, song.scale_name) == (6, "Dorian")


def test_transport_fire_undo(client, song):
    session.playback("play")
    assert song.is_playing
    session.playback("fire_scene", scene="scene 2")
    assert song.scenes[1].fired
    assert session.undo(steps=3) == {"undo": 1}  # fake can_undo flips to False after one


def test_duplicate_clip_default_target(client, song):
    session.duplicate("clip", track="bass", slot=0)
    assert song.tracks[1].clip_slots[1].clip.name == "Bassline"


def test_delete_and_duplicate_scene(client, song):
    session.duplicate("scene", scene="scene 2")
    assert [s.name for s in song.scenes][:3] == ["Scene 1", "Scene 2", "Scene 2"]
    with pytest.raises(LiveError, match="confirm=true"):
        session.delete("scene", scene=2)
    session.delete("scene", scene=2, confirm=True)
    assert len(song.scenes) == 4


def test_delete_clip_needs_track_and_slot(client, song):
    with pytest.raises(LiveError, match="slot is required"):
        session.delete("clip", track="bass", confirm=True)
    session.delete("clip", track="bass", slot=0, confirm=True)
    assert not song.tracks[1].clip_slots[0].has_clip
