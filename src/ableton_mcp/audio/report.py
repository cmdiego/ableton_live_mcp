"""Analyze audio files into reports; resolve and list files in the agent folder."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import workspace
from .loudness import db, levels, loudness, true_peak
from .spectrum import tonal_balance
from .stereo import stereo_image

AUDIO_EXTS = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg")
MAX_MINUTES = 30
# Playback reference levels used by loudness normalization.
STREAMING = {"Spotify / YouTube / Tidal": -14.0, "Apple Music": -16.0, "Amazon Music": -13.0}

_cache: dict[tuple[str, float, int], dict] = {}


def list_files(folders: list[str], pattern: str | None = None) -> list[dict]:
    out = []
    for name in folders:
        root = workspace.folder(name)
        for p in root.rglob("*"):
            if p.suffix.lower() not in AUDIO_EXTS or not p.is_file():
                continue
            if pattern and pattern.lower() not in str(p.relative_to(root)).lower():
                continue
            try:
                info = sf.info(str(p))
            except RuntimeError:
                continue
            st = p.stat()
            out.append({
                "path": str(p.relative_to(workspace.agent_dir())),
                "seconds": round(info.duration, 1), "sample_rate": info.samplerate, "channels": info.channels,
                "format": info.subtype_info, "size_mb": round(st.st_size / 1e6, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)), "_mtime": st.st_mtime,
            })
    out.sort(key=lambda f: f["_mtime"], reverse=True)
    for f in out:
        f.pop("_mtime")
    return out


def resolve_path(path: str | None) -> Path:
    """'latest' (or None) = newest file in agent/exports; else absolute, or relative to agent/,
    agent/exports/ or agent/references/."""
    if path == "latest_capture":
        files = list_files(["captures"])
        if not files:
            raise ValueError("No captures yet: analyzer(action='capture') records Live's master output")
        return workspace.agent_dir() / files[0]["path"]
    if not path or path == "latest":
        files = list_files(["exports"])
        if not files:
            raise ValueError(f"No audio in {workspace.folder('exports')}. Export a mix from Live there first "
                             "(File > Export Audio/Video).")
        return workspace.agent_dir() / files[0]["path"]
    p = Path(path).expanduser()
    candidates = [p] if p.is_absolute() else [workspace.agent_dir() / p, workspace.folder("exports") / p,
                                               workspace.folder("references") / p, workspace.folder("captures") / p]
    for c in candidates:
        if c.is_file():
            return c
    names = [f["path"] for f in list_files(["exports", "references", "captures"])][:10]
    raise ValueError(f"Audio file {path!r} not found. Recent files: {names or 'none'}")


def load(path: Path) -> tuple[np.ndarray, int, object]:
    info = sf.info(str(path))
    if info.duration > MAX_MINUTES * 60:
        raise ValueError(f"{path.name} is {info.duration / 60:.0f} min long; the limit is {MAX_MINUTES} min")
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    return data, sr, info


def _series(values: np.ndarray, step_s: float, every_s: float) -> list[list[float]]:
    """Downsample a 0.1 s series to [time, value] pairs every `every_s` seconds, skipping
    silence (below -70 LUFS; the report's silence_s covers it)."""
    k = max(1, int(round(every_s / step_s)))
    return [[round(i * step_s, 1), round(float(v), 1)]
            for i, v in enumerate(values) if i % k == 0 and math.isfinite(v) and v > -70]


def _silence(momentary: np.ndarray, step: float = 0.1) -> tuple[float, float]:
    loud = np.flatnonzero(momentary > -60)
    if len(loud) == 0:
        return round(len(momentary) * step, 1), 0.0
    return round(float(loud[0]) * step, 1), round(float(len(momentary) - 1 - loud[-1]) * step, 1)


def issues(r: dict) -> list[dict]:
    """Technical problems worth fixing (not taste)."""
    out = []
    L, lv, st = r["loudness"], r["levels"], r.get("stereo", {})

    def add(severity: str, text: str) -> None:
        out.append({"severity": severity, "issue": text})

    if lv["clipped_runs"]:
        add("high", f"{lv['clipped_runs']} runs of clipped samples (3+ samples at full scale)")
    if L["true_peak_dbtp"] > -1.0:
        add("medium", f"True peak {L['true_peak_dbtp']:+.1f} dBTP: lossy streaming encodes can clip. Aim for -1 dBTP or lower.")
    if any(abs(v) > 0.003 for v in lv["dc_offset"]):
        add("low", "DC offset present (wastes headroom; a high-pass or Utility's DC filter removes it)")
    low_corr = (st.get("correlation_by_band") or {}).get("low (<150 Hz)")
    if low_corr is not None and low_corr < 0.6:
        add("medium", f"Low end is not mono-safe (correlation {low_corr} below 150 Hz). Narrow or mono the bass/kick.")
    if st.get("correlation") is not None and st["correlation"] < 0:
        add("high", f"Overall L/R correlation {st['correlation']}: phase problems, parts will cancel in mono")
    if st.get("mono_loudness_change_db") is not None and st["mono_loudness_change_db"] < -3:
        add("medium", f"Loses {-st['mono_loudness_change_db']:.1f} dB when summed to mono")
    if math.isfinite(L["integrated_lufs"]) and L["plr_db"] < 6:
        add("low", f"Peak-to-loudness ratio {L['plr_db']:.1f} dB: heavily limited, transients may be flattened")
    if r["silence_s"]["start"] > 1.0:
        add("low", f"{r['silence_s']['start']} s of silence at the start")
    if r["silence_s"]["end"] > 3.0:
        add("low", f"{r['silence_s']['end']} s of silence or tail at the end")
    return out


def analyze_file(path: Path) -> dict:
    st = path.stat()
    key = (str(path), st.st_mtime, st.st_size)
    if key in _cache:
        return _cache[key]
    data, sr, info = load(path)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)  # analyze mono as centered stereo
        mono_source = True
    else:
        data = data[:, :2]
        mono_source = False
    L = loudness(data, sr)
    tp = db(true_peak(data))
    integrated = L["integrated_lufs"]
    report = {
        "file": str(path),
        "format": {"seconds": round(info.duration, 2), "sample_rate": sr, "channels": info.channels,
                   "encoding": info.subtype_info},
        "loudness": {
            "integrated_lufs": round(integrated, 2) if math.isfinite(integrated) else None,
            "true_peak_dbtp": round(tp, 2),
            "lra_lu": round(L["lra_lu"], 1),
            "max_short_term_lufs": round(L["max_short_term_lufs"], 1),
            "max_momentary_lufs": round(L["max_momentary_lufs"], 1),
            "plr_db": round(tp - integrated, 1) if math.isfinite(integrated) else None,
            "psr_db": round(tp - L["max_short_term_lufs"], 1) if math.isfinite(L["max_short_term_lufs"]) else None,
        },
        "streaming_playback_gain_db": {name: round(target - integrated, 1) for name, target in STREAMING.items()}
        if math.isfinite(integrated) else None,
        "levels": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in levels(data).items()},
        "tonal_balance": tonal_balance(data, sr),
        "stereo": {"mono_source": True} if mono_source else stereo_image(data, sr, integrated),
        "silence_s": dict(zip(("start", "end"), _silence(L["momentary_series"]))),
        "short_term_lufs_curve": _series(L["short_term_series"], 0.1, 1.0),
    }
    report["levels"]["dc_offset"] = [round(v, 4) for v in report["levels"]["dc_offset"]]
    report["issues"] = issues(report)
    _cache[key] = report
    out = workspace.folder("reports") / f"{path.stem}.analysis.json"
    out.write_text(json.dumps(report, indent=1))
    report["report_file"] = str(out.relative_to(workspace.agent_dir()))
    return report


def summary(report: dict, curve_every_s: float = 4.0) -> dict:
    """The report without the bulky parts: 1/3-octave bands and a coarse loudness curve."""
    s = {k: v for k, v in report.items() if k not in ("short_term_lufs_curve", "tonal_balance")}
    tb = report["tonal_balance"]
    s["tonal_balance"] = {k: tb[k] for k in ("groups_db", "tilt_db_per_octave", "centroid_hz")}
    k = max(1, int(curve_every_s))
    s["short_term_lufs_curve"] = report["short_term_lufs_curve"][::k]
    return s
