"""Phase 5: devices, parameters, loading, and clip automation."""

from __future__ import annotations

import math
import random
import re
import string
from typing import Any, Literal

from .. import resolve
from ..app import tool
from ..live_client import LiveClient, LiveError, get_client
from ..util import compact

AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg")
KIND_ROOTS = {
    "device": ["instruments", "audio_effects", "midi_effects"],
    "preset": ["sounds", "drums", "instruments", "audio_effects", "midi_effects", "user_library"],
    "plugin": ["plugins"],
    "max": ["max_for_live", "user_library"],
    "sample": ["samples", "user_library"],
    "any": ["instruments", "audio_effects", "midi_effects", "drums", "sounds", "plugins", "max_for_live",
            "user_library"],
}


# -- addressing -----------------------------------------------------------------------


def device_path(client: LiveClient, track, device) -> str:
    """Device by index or name; nested devices as 'Rack > Chain > Device' (indexes work too)."""
    path = resolve.track_path(client, track)
    parts = [p.strip() for p in str(device).split(">")] if isinstance(device, str) else [device]
    for depth, part in enumerate(parts):
        attr = "devices" if depth % 2 == 0 else "chains"
        if attr == "chains" and not client.get(path, "can_have_chains")["can_have_chains"]:
            name = client.get(path, "name")["name"]
            raise LiveError(f"{name!r} has no chains (is it a rack?). Use 'Rack > Chain > Device' only for racks.")
        names = [x["name"] for x in client.query(f"{path}.{attr}", {"props": ["name"]})]
        if not names:
            raise LiveError(f"{path} has no {attr}")
        path = f"{path}.{attr}.{resolve.match_name(names, part, 'device' if attr == 'devices' else 'chain')}"
    if len(parts) % 2 == 0:
        raise LiveError("A device path must end on a device: 'Rack > Chain > Device'")
    return path


def parameter_path(client: LiveClient, track, device, parameter) -> str:
    """A device parameter, or with device=None a mixer control: volume, pan, 'send A' / 'send reverb'."""
    if device is None:
        tpath = resolve.track_path(client, track)
        key = str(parameter).strip().lower()
        if key in ("volume", "vol", "level", "gain"):
            return f"{tpath}.mixer_device.volume"
        if key in ("pan", "panning"):
            return f"{tpath}.mixer_device.panning"
        if key.startswith("send"):
            ret = key.removeprefix("send").strip()
            names = [r["name"] for r in client.query("song.return_tracks", {"props": ["name"]})]
            if len(ret) == 1 and ret in string.ascii_lowercase and string.ascii_lowercase.index(ret) < len(names):
                idx = string.ascii_lowercase.index(ret)
            else:
                idx = resolve.match_name(names, ret, "return/send")
            return f"{tpath}.mixer_device.sends.{idx}"
        raise LiveError(f"Without a device, parameter must be volume, pan or 'send A'; got {parameter!r}")
    dpath = device_path(client, track, device)
    names = [p["name"] for p in client.query(f"{dpath}.parameters", {"props": ["name"]})]
    return f"{dpath}.parameters.{resolve.match_name(names, parameter, 'parameter')}"


# -- reading and setting ----------------------------------------------------------------

# Devices abbreviate parameter names differently ("Filter 1 Freq" vs "Flt 1 Freq").
ABBREVIATIONS = {
    "filter": ["flt", "lp ", "hp ", "cutoff"], "frequency": ["freq"], "resonance": ["res", "reso"],
    "envelope": ["env"], "oscillator": ["osc"], "amplitude": ["amp"], "attack": ["att", "atk"],
    "release": ["rel"], "volume": ["vol", "gain", "level"], "modulation": ["mod"], "detune": ["det"],
}


def _name_matches(name: str, query: str) -> bool:
    n, q = name.lower(), query.lower()
    if q in n:
        return True
    return any(alt in n for full, alts in ABBREVIATIONS.items() if full.startswith(q) or q in alts for alt in alts + [full])


