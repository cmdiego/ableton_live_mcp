"""Phase 8: arrangement analysis (pure) and editing (fake Live)."""

import pytest

from ableton_mcp.live_client import LiveError
from ableton_mcp.theory import arrangement as arr
from ableton_mcp.tools import arrangement as A
from ableton_mcp.tools import compose, session

BPB = 4.0


def song_layout():
    """intro (pad) 8 | verse (pad+bass) 8 | drop (all) 16 | outro (pad) 8 bars."""
    bar = lambda b: b * BPB
    pad = arr.TrackActivity("Pad", "pad", [(0, bar(40))])
    bass = arr.TrackActivity("Bass", "bass", [(bar(8), bar(32))], [bar(b) + k for b in range(8, 32) for k in (0, 2)])
    drums = arr.TrackActivity("Drums", "drums", [(bar(16), bar(32))], [bar(b) + k * 0.5 for b in range(16, 32) for k in range(8)])
    return [pad, bass, drums], bar(40)


def test_sections_are_detected_and_labelled():
    tracks, end = song_layout()
    grid = arr.build_grid(tracks, end, BPB)
    secs = arr.sections(grid, tracks)
    assert [(s["name"], s["start_bar"], s["end_bar"]) for s in secs] == \
           [("intro", 1, 8), ("verse", 9, 16), ("drop", 17, 32), ("outro", 33, 40)]
    assert secs[2]["enters"] == ["Drums"] and secs[3]["leaves"] == ["Bass", "Drums"]


def test_rising_section_before_drop_is_a_build():
    assert arr.label_sections([0.2, 0.5, 1.0], ["steady", "rising", "steady"]) == ["intro", "build", "drop"]
    assert arr.label_sections([0.2, 0.5, 1.0], ["steady", "steady", "steady"]) == ["intro", "verse", "drop"]
    assert arr.label_sections([0.3, 1.0, 0.4, 1.0, 0.2]) == ["intro", "drop 1", "breakdown", "drop 2", "outro"]


def test_boundaries_snap_to_phrases_and_short_sections_merge():
    tracks = [arr.TrackActivity("A", "pad", [(0, 64)]), arr.TrackActivity("B", "drums", [(36, 40), (44, 64)])]
    grid = arr.build_grid(tracks, 64, BPB)
    # B has a one-bar pickup at bar 10, drops out at bar 11 and enters for good at bar 12:
    # the pickup/drop-out are smoothed away, leaving one boundary where B really enters.
    assert arr.detect_boundaries(grid) == [0, 10]
    pickup_only = [arr.TrackActivity("A", "pad", [(0, 64)]), arr.TrackActivity("B", "drums", [(32, 36)])]
    assert arr.detect_boundaries(arr.build_grid(pickup_only, 64, BPB)) == [0]  # a lone one-bar fill


def test_observations():
    tracks, end = song_layout()
    tracks.append(arr.TrackActivity("Hats", "hats", [(0, end)]))
    grid = arr.build_grid(tracks, end, BPB)
    obs = " | ".join(arr.observations(grid, tracks, arr.sections(grid, tracks), 120))
    assert "Bars 17-32 (16 bars)" in obs and "Length: 40 bars, 1:20" in obs
    assert "plays in 100% of bars" in obs  # hats never rest (song is >= 32 bars)


def test_bar_ranges():
    assert arr.bar_ranges([0, 1, 2, 5, 6, 9]) == "1-3, 6-7, 10"


def test_note_times_follow_clip_loops():
    looped = {"start_time": 8.0, "end_time": 24.0, "looping": True, "loop_start": 0.0, "loop_end": 4.0,
              "start_marker": 0.0, "end_marker": 4.0}
    assert A.note_times([0.0, 2.0], looped) == [8, 10, 12, 14, 16, 18, 20, 22]
    plain = {"start_time": 4.0, "end_time": 6.0, "looping": False, "start_marker": 1.0, "end_marker": 3.0}
    assert A.note_times([0.0, 1.0, 2.5], plain) == [4.0, 5.5]


