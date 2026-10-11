"""Phase 7: stem analysis (masking, high-pass, resonances, low-end mono) and applying EQ moves."""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Literal

import soundfile as sf

from .. import resolve, workspace
from ..app import tool
from ..audio import stems as stem_analysis
from ..audio.report import AUDIO_EXTS
from ..live_client import LiveError, get_client
from ..util import compact

MCP_EQ = "MCP EQ"
EQ_TYPES = {
    ("low_cut", 12): "High Pass 12dB", ("low_cut", 48): "High Pass 48dB",
    ("high_cut", 12): "Low Pass 12dB", ("high_cut", 48): "Low Pass 48dB",
    ("bell", None): "Bell", ("notch", None): "Notch", ("low_shelf", None): "Low Shelf", ("high_shelf", None): "High Shelf",
}


def _stem_files(folder: str | None, files: list[str] | None) -> list[Path]:
    if files:
        out = []
        for f in files:
            p = Path(f).expanduser()
            p = p if p.is_absolute() else workspace.folder("exports") / p
            if not p.is_file():
                raise LiveError(f"Stem file not found: {f}")
            out.append(p)
        return out
    if not folder:
        raise LiveError("Give folder (e.g. 'my-song/stems' inside agent/exports) or files")
    root = Path(folder).expanduser()
    root = root if root.is_absolute() else workspace.folder("exports") / folder
    if not root.is_dir():
        raise LiveError(f"Folder not found: {root}")
    found = sorted(p for p in root.iterdir() if p.suffix.lower() in AUDIO_EXTS and p.is_file())
    if len(found) < 2:
        raise LiveError(f"Need at least 2 stems in {root}; found {len(found)}")
    return found


# Drum-machine and console abbreviations, expanded before matching stems to track names.
ABBREVIATIONS = {
    "bd": "kick", "kd": "kick", "sd": "snare", "sn": "snare", "cl": "clap", "cp": "clap", "ch": "closed hat",
    "oh": "open hat", "hh": "hat", "sh": "shaker", "pc": "perc", "pc1": "perc 1", "pc2": "perc 2",
    "vo": "vocal", "vox": "vocal", "bv": "backing vocal", "gtr": "guitar", "kb": "keys",
}


def _norm(s: str) -> str:
    tokens = [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]
    return " ".join(ABBREVIATIONS.get(t, t) for t in tokens)


def match_tracks(labels: list[str], track_names: list[str]) -> dict[str, str]:
    """Best-effort stem -> Live track mapping by name (exports usually contain the track name)."""
    out = {}
    tracks = {_norm(t): t for t in track_names if _norm(t)}
    for label in labels:
        n = _norm(label)
        contained = [t for t in tracks if t in n or n in t]
        if contained:
            out[label] = tracks[max(contained, key=len)]
            continue
        close = difflib.get_close_matches(n, list(tracks), n=1, cutoff=0.8)
        if close:
            out[label] = tracks[close[0]]
    return out


@tool(read_only=True)
def analyze_stems(folder: str | None = None, files: list[str] | None = None, roles: dict[str, str] | None = None,
                  max_conflicts: int = 8) -> dict:
    """Analyze exported stems together and suggest mixing moves.

    Finds frequency masking over time (two stems loud in the same band at the same moments),
    high-pass candidates, persistent resonances, stereo low end, low-mid/harsh build-ups and
    near-inaudible parts. Each suggestion names the stem (and the matching Live track when the
    names match) with a concrete EQ band for apply_eq, a sidechain idea, or a bass-mono hint.

    folder: a folder inside agent/exports (e.g. 'night-drive/stems') or an absolute path; or
    files: a list of stem paths. Stems must share a sample rate and start together (as Live's
    'Export All Individual Tracks' writes them). Roles (kick, bass, snare, clap, hats, perc,
    vocal, lead, keys, pad, guitar, fx, drums) are guessed from names and checked against the
    audio; override with roles={"name part": "role"}."""
    paths = _stem_files(folder, files)
    rates = {sf.info(str(p)).samplerate for p in paths}
    if len(rates) > 1:
        raise LiveError(f"Stems have different sample rates {sorted(rates)}; export them at one rate")
    fs = rates.pop()
    bmat = stem_analysis._band_matrix(fs)
    loaded = []
    for p in paths:
        data, _ = sf.read(str(p), always_2d=True, dtype="float32")
        loaded.append(stem_analysis.load_stem(p, data, fs, bmat))
    result = stem_analysis.analyze(loaded, fs, max_conflicts, roles)
    try:
        names = [t["name"] for t in get_client().query("song.tracks", {"props": ["name"]})]
        mapping = match_tracks([s["stem"] for s in result["stems"]], names)
    except LiveError:
        mapping = {}  # Live not running: suggestions just name the stems
    for item in result["stems"] + result["suggestions"]:
        if item.get("stem") in mapping:
            item["track"] = mapping[item["stem"]]
    result["unmatched_stems"] = [s["stem"] for s in result["stems"] if s["stem"] not in mapping] or None
    name = Path(folder).name if folder else paths[0].parent.name
    out = workspace.folder("reports") / f"{name}.stems.json"
    out.write_text(json.dumps(result, indent=1, default=str))
    result["report_file"] = str(out.relative_to(workspace.agent_dir()))
    return compact(result)