@tool(read_only=True)
def get_device(track: str | int, device: str | int, filter: str | None = None, ranges: bool = False) -> dict:
    """A device's parameters with their current displayed values, as rows [index, name, value].
    filter keeps parameters whose name contains it ('filter', 'env', 'lfo'). ranges=true adds
    min/max display values and the options of switch-like parameters (waveforms, modes).
    device: name or index from get_track_details; nested rack devices as 'Rack > Chain > Device'.
    Racks also list their chains and macros."""
    client = get_client()
    dpath = device_path(client, track, device)
    d = client.query(dpath, {
        "props": ["name", "class_display_name", "is_active", "can_have_chains"],
        "children": {
            "parameters": {"props": ["name", "$display", "is_quantized", "is_enabled"]},
            "chains": {"props": ["name"], "children": {"devices": {"props": ["name"]}}},
        },
    })
    selected = [p for p in d["parameters"] if not filter or _name_matches(p["name"], filter)]
    if filter and not selected:
        raise LiveError(f"No parameter of {d['name']} matches {filter!r}. Some names: "
                        + ", ".join(p["name"] for p in d["parameters"][:25]))
    infos = client.batch([{"cmd": "param_info", "params": {"path": f"{dpath}.parameters.{p['_i']}"}}
                          for p in selected]) if ranges and selected else [None] * len(selected)
    rows = []
    for p, info in zip(selected, infos):
        row: list[Any] = [p["_i"], p["name"], p["$display"]]
        if info:
            row.append(info.get("value_items") or [info["display_min"], info["display_max"]])
        rows.append(row)
    out = {
        "path": dpath, "name": d["name"], "device": d.get("class_display_name"), "on": d.get("is_active"),
        "columns": ["index", "name", "value"] + (["options or range"] if ranges else []),
        "parameters": rows,
    }
    if d.get("can_have_chains"):
        out["chains"] = [{"name": c["name"], "devices": [x["name"] for x in c.get("devices") or []]}
                         for c in d.get("chains") or []]
        out["macros"] = [r for r in rows if str(r[1]).lower().startswith("macro")] or None
    return compact(out)


@tool()
def set_device_params(track: str | int, device: str | int, params: dict[str, str | float] | None = None,
                      on: bool | None = None) -> dict:
    """Set device parameters by name (or index) using the values Live displays, all as one undo step:
    {"LP Freq": "800 Hz", "LP Res": 40, "Osc 1 Wave": "Saw", "Env 1 Attack": "12 ms"}.
    Numbers are in the parameter's own unit (Hz, ms, dB, %); switch-like parameters take the
    option name; 'raw:0.5' sets the underlying 0..1 value. on=false bypasses the device."""
    client = get_client()
    dpath = device_path(client, track, device)
    names = [p["name"] for p in client.query(f"{dpath}.parameters", {"props": ["name"]})]
    commands, touched = [], []
    for key, value in (params or {}).items():
        idx = resolve.match_name(names, key, "parameter")
        ppath = f"{dpath}.parameters.{idx}"
        if isinstance(value, str) and value.startswith("raw:"):
            commands.append({"cmd": "lom_set", "params": {"path": ppath, "prop": "value", "value": float(value[4:])}})
        else:
            commands.append({"cmd": "param_set_display", "params": {"path": ppath, "target": value}})
        touched.append(idx)
    if on is not None:
        commands.append({"cmd": "lom_set", "params": {"path": f"{dpath}.parameters.0", "prop": "value",
                                                      "value": 1.0 if on else 0.0}})
        touched.append(0)
    if not commands:
        raise LiveError("Nothing to change")
    client.batch(commands, undo_name="Set device parameters")
    now = client.query(f"{dpath}.parameters", {"props": ["name", "$display"]})
    return {"device": dpath, "now": {now[i]["name"]: now[i]["$display"] for i in dict.fromkeys(touched)}}


# -- loading ------------------------------------------------------------------------------


def _classify(item: dict) -> str:
    name, path = item["name"].lower(), item["path"]
    if name.endswith(AUDIO_EXT):
        return "sample"
    if name.endswith(".alc"):
        return "clip"
    if name.endswith(".amxd"):
        return "max"
    if name.endswith((".adg", ".adv", ".fxp", ".aupreset")):
        return "preset"
    if ".plugins." in path:
        return "plugin"
    return "device"


def _rank(item: dict, query: str) -> tuple:
    stem = re.sub(r"\.(adg|adv|amxd|alc|wav|aif|aiff|flac|mp3)$", "", item["name"].lower())
    q = query.lower()
    match = 0 if stem == q else 1 if stem.startswith(q) else 2
    kind_order = {"device": 0, "preset": 1, "plugin": 2, "max": 3, "clip": 4, "sample": 5}
    return (match, kind_order[_classify(item)], item["path"].count("."), len(stem))


def search_browser(client: LiveClient, query: str, kind: str = "any", limit: int = 10) -> list[dict]:
    res = client.call("browser_search", query=query, roots=KIND_ROOTS[kind], limit=60, max_depth=5)
    items = [r for r in res["results"] if r["loadable"]]
    if kind not in ("any", "sample"):
        items = [r for r in items if _classify(r) == kind or (kind == "preset" and _classify(r) == "device")]
    items.sort(key=lambda r: _rank(r, query))
    return [{**r, "kind": _classify(r)} for r in items[:limit]]