# -- tools against the fake set ------------------------------------------------------------


@pytest.fixture
def scenes(client, song):
    session.create("track", "midi", name="Lead")
    compose.write_chords("Lead", "i iv", bars=1, slot=0)     # 4-beat clip in scene 1
    compose.write_chords("Lead", "i VI III VII", slot=1)     # 16-beat clip in scene 2
    song.tracks[1].arrangement_clips.clear()
    song.cue_points.clear()
    return song


def test_build_from_scenes_tiles_and_trims(scenes):
    r = A.edit_arrangement("build_from_scenes", sequence=[{"scene": 0, "bars": 3, "name": "Intro"},
                                                          {"scene": 1, "bars": 6, "name": "Main"}])
    assert r["sections"] == ["bar 1: Intro", "bar 4: Main"] and r["ends_at_bar"] == 10
    lead = scenes.tracks[3].arrangement_clips
    assert [(c.start_time, c.end_time) for c in lead] == [(0, 4), (4, 8), (8, 12), (12, 28), (28, 36)]
    assert [(c.name, c.time) for c in scenes.cue_points] == [("Intro", 0.0), ("Main", 12.0)]


def test_build_refuses_to_overwrite_without_confirm(scenes):
    A.edit_arrangement("build_from_scenes", sequence=[{"scene": 0, "bars": 2}])
    with pytest.raises(LiveError, match="confirm=true"):
        A.edit_arrangement("build_from_scenes", sequence=[{"scene": 0, "bars": 2}])


def test_insert_delete_duplicate(scenes):
    A.edit_arrangement("build_from_scenes", sequence=[{"scene": 0, "bars": 2, "name": "A"}, {"scene": 0, "bars": 2, "name": "B"}])
    A.edit_arrangement("insert_bars", bar=3, bars=2)
    starts = lambda: [c.start_time for c in scenes.tracks[3].arrangement_clips]
    assert starts() == [0, 4, 16, 20] and [c.time for c in scenes.cue_points] == [0.0, 16.0]
    A.edit_arrangement("duplicate_section", bar=1, end_bar=3)
    assert starts() == [0, 4, 8, 12, 24, 28] and [c.time for c in scenes.cue_points] == [0.0, 24.0]
    with pytest.raises(LiveError, match="confirm=true"):
        A.edit_arrangement("delete_bars", bar=5, end_bar=7)
    A.edit_arrangement("delete_bars", bar=5, end_bar=7, confirm=True)
    assert starts() == [0, 4, 8, 12, 16, 20]
    assert [c.time for c in scenes.cue_points] == [0.0, 16.0]


def test_edits_refuse_to_split_clips(scenes):
    A.edit_arrangement("build_from_scenes", sequence=[{"scene": 1, "bars": 4}])
    with pytest.raises(LiveError, match="cross the edit point"):
        A.edit_arrangement("insert_bars", bar=2, bars=1)


def test_locator_actions_and_detection(scenes):
    A.edit_arrangement("build_from_scenes", sequence=[{"scene": 0, "bars": 4}, {"scene": 1, "bars": 8}])
    scenes.cue_points.clear()
    r = A.edit_arrangement("locators_from_sections")
    assert len(r["locators"]) >= 1
    A.edit_arrangement("add_locator", bar=9, name="Hook")
    A.edit_arrangement("rename_locator", locator="Hook", name="Chorus")
    assert any(c.name == "Chorus" and c.time == 32.0 for c in scenes.cue_points)
    A.edit_arrangement("delete_locator", locator="Chorus")
    assert not any(c.name == "Chorus" for c in scenes.cue_points)
    g = A.get_arrangement()
    assert g["song"]["bars"] == 12 and "Lead" in [t["name"] for t in g["tracks"]]


def test_empty_arrangement_explains(client, song):
    song.tracks[1].arrangement_clips.clear()
    with pytest.raises(LiveError, match="build_from_scenes"):
        A.get_arrangement()