KEEP_LAST = "AbletonMCP Analyzer"  # captures must see everything, so insert before it


def find_or_create_device(client, tpath: str, device: str, name: str) -> tuple[str, bool]:
    """Path of the device called `name` on the track, inserting `device` at the end if missing
    (but before the AbletonMCP Analyzer, which stays last)."""
    devices = client.query(f"{tpath}.devices", {"props": ["name"]})
    for d in devices:
        if d["name"] == name:
            return f"{tpath}.devices.{d['_i']}", False
    analyzer = next((d["_i"] for d in devices if d["name"] == KEEP_LAST), None)
    if analyzer is None:
        client.invoke(tpath, "insert_device", device)
        index = len(devices)
    else:
        client.invoke(tpath, "insert_device", device, analyzer)
        index = analyzer
    path = f"{tpath}.devices.{index}"
    client.set(path, name=name)
    return path, True


def eq_band_commands(client, eq: str, bands: list[dict], replace: bool, fresh: bool) -> tuple[list[dict], list[int]]:
    """Commands that write `bands` into an EQ Eight (fills free bands; replace/fresh clears all 8 first)."""
    params = client.query(f"{eq}.parameters", {"props": ["name", "$display"]})
    by_name = {p["name"]: p for p in params}
    clear = replace or fresh  # a fresh EQ Eight has bands switched on with default shapes
    used = [] if clear else [n for n in range(1, 9) if by_name[f"{n} Filter On A"]["$display"] == "On"]
    free = [n for n in range(1, 9) if n not in used]
    if len(bands) > len(free):
        raise LiveError(f"The EQ has {len(free)} free bands; asked for {len(bands)}. Use replace=true to start over.")

    def cmd(n: int, name: str, value) -> dict:
        return {"cmd": "param_set_display",
                "params": {"path": f"{eq}.parameters.{by_name[f'{n} {name} A']['_i']}", "target": value}}

    commands = [cmd(n, "Filter On", "Off") for n in range(1, 9)] if clear else []
    applied = []
    for n, band in zip(free, bands):
        kind = str(band.get("type", "bell"))
        slope = int(float(band.get("slope", 12))) if kind in ("low_cut", "high_cut") else None
        if slope not in (None, 12, 48):
            raise LiveError("slope must be 12 or 48")
        if (kind, slope) not in EQ_TYPES:
            raise LiveError(f"Unknown band type {kind!r}; use bell, notch, low_shelf, high_shelf, low_cut, high_cut")
        commands.append(cmd(n, "Filter Type", EQ_TYPES[(kind, slope)]))
        commands.append(cmd(n, "Frequency", f"{float(band['freq'])} Hz"))
        if kind in ("bell", "low_shelf", "high_shelf"):
            commands.append(cmd(n, "Gain", float(band.get("gain_db", 0.0))))
        commands.append(cmd(n, "Q", float(band.get("q", 0.71))))
        commands.append(cmd(n, "Filter On", "On"))
        applied.append(n)
    return commands, applied


def eq_state(client, eq: str, bands: list[int]) -> list[dict]:
    now = {p["name"]: p["$display"] for p in client.query(f"{eq}.parameters", {"props": ["name", "$display"]})}
    return [{"band": n, "type": now[f"{n} Filter Type A"], "freq": now[f"{n} Frequency A"], "gain": now[f"{n} Gain A"],
             "q": now[f"{n} Q A"]} for n in bands]


@tool()
def apply_eq(track: str | int, bands: list[dict[str, str | float]], replace: bool = False) -> dict:
    """Apply EQ moves to a track in a dedicated EQ Eight named 'MCP EQ' (added at the end of the
    chain if missing, so your own EQs are never touched). One undo step.

    bands: [{"type": "bell", "freq": 250, "gain_db": -3, "q": 1.2}, {"type": "low_cut", "freq": 90, "slope": 12}]
    types: bell, notch, low_shelf, high_shelf, low_cut (high-pass), high_cut (low-pass); slope 12 or 48
    for cuts. Fills free bands; replace=true clears all 8 bands first. Use the 'eq' objects that
    analyze_stems suggests as-is."""
    client = get_client()
    tpath = resolve.track_path(client, track)
    eq, created = find_or_create_device(client, tpath, "EQ Eight", MCP_EQ)
    commands, applied = eq_band_commands(client, eq, bands, replace, created)
    client.batch(commands, undo_name="Apply EQ")
    return {"device": eq, "created": created, "bands": eq_state(client, eq, applied)}
