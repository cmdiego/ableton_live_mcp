"""Phase 10: plan a master from an exported mix (simulated on the audio), apply it to Live's master
track, and iterate on new exports until loudness and true peak are within tolerance."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

import soundfile as sf

from .. import workspace
from ..app import tool
from ..audio import master as M
from ..audio import report as rep
from ..audio.compare import compare
from ..live_client import LiveError, get_client
from ..util import compact, require_confirm
from .mixing import eq_band_commands, eq_state, find_or_create_device

MASTER = "song.master_track"
CHAIN = [("eq", "EQ Eight", "MCP Master EQ"), ("glue", "Glue Compressor", "MCP Glue"),
         ("utility", "Utility", "MCP Utility"), ("limiter", "Limiter", "MCP Limiter")]
EQ_KEYS = ("type", "freq", "gain_db", "q", "slope")


def _state_file() -> Path:
    return workspace.folder("reports") / "master_state.json"


def _plan_file() -> Path:
    return workspace.folder("reports") / "master_plan.json"


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _chain_in_live() -> bool:
    try:
        names = {d["name"] for d in get_client().query(f"{MASTER}.devices", {"props": ["name"]})}
    except LiveError:
        return False
    return "MCP Limiter" in names


def _target(target: str, lufs: float | None, true_peak: float | None, ref_report: dict | None) -> dict:
    if target == "reference":
        if not ref_report:
            raise LiveError("target='reference' needs reference=<file in agent/references>")
        ref_lufs = ref_report["loudness"]["integrated_lufs"]
        t = {"lufs": max(-16.0, min(-6.0, round(ref_lufs, 1))), "true_peak": -1.0,
             "about": f"match the reference's loudness ({ref_lufs} LUFS)"}
    elif target == "custom":
        if lufs is None:
            raise LiveError("target='custom' needs lufs (and optionally true_peak)")
        t = {"lufs": float(lufs), "true_peak": -1.0, "about": "custom"}
    else:
        t = dict(M.TARGETS[target])
    if lufs is not None:
        t["lufs"] = float(lufs)
    if true_peak is not None:
        t["true_peak"] = float(true_peak)
    if not -24 <= t["lufs"] <= -5:
        raise LiveError("Target loudness must be between -24 and -5 LUFS")
    if not -6 <= t["true_peak"] <= 0:
        raise LiveError("True-peak ceiling must be between -6 and 0 dBTP")
    return t


@tool(read_only=True)
def plan_master(path: str | None = None,
                target: Literal["streaming", "apple", "loud", "club", "reference", "custom"] = "streaming",
                lufs: float | None = None, true_peak: float | None = None, reference: str | None = None) -> dict:
    """Plan (or check) a master for an exported mix. Simulates the chain on the actual audio to pick
    settings: gentle EQ (20 Hz high-pass + at most a few +/-3 dB moves; matched to a reference if
    given), optional glue compression (~1.5 dB, only for dynamic mixes), Utility (bass mono / width /
    DC only when needed) and a True Peak Limiter whose input gain hits the target loudness.

    path: export to measure ('latest' = newest in agent/exports; 'latest_capture' = newest analyzer
    capture, which needs no manual export). target: streaming (-14 LUFS,
    -1 dBTP), apple (-16), loud (-9), club (-8, -0.5 dBTP), reference (match a reference track's
    loudness), or custom; lufs/true_peak override. reference: a track in agent/references for tonal
    matching. If the export is newer than the chain apply_master put on Live's master, it already
    includes that chain: the plan becomes small corrections, with within_tolerance when done
    (+/-0.5 LU, true peak at or under the ceiling). Then call apply_master."""
    file = rep.resolve_path(path)
    mix = rep.analyze_file(file)
    ref_report = rep.analyze_file(rep.resolve_path(reference)) if reference else None
    comparison = compare(mix, ref_report) if ref_report else None
    t = _target(target, lufs, true_peak, ref_report)
    state = _load(_state_file())
    exported_after_apply = bool(state and file.stat().st_mtime > state["applied_at"] and _chain_in_live())
    measured = {k: mix["loudness"][k] for k in ("integrated_lufs", "true_peak_dbtp", "lra_lu", "plr_db")}
    if exported_after_apply:
        fix = M.corrections(mix, t["lufs"], t["true_peak"], state["settings"], comparison)
        plan = {"mode": "check", "file": str(file), "target": t, "measured": measured, **fix,
                "note": "This export is newer than the applied master chain, so it was measured through it."}
        if fix["within_tolerance"] and not fix["changes"]:
            plan["next"] = "Done: within tolerance. Listen against the reference before calling it final."
        else:
            plan["next"] = "apply_master to update the chain, then export again and run plan_master."
    else:
        data, fs = sf.read(str(file), always_2d=True, dtype="float32")
        settings = M.plan(data[:, :2] if data.shape[1] > 1 else data.repeat(2, axis=1), fs, mix, t["lufs"],
                          t["true_peak"], comparison)
        plan = {"mode": "new", "file": str(file), "target": t, "measured": measured,
                "settings": {k: settings[k] for k in ("eq", "glue", "utility", "limiter")},
                "predicted": settings["predicted"], "warnings": settings["warnings"],
                "next": "apply_master to build this chain on Live's master, then export the master again into "
                        "agent/exports and run plan_master to check the real result."}
        if mix["issues"]:
            plan["mix_issues"] = [i["issue"] for i in mix["issues"]]
    if comparison:
        plan["vs_reference"] = comparison["findings"]
    plan["planned_at"] = time.time()
    _plan_file().write_text(json.dumps(plan, indent=1, default=str))
    plan["plan_file"] = str(_plan_file().relative_to(workspace.agent_dir()))
    return compact(plan)


MAX = object()  # "set this parameter to its maximum" (e.g. Glue's Release, whose top position reads 'A' = auto)


def _param_commands(client, dpath: str, values: dict) -> list[dict]:
    params = {p["name"]: p for p in client.query(f"{dpath}.parameters", {"props": ["name", "max"]})}
    out = []
    for k, v in values.items():
        path = f"{dpath}.parameters.{params[k]['_i']}"
        if v is MAX:
            out.append({"cmd": "lom_set", "params": {"path": path, "prop": "value", "value": params[k]["max"]}})
        else:
            out.append({"cmd": "param_set_display", "params": {"path": path, "target": v}})
    return out


@tool()
def apply_master(remove: bool = False, confirm: bool = False) -> dict:
    """Build or update the master chain from the latest plan_master result, at the end of Live's master
    track: 'MCP Master EQ' (EQ Eight), 'MCP Glue' (Glue Compressor), 'MCP Utility', 'MCP Limiter'
    (Limiter in True Peak mode). Your own master devices are never changed. One undo step.
    After applying, export the master again into agent/exports and run plan_master to check.
    remove=true (with confirm=true) deletes the MCP master devices instead."""
    client = get_client()
    if remove:
        devices = client.query(f"{MASTER}.devices", {"props": ["name"]})
        ours = [d for d in devices if d["name"] in {name for _, _, name in CHAIN}]
        require_confirm(confirm, f"remove {len(ours)} MCP devices from the master track")
        for d in sorted(ours, key=lambda d: -d["_i"]):
            client.invoke(MASTER, "delete_device", d["_i"])
        _state_file().unlink(missing_ok=True)
        return {"removed": [d["name"] for d in ours]}
    plan = _load(_plan_file())
    if not plan:
        raise LiveError("No master plan yet: run plan_master first")
    settings = plan["settings"]
    client.call("undo_group", action="begin")
    try:
        paths, commands = {}, []
        for key, device, name in CHAIN:
            if key == "glue" and not settings.get("glue"):
                existing = [d for d in client.query(f"{MASTER}.devices", {"props": ["name"]}) if d["name"] == name]
                if existing:
                    commands += _param_commands(client, f"{MASTER}.devices.{existing[0]['_i']}", {"Device On": "Off"})
                continue
            paths[key], created = find_or_create_device(client, MASTER, device, name)
            if key == "eq":
                bands = [{k: m[k] for k in EQ_KEYS if k in m} for m in settings["eq"]]
                eq_cmds, eq_bands = eq_band_commands(client, paths[key], bands, replace=True, fresh=created)
                commands += eq_cmds
        if settings.get("glue"):
            g = settings["glue"]
            commands += _param_commands(client, paths["glue"], {
                "Device On": "On", "Threshold": g["threshold_db"], "Ratio": float(g["ratio"]), "Attack": 10.0,
                "Release": MAX, "Output": 0.0, "Dry/Wet": 100.0, "Range": 70.0})
        u = settings["utility"]
        commands += _param_commands(client, paths["utility"], {
            "Bass Mono": "On" if u["bass_mono"] else "Off", "Bass Freq": f"{u['bass_freq']} Hz",
            "Stereo Width": u["width"], "DC Filter": "On" if u["dc_filter"] else "Off"})
        lim = settings["limiter"]
        commands += _param_commands(client, paths["limiter"], {
            "Mode": "True Peak", "Maximize On": "Off", "Ceiling": lim["ceiling_db"], "Input Gain": lim["input_gain_db"],
            "Auto": "On" if lim.get("auto_release", True) else "Off", "Lookahead": "1.5 ms"})
        client.batch(commands)
    finally:
        client.call("undo_group", action="end")
    _state_file().write_text(json.dumps({"applied_at": time.time(), "settings": settings,
                                         "target": plan["target"], "source": plan["file"]}, indent=1))
    shown = {}
    for key, _, name in CHAIN:
        if key in paths:
            if key == "eq":
                shown[name] = eq_state(client, paths[key], list(range(1, len(settings["eq"]) + 1)))
            else:
                want = {"glue": ["Threshold", "Ratio", "Attack", "Release"],
                        "utility": ["Bass Mono", "Bass Freq", "Stereo Width", "DC Filter"],
                        "limiter": ["Mode", "Input Gain", "Ceiling", "Auto"]}[key]
                params = client.query(f"{paths[key]}.parameters", {"props": ["name", "$display"]})
                shown[name] = {p["name"]: p["$display"] for p in params if p["name"] in want}
    return {"applied": shown, "target": plan["target"],
            "next": "Export the master (File > Export Audio/Video, WAV, no normalization) into agent/exports, "
                    "then run plan_master to measure the real result."}
