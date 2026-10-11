"""Phase 0/1: connection, reading the set, organizing, transport, undo, raw API."""

from __future__ import annotations

from typing import Any, Literal

from .. import resolve
from ..app import tool
from ..live_client import LiveError, get_client
from ..theory import NOTE_NAMES, pitch_class
from ..theory.scales import live_scale_name
from ..util import compact, hex_color, parse_color, require_confirm

CLIP_PROPS = ["name", "length", "is_midi_clip", "is_playing", "is_recording", "muted", "looping", "color"]
ARRANGEMENT_CLIP_PROPS = ["name", "start_time", "end_time", "length", "is_midi_clip", "muted"]
TRACK_PROPS = ["name", "color", "has_midi_input", "has_audio_input", "is_foldable", "is_grouped",
               "mute", "solo", "arm", "fold_state"]
SONG_PROPS = ["tempo", "signature_numerator", "signature_denominator", "root_note", "scale_name",
              "is_playing", "current_song_time", "song_length", "loop", "loop_start", "loop_length",
              "metronome", "record_mode", "session_record"]


def _track_spec(include_clips: bool, include_mixer: bool, include_arrangement: bool) -> dict:
    children: dict[str, Any] = {
        "group_track": {"props": ["name"]},
        "devices": {"props": ["name", "class_name"]},
    }
    if include_mixer:
        children["mixer_device"] = {"children": {
            "volume": {"props": ["$display"]},
            "panning": {"props": ["$display"]},
            "sends": {"props": ["$display"]},
        }}
    if include_clips:
        children["clip_slots"] = {"where": {"has_clip": True}, "children": {"clip": {"props": CLIP_PROPS}}}
    if include_arrangement:
        children["arrangement_clips"] = {"props": ARRANGEMENT_CLIP_PROPS}
    return {"props": TRACK_PROPS, "children": children}


def _kind(t: dict) -> str:
    if t.get("is_foldable"):
        return "group"
    if t.get("has_midi_input"):
        return "midi"
    if t.get("has_audio_input"):
        return "audio"
    return "return"


def _shape_clip(c: dict) -> dict:
    return {
        "name": c.get("name") or None,
        "length_beats": c.get("length"),
        "type": "midi" if c.get("is_midi_clip") else "audio",
        "playing": c.get("is_playing") or None,
        "recording": c.get("is_recording") or None,
        "muted": c.get("muted") or None,
        "color": hex_color(c.get("color")),
    }


def _shape_track(t: dict, kind: str | None = None) -> dict:
    mixer = t.get("mixer_device") or {}
    out = {
        "index": t.get("_i"),
        "name": t.get("name"),
        "kind": kind or _kind(t),
        "color": hex_color(t.get("color")),
        "group": (t.get("group_track") or {}).get("name"),
        "folded": bool(t.get("fold_state")) if t.get("is_foldable") else None,
        "mute": t.get("mute") or None,
        "solo": t.get("solo") or None,
        "arm": t.get("arm") or None,
        "volume": (mixer.get("volume") or {}).get("$display"),
        "pan": (mixer.get("panning") or {}).get("$display"),
        "sends": [s.get("$display") for s in mixer.get("sends") or []],
        "devices": [d["name"] for d in t.get("devices") or []],
    }
    if t.get("clip_slots") is not None:
        out["clips"] = [{"slot": s["_i"], **_shape_clip(s["clip"])} for s in t["clip_slots"] if s.get("clip")]
    if t.get("arrangement_clips") is not None:
        out["arrangement_clips"] = [_shape_arrangement_clip(c) for c in t["arrangement_clips"]]
    return out


def _shape_arrangement_clip(c: dict) -> dict:
    return {
        "index": c.get("_i"),
        "name": c.get("name") or None,
        "start": c.get("start_time"),
        "end": c.get("end_time"),
        "type": "midi" if c.get("is_midi_clip") else "audio",
        "muted": c.get("muted") or None,
    }