@tool()
def load_device(track: str | int, name: str, kind: Literal["any", "device", "preset", "plugin", "max", "sample"] = "any",
                position: int = -1, search_only: bool = False) -> dict:
    """Load a device onto a track: a built-in device by name ('Drift', 'EQ Eight', 'Glue Compressor'),
    an instrument/effect preset or drum kit ('909 Core Kit', 'Grand Piano'), a plugin (VST/AU), or a
    Max for Live device. kind narrows the browser search. position: index in the device chain for
    built-in devices (-1 = end). A track holds one instrument: delete the old one first
    (delete target='device'). search_only=true lists matches without loading."""
    client = get_client()
    tpath = resolve.track_path(client, track)
    before = [d["name"] for d in client.query(f"{tpath}.devices", {"props": ["name"]})]
    if not search_only and kind in ("any", "device"):
        try:
            args = [name] if position < 0 else [name, position]
            client.invoke(tpath, "insert_device", *args)
            after = [d["name"] for d in client.query(f"{tpath}.devices", {"props": ["name"]})]
            return {"loaded": name, "via": "insert_device", "devices": after}
        except LiveError as e:
            if "instrument" in str(e).lower():
                raise LiveError(f"{e}. Delete the current instrument first (delete target='device').") from e
            # not a built-in device name: fall through to the browser
    matches = search_browser(client, name, kind)
    if search_only:
        return {"matches": [{k: m[k] for k in ("name", "kind", "path")} for m in matches]}
    if not matches:
        raise LiveError(f"Nothing loadable named like {name!r} in the browser ({kind})")
    best = matches[0]
    client.set("song.view", selected_track={"$path": tpath})
    client.invoke("app.browser", "load_item", {"$path": best["path"]})
    after = [d["name"] for d in client.query(f"{tpath}.devices", {"props": ["name"]})]
    return compact({"loaded": best["name"], "kind": best["kind"], "via": "browser", "devices": after,
                    "note": "Live may still be loading samples" if after == before else None,
                    "other_matches": [m["name"] for m in matches[1:5]]})


# -- automation ---------------------------------------------------------------------------


def _wave(shape: str, phase: float, rng: random.Random) -> float:
    """0..1 for one cycle position phase in [0, 1)."""
    if shape == "sine":
        return 0.5 - 0.5 * math.cos(2 * math.pi * phase)
    if shape == "triangle":
        return 1 - abs(2 * phase - 1)
    if shape == "saw":
        return phase
    if shape == "ramp_down":
        return 1 - phase
    if shape == "square":
        return 1.0 if phase < 0.5 else 0.0
    if shape == "random":
        return rng.random()
    raise ValueError(f"Unknown wave {shape!r}; use sine, triangle, saw, ramp_down, square or random")


JUMP = 0.001  # beats between two breakpoints that form a step


def automation_points(shape: str, start: float, end: float, lo: float, hi: float, *, wave: str = "sine",
                      rate_beats: float = 4.0, phase: float = 0.0, curve: float = 1.0,
                      values: list[float] | None = None, step_beats: float = 1.0,
                      points: list[tuple[float, float]] | None = None, seed: int | None = None) -> list[tuple[float, float]]:
    """Breakpoints (beat, raw value). lo/hi are raw values; `values`/`points` are raw too."""
    if shape == "ramp":
        if curve == 1.0:
            return [(start, lo), (end, hi)]
        n = 16
        return [(start + (end - start) * k / n, lo + (hi - lo) * (k / n) ** curve) for k in range(n + 1)]
    if shape == "points":
        if not points:
            raise ValueError("shape 'points' needs points [[beat, value], ...]")
        return sorted(points)
    if shape == "steps":
        if not values:
            raise ValueError("shape 'steps' needs values")
        out, t, k = [], start, 0
        while t < end - 1e-9:
            v = values[k % len(values)]
            out += [(t, v), (min(t + step_beats, end) - JUMP, v)]
            t += step_beats
            k += 1
        return out
    if shape == "lfo":
        rng = random.Random(seed)
        hard = wave in ("square", "random", "saw", "ramp_down")
        per_cycle = 2 if hard else 16
        out, t = [], start
        while t < end - 1e-9:
            cycle_start = t
            for k in range(per_cycle):
                tk = cycle_start + rate_beats * k / per_cycle
                if tk >= end - 1e-9:
                    break
                ph = (k / per_cycle + phase) % 1.0
                v = lo + (hi - lo) * _wave(wave, ph, rng)
                if hard:
                    seg_end = min(tk + rate_beats / per_cycle, end)
                    if wave in ("saw", "ramp_down"):
                        v_end = lo + (hi - lo) * _wave(wave, min(ph + 1 / per_cycle, 0.999), rng)
                        out += [(tk, v), (seg_end - JUMP, v_end)]
                    else:
                        out += [(tk, v), (seg_end - JUMP, v)]
                else:
                    out.append((tk, v))
            t = cycle_start + rate_beats
        if not hard:
            out.append((end, lo + (hi - lo) * _wave(wave, ((end - start) / rate_beats + phase) % 1.0, rng)))
        return out
    raise ValueError(f"Unknown shape {shape!r}; use ramp, lfo, steps, points or clear")


