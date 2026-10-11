"""Remote Script layer: path resolution, queries, notes, batch, threading."""

import json
import socket
import threading

import pytest

from AbletonMCP import dispatcher, lom, server
from ableton_mcp.live_client import LiveError

from .conftest import start_server


def test_ping(client):
    assert client.call("ping")["live_version"] == "12.4.6"


def test_get_and_set_by_path(client, song):
    assert client.get("song", "tempo") == {"tempo": 120.0}
    client.set("song", tempo=128.0)
    assert song.tempo == 128.0
    assert client.get("song.tracks.1.mixer_device.volume", "$display")["$display"] == "0.0 dB"


def test_bad_path_is_a_readable_error(client):
    with pytest.raises(LiveError, match="Index 9 out of range"):
        client.get("song.tracks.9", "name")
    with pytest.raises(LiveError, match="Unknown root"):
        client.get("nope.tracks")


def test_query_filters_and_indexes(client):
    result = client.query("song.tracks", {
        "props": ["name"],
        "children": {"clip_slots": {"where": {"has_clip": True}, "children": {"clip": {"props": ["name"]}}}},
    })
    assert [t["name"] for t in result] == ["Drums", "Bass", "Vox"]
    assert result[1]["clip_slots"] == [{"clip": {"name": "Bassline"}, "_i": 0}]


def test_query_tolerates_unreadable_props(client):
    result = client.query("song.return_tracks", {"props": ["name", "arm"]})
    assert result[0] == {"name": "A-Reverb", "arm": None, "_i": 0}


def test_call_with_object_reference(client, song):
    client.invoke("song.tracks.1.clip_slots.0", "duplicate_clip_to", {"$path": "song.tracks.1.clip_slots.2"})
    assert song.tracks[1].clip_slots[2].clip.name == "Bassline"


def test_notes_roundtrip(client, song):
    path = "song.tracks.1.clip_slots.0.clip"
    res = client.call("add_notes", path=path, notes=[
        {"pitch": 45, "start_time": 0, "duration": 0.5, "velocity": 110},
        {"pitch": 48, "start_time": 1, "duration": 0.5},
    ])
    assert res["added"] == 2
    notes = client.call("get_notes", path=path)
    assert [(n["pitch"], n["start_time"]) for n in notes] == [(45, 0), (48, 1)]
    client.call("remove_notes", path=path, note_ids=[notes[0]["note_id"]])
    assert [n["pitch"] for n in client.call("get_notes", path=path)] == [48]


def test_notes_reject_audio_clip(client, song):
    song.tracks[2].clip_slots[0].clip = song.tracks[1].clip_slots[0].clip.__class__(4.0, midi=False)
    with pytest.raises(LiveError, match="not a MIDI clip"):
        client.call("get_notes", path="song.tracks.2.clip_slots.0.clip")


def test_batch_stops_on_first_error(client, song):
    with pytest.raises(LiveError, match=r"batch\[1\]"):
        client.batch([
            {"cmd": "lom_set", "params": {"path": "song", "prop": "tempo", "value": 99.0}},
            {"cmd": "lom_get", "params": {"path": "song.tracks.42"}},
        ])
    assert song.tempo == 99.0


def _raw(port):
    sock = socket.create_connection(("127.0.0.1", port))
    sock.settimeout(2)
    return sock


def _poll_until(srv, sock, n_lines):
    buf = b""
    for _ in range(200):
        srv.poll()
        try:
            buf += sock.recv(65536)
        except socket.timeout:
            pass
        if buf.count(b"\n") >= n_lines:
            break
    return [json.loads(l) for l in buf.splitlines()]


def test_poll_executes_on_the_polling_thread(song):
    seen = []
    ctx = dispatcher.Context({"song": song}, "t")
    disp = dispatcher.Dispatcher(lambda: {"who": lambda c, p: seen.append(threading.current_thread().name)},
                                 lambda: ctx, log=print)
    srv = server.CommandServer("127.0.0.1", 0, disp.execute, log=lambda m: None)
    srv.start()
    sock = _raw(srv.port)
    sock.sendall(b'{"id": 1, "cmd": "who"}\n')
    sock.settimeout(0.01)
    assert _poll_until(srv, sock, 1)[0]["ok"]
    assert seen == [threading.current_thread().name]  # no hidden worker threads
    srv.stop()


def test_poll_handles_split_lines_and_several_clients(song):
    srv = start_server(song)
    a, b = _raw(srv.port), _raw(srv.port)
    for s in (a, b):
        s.settimeout(0.01)
    a.sendall(b'{"id": 1, "cmd": "ping"}\n{"id": 2, "cmd": "lom_get", "params": {"path": "so')
    b.sendall(b'{"id": 7, "cmd": "nope"}\n')
    srv.poll()
    a.sendall(b'ng", "props": ["tempo"]}}\n')
    replies_a = _poll_until(srv, a, 2)
    replies_b = _poll_until(srv, b, 1)
    assert [r["id"] for r in replies_a] == [1, 2] and replies_a[1]["result"] == {"tempo": 120.0}
    assert replies_b[0]["id"] == 7 and "unknown command" in replies_b[0]["error"]
    srv.stop()


def test_poll_without_clients_is_cheap(song):
    srv = start_server(song)
    for _ in range(1000):
        srv.poll()
    srv.stop()


def test_describe_lists_methods_and_props(song):
    d = lom.describe(song)
    assert "tempo" in d["properties"] and "create_midi_track" in d["methods"]
