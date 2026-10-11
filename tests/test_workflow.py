"""Phase 9: batch through the real MCP call path, prompts, resources, and the merged tools."""

import asyncio
import json

from mcp.server.mcpserver.exceptions import ToolError

from ableton_mcp.server import mcp
from ableton_mcp.tools import midi, session


def call(name, args):
    """Call a tool the way a client does; returns (is_error, parsed result or error text)."""
    try:
        result = asyncio.run(mcp.call_tool(name, args))
    except ToolError as e:
        return True, str(e)
    return result.is_error, json.loads(result.content[0].text)


def test_batch_runs_steps_in_one_undo_group(client, song):
    err, out = call("batch", {"steps": [
        {"tool": "set_mixer", "args": {"track": "bass", "volume_db": -6}},
        {"tool": "set_track", "args": {"track": "vox", "name": "Lead Vox"}},
        {"tool": "set_song", "args": {"tempo": "128"}},  # validated/coerced like a direct call
    ]})
    assert not err and out["summary"].startswith("3/3 steps applied") and "One undo" in out["summary"]
    assert song.tracks[2].name == "Lead Vox" and song.tempo == 128.0
    assert song.undo_depth == 0 and song.undo_groups >= 1  # the group was opened and closed


def test_batch_stops_at_first_error_and_still_closes_group(client, song):
    err, out = call("batch", {"steps": [
        {"tool": "set_track", "args": {"track": "bass", "name": "B"}},
        {"tool": "set_track", "args": {"track": "nonexistent", "name": "X"}},
        {"tool": "set_track", "args": {"track": "vox", "name": "never"}},
    ]})
    assert not err and [r["ok"] for r in out["results"]] == [True, False]
    assert "No track matches" in out["results"][1]["error"]
    assert song.tracks[2].name == "Vox" and song.undo_depth == 0


def test_batch_continue_on_error_and_validation(client, song):
    err, out = call("batch", {"steps": [
        {"tool": "set_track", "args": {"track": "nonexistent", "name": "X"}},
        {"tool": "set_track", "args": {"track": "vox", "name": "V"}},
    ], "stop_on_error": False})
    assert [r["ok"] for r in out["results"]] == [False, True]
    for bad, msg in ([{"tool": "batch", "args": {}}], "another batch"), ([{"tool": "nope"}], "unknown tool"), \
                    ([{"args": {}}], "needs a 'tool'"):
        err, text = call("batch", {"steps": bad})
        assert err and msg in text


def test_destructive_steps_still_need_confirm(client, song):
    err, out = call("batch", {"steps": [{"tool": "delete", "args": {"target": "track", "track": "vox"}}]})
    assert not out["results"][0]["ok"] and "confirm=true" in out["results"][0]["error"]
    assert len(song.tracks) == 3


def test_prompts_and_resources(client, song):
    names = [p.name for p in asyncio.run(mcp.list_prompts())]
    assert names == ["organize_session", "write_part", "mix_check", "arrangement_review", "master_track"]
    msg = asyncio.run(mcp.get_prompt("write_part", {"part": "bassline", "style": "funk", "bars": "4"}))
    assert "funk" in msg.messages[0].content.text and "4 bars" in msg.messages[0].content.text
    session_json = json.loads(asyncio.run(mcp.read_resource("ableton://session"))[0].content)
    assert session_json["song"]["key"] == "A Minor"
    notes = asyncio.run(mcp.read_resource("ableton://api-notes"))[0].content
    assert "Live 12" in notes


def test_merged_tools(client, song):
    assert session.create("scene", name="Outro")["created"] == "song.scenes.4"
    assert session.create("track", "audio", name="FX")["name"] == "FX"
    session.playback("fire_clip", track="bass", slot=0)
    assert song.tracks[1].clip_slots[0].fired
    session.playback("jump_to", position_beats=16)
    assert song.current_song_time == 16
    r = midi.read_clip("bass", slot=0)
    assert "notes" in r and "analysis" in r
    assert list(midi.read_clip("bass", slot=0, include="notes")) == ["notes"]
    overview = session.get_session_overview()
    assert overview["live"]["live_version"] == "12.4.6"