def _song_info(s: dict) -> dict:
    root = s.get("root_note")
    return {
        "tempo": round(s["tempo"], 3) if s.get("tempo") else None,
        "time_signature": f"{s.get('signature_numerator')}/{s.get('signature_denominator')}",
        "key": f"{NOTE_NAMES[root]} {s.get('scale_name')}" if isinstance(root, int) else None,
        "playing": s.get("is_playing"),
        "position_beats": s.get("current_song_time"),
        "song_length_beats": s.get("song_length"),
        "loop": {"on": s.get("loop"), "start": s.get("loop_start"), "length": s.get("loop_length")},
        "metronome": s.get("metronome"),
        "arrangement_record": s.get("record_mode"),
        "session_record": s.get("session_record"),
    }


# -- reading ------------------------------------------------------------------


@tool(read_only=True)
def get_session_overview(include_clips: bool = True, include_mixer: bool = True,
                         include_arrangement: bool = False) -> dict:
    """Call this first (it also confirms the connection and versions). Snapshot of the whole Live set: tempo, time signature, key/scale, transport, every
    track (kind, group, mute/solo/arm, volume/pan/sends, device names, session clips),
    return tracks, master, scenes, locators and selection. Set include_arrangement=true to
    also list arrangement-view clips (start/end in beats)."""
    spec = _track_spec(include_clips, include_mixer, include_arrangement)
    master_spec = _track_spec(False, include_mixer, False)
    data = get_client().query("song", {
        "props": SONG_PROPS,
        "children": {
            "tracks": spec,
            "return_tracks": _track_spec(False, include_mixer, False),
            "master_track": master_spec,
            "scenes": {"props": ["name", "color", "tempo", "tempo_enabled", "is_empty"]},
            "cue_points": {"props": ["name", "time"]},
            "view": {"children": {
                "selected_track": {"props": ["name"]},
                "selected_scene": {"props": ["name"]},
            }},
        },
    })
    view = data.get("view") or {}
    return compact({
        "live": get_client().call("ping"),
        "song": _song_info(data),
        "tracks": [_shape_track(t) for t in data["tracks"]],
        "return_tracks": [_shape_track(t, "return") for t in data["return_tracks"]],
        "master": _shape_track(data["master_track"], "master"),
        "scenes": [{
            "index": s["_i"],
            "name": s.get("name") or None,
            "tempo": s.get("tempo") if s.get("tempo_enabled") else None,
            "empty": s.get("is_empty") or None,
        } for s in data["scenes"]],
        "locators": [{"name": c.get("name"), "time": c.get("time")} for c in data.get("cue_points") or []],
        "selected": {
            "track": (view.get("selected_track") or {}).get("name"),
            "scene": (view.get("selected_scene") or {}).get("name"),
        },
    })


@tool(read_only=True)
def get_track_details(track: str | int) -> dict:
    """Everything about one track: devices (type, on/off), routing, all session clips with
    loop settings, and arrangement clips. `track` is an index, a name, 'master' or 'Return A'."""
    client = get_client()
    path = resolve.track_path(client, track)
    clip_props = CLIP_PROPS + ["loop_start", "loop_end", "start_marker", "end_marker"]
    spec = _track_spec(True, True, path.startswith("song.tracks"))
    spec["children"]["clip_slots"]["children"]["clip"]["props"] = clip_props
    spec["children"]["devices"] = {"props": ["name", "class_name", "class_display_name", "type", "is_active",
                                             "can_have_chains"],
                                   "children": {"parameters": {"props": ["name"]}}}
    for attr in ("input_routing_type", "input_routing_channel", "output_routing_type", "output_routing_channel"):
        spec["children"][attr] = {"props": ["display_name"]}
    t = client.query(path, spec)
    kind = "master" if path == "song.master_track" else "return" if "return_tracks" in path else None
    out = _shape_track(t, kind)
    out["path"] = path
    out["devices"] = [{
        "index": d["_i"],
        "name": d.get("name"),
        "device": d.get("class_display_name") or d.get("class_name"),
        "on": d.get("is_active"),
        "rack": d.get("can_have_chains") or None,
        "parameter_count": len(d.get("parameters") or []),
    } for d in t.get("devices") or []]
    if t.get("clip_slots"):
        for shaped, raw in zip(out["clips"], t["clip_slots"]):
            c = raw["clip"]
            shaped.update(loop_start=c.get("loop_start"), loop_end=c.get("loop_end"),
                          looping=c.get("looping"))
    out["routing"] = {
        attr: (t.get(attr) or {}).get("display_name")
        for attr in ("input_routing_type", "input_routing_channel", "output_routing_type", "output_routing_channel")
    }
    return compact(out)