@tool()
def automate(track: str | int, parameter: str | int, device: str | int | None = None,
             slot: int | None = None, arrangement_clip: int | None = None,
             shape: Literal["ramp", "lfo", "steps", "points", "clear"] = "ramp",
             start_value: str | float | None = None, end_value: str | float | None = None,
             wave: Literal["sine", "triangle", "saw", "ramp_down", "square", "random"] = "sine",
             rate_beats: float = 4.0, phase: float = 0.0, curve: float = 1.0,
             values: list[str | float] | None = None, step_beats: float = 1.0,
             points: list[list[str | float]] | None = None,
             start_beat: float | None = None, end_beat: float | None = None, keep_existing: bool = False,
             seed: int | None = None) -> dict:
    """Write automation for one parameter into a clip (session slot or arrangement clip; it plays
    with the clip). Values use the parameter's display units: "300 Hz", "-12 dB", 40 (%), "Saw".

    parameter: a device parameter name (with device), or without device a mixer control:
    volume, pan, 'send A'. shapes:
    - ramp: start_value -> end_value; curve >1 starts slow (exponential feel), <1 starts fast
    - lfo: wave between start_value (low) and end_value (high), one cycle per rate_beats
    - steps: values held for step_beats each (repeats to fill)
    - points: [[beat, value], ...] with straight lines between them
    - clear: remove this parameter's automation from the clip
    start_beat/end_beat limit the range (default: whole clip). Replaces the parameter's
    existing automation in the clip unless keep_existing=true."""
    client = get_client()
    clip = resolve.any_clip_path(client, track, slot, arrangement_clip)
    ppath = parameter_path(client, track, device, parameter)
    pname = client.get(ppath, "name")["name"]
    if shape == "clear":
        client.call("envelope", clip=clip, param=ppath, action="clear")
        return {"clip": clip, "parameter": pname, "cleared": True}
    info = client.get(clip, "length", "loop_start", "loop_end", "looping")
    start = start_beat if start_beat is not None else (info["loop_start"] if info["looping"] else 0.0)
    end = end_beat if end_beat is not None else (info["loop_end"] if info["looping"] else info["length"])
    if end <= start:
        raise LiveError(f"end_beat ({end}) must be after start_beat ({start})")

    # Convert every display value to raw in one round trip.
    targets: list[Any] = []
    if shape in ("ramp", "lfo"):
        if start_value is None or end_value is None:
            raise LiveError(f"shape '{shape}' needs start_value and end_value")
        targets = [start_value, end_value]
    elif shape == "steps":
        targets = list(values or [])
    elif shape == "points":
        targets = [p[1] for p in points or []]
    raw = client.call("param_values_for_display", path=ppath, targets=targets) if targets else []
    lo, hi = (raw[0], raw[1]) if shape in ("ramp", "lfo") else (0.0, 1.0)
    pts = automation_points(
        shape, start, end, lo, hi, wave=wave, rate_beats=rate_beats, phase=phase, curve=curve,
        values=raw if shape == "steps" else None, step_beats=step_beats,
        points=[(float(p[0]), r) for p, r in zip(points or [], raw)] if shape == "points" else None, seed=seed)
    client.call("envelope", clip=clip, param=ppath, points=[[round(t, 6), v] for t, v in pts], keep=keep_existing)

    # Read back a few moments and show them the way Live displays them (for an LFO, across one cycle).
    span = min(rate_beats, end - start) if shape == "lfo" else end - start
    last = end if shape in ("ramp", "points") else end - JUMP * 2  # steps: stay inside the last step
    check_times = sorted({min(start + span * k / 4, last) for k in range(5)})
    read = client.call("envelope", clip=clip, param=ppath, action="read", times=check_times) or []
    shown = client.batch([{"cmd": "lom_call", "params": {"path": ppath, "method": "str_for_value", "args": [v]}}
                          for v in read]) if read else []
    return {"clip": clip, "parameter": pname, "shape": shape, "breakpoints": len(pts),
            "range_beats": [start, end], "check": [[round(t, 3), s] for t, s in zip(check_times, shown)]}
