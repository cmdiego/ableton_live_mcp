"""Phase 11: the Max for Live analyzer on the master — capture Live's real output, read a live meter."""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path
from typing import Literal

import soundfile as sf

from .. import m4l, workspace
from ..app import tool
from ..audio import report as rep
from ..live_client import LiveError, get_client
from ..util import compact
from ..workspace import REPO_ROOT

MASTER = "song.master_track"
DEVICE_FILE = REPO_ROOT / "m4l" / f"{m4l.DEVICE_NAME}.amxd"
USER_LIBRARY_DIR = Path.home() / "Music" / "Ableton" / "User Library" / "Presets" / "Audio Effects" / "Max Audio Effect"


def _devices(client) -> list[dict]:
    return client.query(f"{MASTER}.devices", {"props": ["name"]})


def _position(client) -> tuple[int | None, int]:
    devs = _devices(client)
    idx = next((d["_i"] for d in devs if d["name"] == m4l.DEVICE_NAME), None)
    return idx, len(devs)


def _ping(timeout: float = 2.0) -> float | None:
    reply = m4l.link().request("/ping", "/pong", timeout=timeout)
    return float(reply[0]) if reply else None


def install(client) -> dict:
    dest = USER_LIBRARY_DIR / DEVICE_FILE.name
    if not dest.exists() or dest.read_bytes() != DEVICE_FILE.read_bytes():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEVICE_FILE, dest)
    idx, _ = _position(client)
    if idx is None:
        found = []
        for _ in range(10):  # Live indexes a newly copied file after a moment
            found = client.call("browser_search", query=m4l.DEVICE_NAME, roots=["user_library"], limit=1,
                                max_depth=6)["results"]
            if found:
                break
            time.sleep(0.5)
        if not found:
            raise LiveError(f"Live's browser doesn't list {m4l.DEVICE_NAME} yet ({dest}). Is this Live Suite "
                            "(Max for Live)? Try again in a few seconds.")
        client.set("song.view", selected_track={"$path": MASTER})
        client.invoke("app.browser", "load_item", {"$path": found[0]["path"]})
    sr = None
    for _ in range(10):
        sr = _ping(1.0)
        if sr:
            break
    if not sr:
        raise LiveError("The analyzer is on the master but doesn't answer. Is audio (DSP) running in Live?")
    return status(client)


def status(client) -> dict:
    idx, count = _position(client)
    if idx is None:
        return {"installed": False, "next": "analyzer(action='install') puts it on the master track"}
    sr = _ping()
    out = {"installed": True, "position": f"{idx + 1} of {count} on the master", "responding": bool(sr),
           "sample_rate": sr}
    if idx != count - 1:
        out["warning"] = "It isn't the last device on the master, so captures miss the devices after it."
    return compact(out)


def capture(client, seconds: float | None, bars: float | None, start_bar: float | None, play: bool,
            name: str | None) -> dict:
    idx, count = _position(client)
    if idx is None:
        raise LiveError("The analyzer isn't on the master yet: analyzer(action='install')")
    song = client.get("song", "tempo", "signature_numerator", "signature_denominator", "is_playing", "start_time")
    bpb = song["signature_numerator"] * 4.0 / song["signature_denominator"]
    if bars:
        seconds = bars * bpb * 60.0 / song["tempo"]
    seconds = float(seconds or 10.0)
    if not 0.5 <= seconds <= 600:
        raise LiveError("Capture length must be between 0.5 s and 10 minutes")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (name or "capture"))
    path = workspace.folder("captures") / f"{safe}_{stamp}.wav"  # no spaces: Max splits messages on them
    link = m4l.link()
    started = False
    if start_bar is not None:
        # start_playing() begins at song.start_time (the play-start marker), not current_song_time.
        beat = (start_bar - 1) * bpb
        client.set("song", start_time=beat)
        client.set("song", current_song_time=beat)
    if play and not song["is_playing"]:
        client.invoke("song", "start_playing")
        started = True
        time.sleep(0.15)
    elif not play and not song["is_playing"]:
        raise LiveError("Live isn't playing: start playback or pass play=true")
    link.send("/open", str(path))
    time.sleep(0.1)
    link.send("/start")
    try:
        time.sleep(seconds)
    finally:
        stopped = link.request("/stop", "/stopped", timeout=5)
        if started:
            client.invoke("song", "stop_playing")
        if start_bar is not None:
            client.set("song", start_time=song["start_time"])  # put the user's play marker back
    if stopped is None:
        raise LiveError("The analyzer didn't confirm the stop; check it's still on the master")
    for _ in range(20):  # wait for the header to be written
        try:
            sf.info(str(path))
            break
        except RuntimeError:
            time.sleep(0.1)
    report = rep.summary(rep.analyze_file(path))
    out = {"capture": str(path.relative_to(workspace.agent_dir())), "seconds": round(seconds, 2), **report}
    if idx != count - 1:
        out["warning"] = "The analyzer isn't last on the master: devices after it aren't in this capture."
    return compact(out)


def meter(client, seconds: float) -> dict:
    if _position(client)[0] is None:
        raise LiveError("The analyzer isn't on the master yet: analyzer(action='install')")
    link = m4l.link()
    t0 = time.time()
    link.send("/meter", 1)
    try:
        time.sleep(max(0.5, min(seconds, 30.0)))
    finally:
        link.send("/meter", 0)
    vals = [v for t, v in list(link.meter) if t >= t0 and len(v) == 4]
    if not vals:
        raise LiveError("No meter readings arrived; is the analyzer on the master and Live's audio running?")

    def dbfs(x: float) -> float | None:
        return round(20 * math.log10(x), 1) if x > 1e-6 else None

    peak_l, peak_r = max(v[0] for v in vals), max(v[1] for v in vals)
    rms_l = sorted(v[2] for v in vals)[len(vals) // 2]
    rms_r = sorted(v[3] for v in vals)[len(vals) // 2]
    return compact({"readings": len(vals), "playing": client.get("song", "is_playing")["is_playing"],
                    "peak_dbfs": [dbfs(peak_l), dbfs(peak_r)], "rms_dbfs_median": [dbfs(rms_l), dbfs(rms_r)],
                    "note": "Sample peaks and RMS from the master; use capture for LUFS/true peak/spectrum."})


@tool()
def analyzer(action: Literal["install", "status", "capture", "meter"] = "status", seconds: float | None = None,
             bars: float | None = None, start_bar: float | None = None, play: bool = True,
             name: str | None = None) -> dict:
    """The 'AbletonMCP Analyzer' Max for Live device on the master track (Live Suite).

    - install: copy it to your User Library and put it last on the master; checks it answers
    - status: whether it's there, last in the chain, and the sample rate
    - capture: record Live's actual master output (32-bit float WAV into agent/captures) for
      `seconds` or `bars`, optionally from start_bar (play=true starts and stops playback), then
      analyze it like an export. plan_master accepts the capture path, so mastering can iterate
      without manual exports. Captures are real-time: 30 s of audio takes 30 s.
    - meter: `seconds` (default 3) of live peak/RMS readings while Live plays."""
    client = get_client()
    if action == "install":
        return install(client)
    if action == "status":
        return status(client)
    if action == "capture":
        return capture(client, seconds, bars, start_bar, play, name)
    return meter(client, seconds or 3.0)