# -- organizing -----------------------------------------------------------------


@tool()
def set_track(track: str | int, name: str | None = None, color: str | None = None,
              mute: bool | None = None, solo: bool | None = None, arm: bool | None = None,
              folded: bool | None = None) -> dict:
    """Rename, recolor ('#ff8800' or a name like 'blue'), mute, solo, arm, or fold/unfold a
    group track. Only the given fields change."""
    client = get_client()
    path = resolve.track_path(client, track)
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if color is not None:
        values["color"] = parse_color(color)
    if mute is not None:
        values["mute"] = mute
    if solo is not None:
        values["solo"] = solo
    if arm is not None:
        values["arm"] = arm
    if folded is not None:
        values["fold_state"] = int(folded)
    if not values:
        raise LiveError("Nothing to change")
    client.set(path, **values)
    return {"track": path, "updated": list(values)}


@tool()
def set_clip(track: str | int, slot: int, name: str | None = None, color: str | None = None,
             muted: bool | None = None, looping: bool | None = None,
             loop_start: float | None = None, loop_end: float | None = None) -> dict:
    """Edit a session clip: name, color, mute (clip activator), looping and loop range (beats)."""
    client = get_client()
    path = resolve.clip_path(client, track, slot)
    values: dict[str, Any] = {}
    for key, val in (("name", name), ("muted", muted), ("looping", looping)):
        if val is not None:
            values[key] = val
    if color is not None:
        values["color"] = parse_color(color)
    commands = []
    if values:
        commands.append({"cmd": "lom_set", "params": {"path": path, "values": values}})
    # Live rejects loop_start >= loop_end, so order the writes to keep them valid.
    if loop_start is not None and loop_end is not None:
        current = client.get(path, "loop_end")["loop_end"]
        order = (("loop_end", loop_end), ("loop_start", loop_start)) if loop_start >= current else (
            ("loop_start", loop_start), ("loop_end", loop_end))
        commands += [{"cmd": "lom_set", "params": {"path": path, "prop": k, "value": v}} for k, v in order]
    else:
        for key, val in (("loop_start", loop_start), ("loop_end", loop_end)):
            if val is not None:
                commands.append({"cmd": "lom_set", "params": {"path": path, "prop": key, "value": val}})
    if not commands:
        raise LiveError("Nothing to change")
    client.batch(commands, undo_name="Edit clip")
    return {"clip": path, "state": compact(_shape_clip(client.get(path, *CLIP_PROPS)))}


@tool()
def set_scene(scene: str | int, name: str | None = None, color: str | None = None,
              tempo: float | None = None) -> dict:
    """Rename or recolor a scene, or give it a tempo (0 clears the scene tempo)."""
    client = get_client()
    path = resolve.scene_path(client, scene)
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if color is not None:
        values["color"] = parse_color(color)
    if tempo is not None:
        if tempo > 0:
            values["tempo"] = tempo
            values["tempo_enabled"] = True
        else:
            values["tempo_enabled"] = False
    if not values:
        raise LiveError("Nothing to change")
    client.set(path, **values)
    return {"scene": path, "updated": list(values)}


@tool()
def create(target: Literal["track", "scene"], kind: Literal["midi", "audio", "return"] = "midi",
           index: int = -1, name: str | None = None, color: str | None = None) -> dict:
    """Create a track (kind: midi, audio or return) or an empty scene. index -1 appends at the end
    (ignored for return tracks). Group tracks can't be created through Live's API."""
    client = get_client()
    if target == "scene":
        client.invoke("song", "create_scene", index)
        count = len(client.get("song", "scenes")["scenes"])
        path = f"song.scenes.{count - 1 if index < 0 else index}"
    elif kind == "return":
        client.invoke("song", "create_return_track")
        count = len(client.get("song", "return_tracks")["return_tracks"])
        path = f"song.return_tracks.{count - 1}"
    else:
        client.invoke("song", f"create_{kind}_track", index)
        count = len(client.get("song", "tracks")["tracks"])
        path = f"song.tracks.{count - 1 if index < 0 else index}"
    values: dict[str, Any] = {}
    if name:
        values["name"] = name
    if color:
        values["color"] = parse_color(color)
    if values:
        client.set(path, **values)
    return {"created": path, "name": client.get(path, "name")["name"]}


@tool(destructive=True)
def delete(target: Literal["track", "scene", "clip", "device"], track: str | int | None = None,
           scene: str | int | None = None, slot: int | None = None, device: str | int | None = None,
           confirm: bool = False) -> dict:
    """Delete a track (or return track) with everything on it, a scene with every clip in its
    row, a session clip (track + slot), or a device (track + device name/index, nested as
    'Rack > Chain > Device'). Needs confirm=true; ask the user first."""
    client = get_client()
    if target == "track":
        path = resolve.track_path(client, _need(track, "track"))
        if path == "song.master_track":
            raise LiveError("The master track can't be deleted")
        name = client.get(path, "name")["name"]
        require_confirm(confirm, f"delete track {name!r} with all its clips and devices")
        method = "delete_return_track" if "return_tracks" in path else "delete_track"
        client.invoke("song", method, _index(path))
    elif target == "scene":
        path = resolve.scene_path(client, _need(scene, "scene"))
        name = client.get(path, "name")["name"] or path
        require_confirm(confirm, f"delete scene {name!r} and all clips in that row")
        client.invoke("song", "delete_scene", _index(path))
    elif target == "clip":
        path = resolve.clip_path(client, _need(track, "track"), _need(slot, "slot"))
        name = client.get(path, "name")["name"] or path
        require_confirm(confirm, f"delete clip {name!r}")
        client.invoke(path.removesuffix(".clip"), "delete_clip")
    else:
        from .devices import device_path
        path = device_path(client, _need(track, "track"), _need(device, "device"))
        name = client.get(path, "name")["name"]
        require_confirm(confirm, f"delete device {name!r}")
        parent, _, idx = path.rpartition(".devices.")
        client.invoke(parent, "delete_device", int(idx))
    return {"deleted": target, "name": name}


@tool(destructive=True)
def duplicate(target: Literal["track", "scene", "clip"], track: str | int | None = None,
              scene: str | int | None = None, slot: int | None = None,
              to_track: str | int | None = None, to_slot: int | None = None) -> dict:
    """Duplicate a track (copy goes right after it), a scene with its clips (copy goes below),
    or a session clip into another slot (default: the next slot down on the same track;
    overwrites the target slot)."""
    client = get_client()
    if target == "track":
        path = resolve.track_path(client, _need(track, "track"))
        if not path.startswith("song.tracks."):
            raise LiveError("Only regular tracks can be duplicated")
        client.invoke("song", "duplicate_track", _index(path))
        new = f"song.tracks.{_index(path) + 1}"
        return {"created": new, "name": client.get(new, "name")["name"]}
    if target == "scene":
        path = resolve.scene_path(client, _need(scene, "scene"))
        client.invoke("song", "duplicate_scene", _index(path))
        return {"created": f"song.scenes.{_index(path) + 1}"}
    slot = _need(slot, "slot")
    src = resolve.clip_slot_path(client, _need(track, "track"), slot)
    if not client.get(src, "has_clip")["has_clip"]:
        raise LiveError(f"Clip slot {slot} on {track!r} is empty")
    dst = resolve.clip_slot_path(client, to_track if to_track is not None else track,
                                 to_slot if to_slot is not None else slot + 1)
    client.invoke(src, "duplicate_clip_to", {"$path": dst})
    return {"copied_to": dst}


def _need(value, name: str):
    if value is None:
        raise LiveError(f"{name} is required here")
    return value


def _index(path: str) -> int:
    return int(path.rsplit(".", 1)[1])


@tool()
def set_song(tempo: float | None = None, time_signature: str | None = None,
             key: str | None = None, scale: str | None = None, metronome: bool | None = None,
             loop: bool | None = None, loop_start: float | None = None,
             loop_length: float | None = None) -> dict:
    """Set tempo (BPM), time signature ('6/8'), key root ('F#'), scale, metronome, and the
    arrangement loop (beats). Scales: Major, Minor, Dorian, Mixolydian, Lydian, Phrygian, Locrian,
    Harmonic Minor, Melodic Minor, Phrygian Dominant, Hungarian Minor, Minor/Major Pentatonic,
    Minor Blues, Whole Tone (aliases like 'aeolian' work)."""
    client = get_client()
    values: dict[str, Any] = {}
    if tempo is not None:
        values["tempo"] = float(tempo)
    if time_signature:
        num, den = time_signature.split("/")
        values["signature_numerator"] = int(num)
        values["signature_denominator"] = int(den)
    if key:
        values["root_note"] = pitch_class(key)
    if scale:
        values["scale_name"] = live_scale_name(scale)  # Live silently falls back to Major on unknown names
    for prop, val in (("metronome", metronome), ("loop", loop), ("loop_start", loop_start),
                      ("loop_length", loop_length)):
        if val is not None:
            values[prop] = val
    if not values:
        raise LiveError("Nothing to change")
    client.set("song", **values)
    return {"song": _song_info(client.get("song", *SONG_PROPS))}


# -- playback -------------------------------------------------------------------


@tool()
def playback(action: Literal["play", "stop", "continue", "stop_all_clips", "jump_to", "fire_clip", "fire_scene",
                             "stop_track"],
             position_beats: float | None = None, track: str | int | None = None, slot: int | None = None,
             scene: str | int | None = None) -> dict:
    """Transport and launching: play / stop / continue the song, stop_all_clips, jump_to a position
    (position_beats), fire_clip (track + slot), fire_scene (scene), stop_track (stop a track's clips)."""
    client = get_client()
    if action == "jump_to":
        if position_beats is None:
            raise LiveError("jump_to needs position_beats")
        # Moves the playhead now and the play-start marker (start_playing() begins at start_time).
        client.set("song", start_time=float(position_beats))
        client.set("song", current_song_time=float(position_beats))
    elif action == "fire_scene":
        path = resolve.scene_path(client, _need(scene, "scene"))
        client.invoke(path, "fire")
        return {"fired": path}
    elif action == "fire_clip":
        path = resolve.clip_slot_path(client, _need(track, "track"), _need(slot, "slot"))
        client.invoke(path, "fire")
        return {"fired": path}
    elif action == "stop_track":
        path = resolve.track_path(client, _need(track, "track"))
        client.invoke(path, "stop_all_clips")
        return {"stopped": path}
    else:
        method = {"play": "start_playing", "stop": "stop_playing", "continue": "continue_playing",
                  "stop_all_clips": "stop_all_clips"}[action]
        client.invoke("song", method)
    return {"song": _song_info(client.get("song", *SONG_PROPS))}


@tool()
def undo(steps: int = 1, redo: bool = False) -> dict:
    """Undo (or redo) the last change(s) in Live."""
    client = get_client()
    done = 0
    for _ in range(max(1, steps)):
        flag = "can_redo" if redo else "can_undo"
        if not client.get("song", flag)[flag]:
            break
        client.invoke("song", "redo" if redo else "undo")
        done += 1
    return {"redo" if redo else "undo": done}


# -- escape hatch -------------------------------------------------------------


@tool(destructive=True)
def live_api(op: Literal["get", "set", "call", "describe", "query"], path: str,
             props: list[str] | None = None, values: dict[str, Any] | None = None,
             method: str | None = None, args: list[Any] | None = None,
             spec: dict[str, Any] | None = None) -> Any:
    """Raw access to the Live Object Model for anything without a dedicated tool.

    path: dot path from 'song' or 'app', e.g. 'song.tracks.0.devices.1.parameters.3'.
    - describe: list properties, children and methods at path (start here when exploring)
    - get: read props (or the object itself if props is empty); '$display' gives a parameter's text value
    - set: write values {prop: value}
    - call: call method with args; pass a Live object as {"$path": "song.tracks.2"}
    - query: nested read, spec = {"props": [...], "children": {attr: spec}, "where": {prop: value}}
    """
    client = get_client()
    if op == "describe":
        return client.describe(path)
    if op == "get":
        return client.get(path, *(props or []))
    if op == "set":
        if not values:
            raise LiveError("set needs values")
        return client.set(path, **values)
    if op == "call":
        if not method:
            raise LiveError("call needs method")
        return client.invoke(path, method, *(args or []))
    return client.query(path, spec or {})
