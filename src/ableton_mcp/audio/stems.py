"""Multitrack (stem) analysis: masking over time, high-pass candidates, resonances, low-end
mono, buildups, and concrete EQ suggestions.

Stems are assumed to start together (as Live exports them). Levels are in dBFS of the
mid signal; frames are ~85 ms (4096 samples at 48 kHz, 50% overlap).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import signal

from .loudness import loudness
from .spectrum import THIRD_OCTAVES

BANDS = [f for f in THIRD_OCTAVES if 31.5 <= f <= 16000]
NFFT, HOP = 4096, 2048
SIGNIFICANT_DB = 10.0      # a stem matters in a frame/band if within this of the mix there
CORE_DB = 12.0             # a band is "core" for a stem if within this of its loudest band
OVERLAP_MIN = 0.35         # share of the sparser stem's active frames where the other competes
MIN_FRAMES = 5             # ignore stems that matter in a band for fewer frames than this

ROLE_KEYWORDS = [  # first match wins; short codes must match a whole token
    ("kick", ["kick", "kik", "bassdrum"], ["bd", "kd"]),
    ("bass", ["bass", "sub", "808", "reese"], []),
    ("snare", ["snare", "snr"], ["sd", "sn"]),
    ("clap", ["clap"], ["cl", "cp", "clp"]),
    ("hats", ["hat", "hihat", "cymbal", "ride", "crash"], ["hh", "ch", "oh", "cy", "cym"]),
    ("perc", ["perc", "shaker", "tom", "conga", "bongo", "tamb", "rim"], ["sh", "pc", "pc1", "pc2", "lt", "mt", "ht", "rs"]),
    ("vocal", ["vox", "vocal", "voice", "choir", "adlib"], ["vo", "bv", "bvs"]),
    ("lead", ["lead", "synth", "pluck", "arp", "melody", "hook"], []),
    ("keys", ["keys", "piano", "rhodes", "organ", "epiano", "wurli"], []),
    ("pad", ["pad", "string", "strings", "atmos", "drone", "texture"], []),
    ("guitar", ["guitar", "gtr"], []),
    ("fx", ["fx", "riser", "impact", "sweep", "noise", "uplifter", "downlifter"], []),
    ("drums", ["drum", "drums", "beat", "kit", "loop", "break"], []),
]

# How strongly each role should own a region (higher keeps its energy; lower gets cut).
REGIONS = [("low", 0, 150), ("low-mid", 150, 500), ("mid", 500, 2000), ("high-mid", 2000, 6000), ("high", 6000, 24000)]
PRIORITY = {
    "kick": {"low": 3, "low-mid": 1, "mid": 1, "high-mid": 2, "high": 1},
    "bass": {"low": 3, "low-mid": 2, "mid": 1, "high-mid": 1, "high": 0},
    "snare": {"low": 1, "low-mid": 2, "mid": 2, "high-mid": 2, "high": 1},
    "clap": {"low": 0, "low-mid": 1, "mid": 2, "high-mid": 2, "high": 1},
    "hats": {"low": 0, "low-mid": 0, "mid": 1, "high-mid": 2, "high": 3},
    "perc": {"low": 0, "low-mid": 1, "mid": 1, "high-mid": 2, "high": 2},
    "vocal": {"low": 0, "low-mid": 2, "mid": 3, "high-mid": 3, "high": 2},
    "lead": {"low": 0, "low-mid": 1, "mid": 3, "high-mid": 2, "high": 2},
    "keys": {"low": 1, "low-mid": 2, "mid": 2, "high-mid": 1, "high": 1},
    "pad": {"low": 0, "low-mid": 1, "mid": 1, "high-mid": 1, "high": 1},
    "guitar": {"low": 0, "low-mid": 2, "mid": 2, "high-mid": 2, "high": 1},
    "fx": {"low": 0, "low-mid": 0, "mid": 1, "high-mid": 1, "high": 1},
    "drums": {"low": 2, "low-mid": 2, "mid": 2, "high-mid": 2, "high": 2},
    "other": {"low": 1, "low-mid": 1, "mid": 1, "high-mid": 1, "high": 1},
}
# Suggested high-pass per role (None = keep the lows).
HIGH_PASS = {"kick": None, "bass": None, "drums": None, "snare": 60, "clap": 150, "hats": 250, "perc": 120,
             "vocal": 90, "lead": 120, "keys": 80, "pad": 120, "guitar": 80, "fx": 60, "other": None}


PERCUSSIVE = {"kick", "snare", "clap", "hats", "perc", "drums"}
MAX_EQ_PER_STEM = 3


def region_of(freq: float) -> str:
    return next(name for name, lo, hi in REGIONS if lo <= freq < hi)


def stem_label(path: Path) -> str:
    """'DT2 09 BASS 0002 [2026-10-08 200140].wav' -> 'DT2 09 BASS'; '01 Kick.wav' -> '01 Kick'."""
    s = re.sub(r"\[.*?\]", "", path.stem)
    s = re.sub(r"\s+\d{3,}\s*$", "", s.strip())
    return s.strip(" -_") or path.stem


def guess_role(label: str) -> str:
    tokens = [t for t in re.split(r"[^a-z0-9]+", label.lower()) if t]
    joined = " ".join(tokens)
    if "bass drum" in joined:
        return "kick"
    for role, words, codes in ROLE_KEYWORDS:
        if any(w in joined for w in words) or any(t in codes for t in tokens):
            return role
    return "other"


@dataclass
class Stem:
    path: Path
    label: str
    role: str
    bands: np.ndarray            # (frames, len(BANDS)) power
    fine_db: np.ndarray          # time-averaged spectrum in dB, NFFT/2+1 bins
    fine_frames_db: np.ndarray   # per-frame dB spectrum (frames, bins) on a decimated grid for resonance checks
    lufs: float
    peak_dbfs: float
    low_corr: float | None
    info: dict = field(default_factory=dict)


def _band_matrix(fs: float) -> np.ndarray:
    freqs = np.fft.rfftfreq(NFFT, 1 / fs)
    m = np.zeros((len(freqs), len(BANDS)))
    for j, fc in enumerate(BANDS):
        m[(freqs >= fc / 2 ** (1 / 6)) & (freqs < fc * 2 ** (1 / 6)), j] = 1.0
    return m


def _frames_power(mid: np.ndarray, fs: float) -> np.ndarray:
    _, _, z = signal.stft(mid, fs, window="hann", nperseg=NFFT, noverlap=NFFT - HOP, boundary=None, padded=False)
    return (np.abs(z) ** 2).T  # (frames, bins)


def _low_corr(x: np.ndarray, fs: float) -> float | None:
    sos = signal.butter(4, 150, "lowpass", fs=fs, output="sos")
    lo = signal.sosfilt(sos, x.astype(np.float64), axis=0)
    a, b = lo[:, 0], lo[:, 1]
    den = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    return round(float(np.dot(a, b) / den), 2) if den > 1e-12 else None


def load_stem(path: Path, data: np.ndarray, fs: float, bmat: np.ndarray) -> Stem:
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    data = data[:, :2]
    mid = data.mean(axis=1).astype(np.float64)
    power = _frames_power(mid, fs)
    label = stem_label(path)
    with np.errstate(divide="ignore"):
        fine_db = 10 * np.log10(power.mean(axis=0) + 1e-20)
        fine_frames = 10 * np.log10(power[:, ::2] + 1e-20)  # half resolution is plenty for persistence
    L = loudness(data, fs)["integrated_lufs"]
    peak = float(np.max(np.abs(data)))
    return Stem(path, label, guess_role(label), power @ bmat, fine_db, fine_frames,
                round(L, 1) if math.isfinite(L) else -math.inf,
                round(20 * math.log10(peak), 1) if peak > 0 else -math.inf, _low_corr(data, fs))


# -- per-stem features --------------------------------------------------------------


def _db(x: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return 10 * np.log10(x + 1e-20)


def energy_range(stem: Stem) -> tuple[float, float, float]:
    """(5% point, dominant band, 95% point) of the stem's average band energy, in Hz."""
    avg = stem.bands.mean(axis=0)
    total = avg.sum()
    if total <= 0:
        return 0.0, 0.0, 0.0
    cum = np.cumsum(avg) / total
    return (float(BANDS[int(np.searchsorted(cum, 0.05))]), float(BANDS[int(np.argmax(avg))]),
            float(BANDS[min(int(np.searchsorted(cum, 0.95)), len(BANDS) - 1)]))


def core_bands(stem: Stem) -> np.ndarray:
    avg = _db(stem.bands.mean(axis=0))
    return avg >= avg.max() - CORE_DB


def sustain_db(stem: Stem) -> float:
    """Median frame level relative to the loudest frame: near 0 = steady tone/pad, very negative = hits."""
    frame = _db(stem.bands.sum(axis=1))
    active = frame[frame > frame.max() - 60]
    return float(np.median(active) - frame.max()) if len(active) else -60.0


def content_role(stem: Stem) -> str | None:
    """What the audio behaves like, when it clearly contradicts a melodic/percussive name."""
    _, dom, _ = energy_range(stem)
    if dom and dom < 100:
        return "bass" if sustain_db(stem) > -12 else "kick"
    return None


def high_pass_suggestion(stem: Stem, fs: float) -> dict | None:
    cutoff = HIGH_PASS.get(stem.role)
    if cutoff is None:
        return None
    freqs = np.fft.rfftfreq(NFFT, 1 / fs)
    power = 10 ** (stem.fine_db / 10)
    total = power[freqs >= 20].sum()
    below = power[(freqs >= 20) & (freqs < cutoff)].sum() / total if total > 0 else 0.0
    if below < 0.02 or below > 0.5:  # over half below the cutoff is the part itself, not junk
        return None
    # Don't cut into the part's body: walk down from its loudest band while it stays within
    # 20 dB, and stay below where that body starts.
    avg = _db(stem.bands.mean(axis=0))
    i = int(np.argmax(avg))
    while i > 0 and avg[i - 1] >= avg.max() - 20:
        i -= 1
    freq = max(30.0, min(cutoff, BANDS[i] * 0.7))
    return {"type": "low_cut", "freq": round(freq), "slope": 12,
            "why": f"{below:.0%} of its energy sits below {cutoff} Hz, which a {stem.role} rarely needs in a mix"}


def resonances(stem: Stem, fs: float, min_prominence: float = 8.0) -> list[dict]:
    """Narrow peaks that stand out from the local spectrum and persist over time."""
    freqs = np.fft.rfftfreq(NFFT, 1 / fs)
    grid = np.geomspace(150, min(9000, fs / 2.2), 200)  # 1/30-octave
    spec = np.interp(grid, freqs, stem.fine_db)
    baseline = signal.medfilt(spec, 21)  # ~2/3 octave
    prominence = spec - baseline
    peaks, props = signal.find_peaks(prominence, height=min_prominence, distance=6)
    out = []
    ffreqs = freqs[::2]
    active = stem.fine_frames_db.max(axis=1) > stem.fine_frames_db.max() - 30
    frames = stem.fine_frames_db[active]
    for i in peaks:
        f0 = grid[i]
        k = int(np.argmin(np.abs(ffreqs - f0)))
        lo, hi = int(np.searchsorted(ffreqs, f0 / 2 ** (1 / 6))), int(np.searchsorted(ffreqs, f0 * 2 ** (1 / 6)))
        if hi - lo < 3 or len(frames) == 0:
            continue
        local = np.median(frames[:, lo:hi], axis=1)
        persistence = float(np.mean(frames[:, k] - local >= 4.0))
        if persistence >= 0.6:
            out.append({"freq": round(float(f0)), "prominence_db": round(float(prominence[i]), 1),
                        "persistence": round(persistence, 2)})
    return out


# -- masking ------------------------------------------------------------------------------


def masking(stems: list[Stem]) -> list[dict]:
    n_frames = min(len(s.bands) for s in stems)
    P = np.stack([s.bands[:n_frames] for s in stems])          # (stems, frames, bands)
    mix = _db(P.sum(axis=0))
    level = _db(P)
    significant = (level >= mix[None] - SIGNIFICANT_DB) & (level > -75)
    cores = [core_bands(s) for s in stems]
    conflicts = []
    for a in range(len(stems)):
        for b in range(a + 1, len(stems)):
            both = (significant[a] & significant[b]).sum(axis=0)
            # Judge from the sparser part's point of view: a kick hitting 4x a bar is masked on
            # every hit by a bass that plays all the time, even though the bass rarely "needs" it.
            sparser = np.minimum(significant[a].sum(axis=0), significant[b].sum(axis=0))
            enough = sparser >= max(MIN_FRAMES, 0.02 * n_frames)
            overlap = np.where(enough, both / np.maximum(sparser, 1), 0.0)
            hot = (overlap >= OVERLAP_MIN) & cores[a] & cores[b]
            j = 0
            while j < len(BANDS):
                if not hot[j]:
                    j += 1
                    continue
                k = j
                while k + 1 < len(BANDS) and hot[k + 1]:
                    k += 1
                lo, hi = BANDS[j] / 2 ** (1 / 6), BANDS[k] * 2 ** (1 / 6)
                ov = float(overlap[j:k + 1].mean())
                la = float(np.mean(level[a][:, j:k + 1][significant[a][:, j:k + 1]])) if significant[a][:, j:k + 1].any() else -120
                lb = float(np.mean(level[b][:, j:k + 1][significant[b][:, j:k + 1]])) if significant[b][:, j:k + 1].any() else -120
                conflicts.append({"a": a, "b": b, "low_hz": round(lo), "high_hz": round(hi),
                                  "center_hz": round(math.sqrt(lo * hi)), "overlap": round(ov, 2),
                                  "level_db": [round(la, 1), round(lb, 1)],
                                  "score": round(ov * math.sqrt(k - j + 1), 3)})
                j = k + 1
    conflicts.sort(key=lambda c: -c["score"])
    return conflicts


def _cut_target(stems: list[Stem], c: dict) -> int:
    region = region_of(c["center_hz"])
    pa = PRIORITY.get(stems[c["a"]].role, PRIORITY["other"])[region]
    pb = PRIORITY.get(stems[c["b"]].role, PRIORITY["other"])[region]
    if pa != pb:
        return c["a"] if pa < pb else c["b"]
    return c["a"] if c["level_db"][0] < c["level_db"][1] else c["b"]  # the quieter one yields


def merge_eq(suggestions: list[dict]) -> list[dict]:
    """Merge overlapping bell cuts on the same stem (keep the deepest cut over the union of ranges),
    and keep at most MAX_EQ_PER_STEM EQ moves per stem."""
    out, bells = [], {}
    for sug in suggestions:
        eq = sug.get("eq")
        if not eq or eq["type"] != "bell":
            out.append(sug)
            continue
        half = eq["freq"] / eq["q"] / 2
        lo, hi = max(20.0, eq["freq"] - half), eq["freq"] + half
        for prev in bells.setdefault(sug["stem"], []):
            if lo <= prev["hi"] and hi >= prev["lo"]:
                prev["lo"], prev["hi"] = min(prev["lo"], lo), max(prev["hi"], hi)
                prev["gain"] = min(prev["gain"], eq["gain_db"])
                prev["why"].append(sug["why"])
                break
        else:
            bells[sug["stem"]].append({"lo": lo, "hi": hi, "gain": eq["gain_db"], "why": [sug["why"]], "sug": sug})
    for stem, items in bells.items():
        for b in items:
            center = math.sqrt(b["lo"] * b["hi"])
            q = round(min(4.0, max(0.7, center / max(1.0, b["hi"] - b["lo"]))), 2)
            merged = {**b["sug"], "eq": {"type": "bell", "freq": round(center), "gain_db": b["gain"], "q": q},
                      "why": " / ".join(b["why"])}
            out.append(merged)
    counts: dict[str, int] = {}
    kept = []
    for sug in out:
        if "eq" in sug:
            counts[sug["stem"]] = counts.get(sug["stem"], 0) + 1
            if counts[sug["stem"]] > MAX_EQ_PER_STEM:
                continue
        kept.append(sug)
    return kept


def bell_for(c: dict, depth_db: float) -> dict:
    q = c["center_hz"] / max(1.0, c["high_hz"] - c["low_hz"])
    return {"type": "bell", "freq": c["center_hz"], "gain_db": round(-depth_db, 1), "q": round(min(4.0, max(0.7, q)), 2)}


# -- report ---------------------------------------------------------------------------------


SILENT_DBFS = -90.0


def analyze(stems: list[Stem], fs: float, max_conflicts: int = 8, roles: dict[str, str] | None = None) -> dict:
    silent = [s.label for s in stems if not s.peak_dbfs > SILENT_DBFS]
    stems = [s for s in stems if s.peak_dbfs > SILENT_DBFS]
    if not stems:
        return {"stems": [], "silent_stems": silent, "conflicts": [], "suggestions": []}
    mismatches = {}
    for s in stems:
        override = next((r for name, r in (roles or {}).items() if name.lower() in s.label.lower()), None)
        if override:
            if override not in PRIORITY:
                raise ValueError(f"Unknown role {override!r}; use {', '.join(PRIORITY)}")
            s.role = override
            continue
        actual = content_role(s)
        if actual and s.role not in ("kick", "bass", "drums", "other", actual):
            _, dom, _ = energy_range(s)
            mismatches[s.label] = (f"Named like a {s.role}, but it is dominated by "
                                   f"{'a sustained tone' if actual == 'bass' else 'low hits'} around {dom:g} Hz; "
                                   f"treated as {actual}. Pass roles={{...}} if that's wrong.")
            s.role = actual
        elif actual and s.role == "other":
            s.role = actual
    total_power = sum(float(s.bands.sum()) for s in stems) or 1e-20
    per_stem, suggestions = [], []
    hp_freq: dict[str, float] = {}
    for s in stems:
        lo5, dom, hi95 = energy_range(s)
        hp = high_pass_suggestion(s, fs)
        if hp:
            hp_freq[s.label] = hp["freq"]
        res = [] if s.role in ("kick", "snare", "perc", "drums", "hats", "clap") else resonances(s, fs)
        entry = {"stem": s.label, "role": s.role, "lufs": s.lufs, "peak_dbfs": s.peak_dbfs,
                 "share_of_energy": round(float(s.bands.sum()) / total_power, 3),
                 "range_hz": [lo5, hi95], "dominant_hz": dom, "low_end_correlation": s.low_corr,
                 "sustain_db": round(sustain_db(s), 1)}
        if s.label in mismatches:
            entry["role_note"] = mismatches[s.label]
        if res:
            entry["resonances"] = res
        per_stem.append(entry)
        if hp:
            suggestions.append({"stem": s.label, "eq": {k: hp[k] for k in ("type", "freq", "slope")}, "why": hp["why"]})
        for r in res[:2]:
            suggestions.append({"stem": s.label, "eq": {"type": "bell", "freq": r["freq"], "gain_db": -3.0, "q": 5.0},
                                "why": f"Persistent resonance {r['prominence_db']} dB above its surroundings "
                                       f"in {r['persistence']:.0%} of the time"})
        bass_share = float(s.bands[:, [i for i, f in enumerate(BANDS) if f <= 125]].sum()) / max(float(s.bands.sum()), 1e-20)
        if s.low_corr is not None and s.low_corr < 0.8 and bass_share > 0.1:
            suggestions.append({"stem": s.label, "utility": {"bass_mono_hz": 120},
                                "why": f"Low end is stereo (correlation {s.low_corr}); mono it below ~120 Hz"})
    conflicts = masking(stems)
    shown, dense_top = [], set()
    for c in conflicts:
        a, b = stems[c["a"]], stems[c["b"]]
        if a.role in PERCUSSIVE and b.role in PERCUSSIVE and c["low_hz"] >= 2000:
            # Short transients up top rarely mask each other; overlapping cymbals/perc is texture.
            dense_top.update([a.label, b.label])
            continue
        if len(shown) >= max_conflicts:
            continue
        loser = _cut_target(stems, c)
        keeper = c["b"] if loser == c["a"] else c["a"]
        entry = {"stems": [a.label, b.label], "range_hz": [c["low_hz"], c["high_hz"]], "overlap": c["overlap"],
                 "levels_db": c["level_db"], "suggest_cut": stems[loser].label}
        shown.append(entry)
        roles = {a.role, b.role}
        depth = round(2.0 + 2.0 * c["overlap"], 1)
        region = region_of(c["center_hz"])
        same = PRIORITY.get(a.role, PRIORITY["other"])[region] == PRIORITY.get(b.role, PRIORITY["other"])[region]
        reason = f"{stems[keeper].label} is louder there" if same else f"a {stems[keeper].role} should own this range"
        why = (f"Masks {stems[keeper].label} at {c['low_hz']}-{c['high_hz']} Hz in {c['overlap']:.0%} of the "
               f"moments it plays; {reason}")
        if roles == {"kick", "bass"} and c["center_hz"] < 200:
            kick, bass = (a, b) if a.role == "kick" else (b, a)
            suggestions.append({"stem": bass.label, "sidechain_from": kick.label,
                                "why": "Kick and bass share the low end at the same time: duck the bass with "
                                       "a sidechained Compressor (fast attack, ~100 ms release) or carve with EQ"})
            suggestions.append({"stem": bass.label, "eq": bell_for(c, depth), "why": why})
        elif c["high_hz"] > hp_freq.get(stems[loser].label, 0) * 1.2:  # the high-pass already handles it
            suggestions.append({"stem": stems[loser].label, "eq": bell_for(c, depth), "why": why})
    suggestions = merge_eq(suggestions)
    # Buildups across the whole multitrack (heuristics; pink noise would be about -10 dB per octave).
    sums = np.sum([s.bands.mean(axis=0) for s in stems], axis=0)
    tot = sums.sum() or 1e-20

    def share(lo: float, hi: float) -> float:
        return 10 * math.log10(max(sums[[i for i, f in enumerate(BANDS) if lo <= f < hi]].sum(), 1e-20) / tot)

    buildups = []
    if share(200, 500) > -7:
        top = sorted(stems, key=lambda s: -float(s.bands[:, [i for i, f in enumerate(BANDS) if 200 <= f < 500]].sum()))
        buildups.append({"issue": f"Low-mid build-up (200-500 Hz at {share(200, 500):.1f} dB of the total)",
                         "main_contributors": [s.label for s in top[:3]]})
    if share(2000, 5000) > -9:
        top = sorted(stems, key=lambda s: -float(s.bands[:, [i for i, f in enumerate(BANDS) if 2000 <= f < 5000]].sum()))
        buildups.append({"issue": f"Possible harshness (2-5 kHz at {share(2000, 5000):.1f} dB of the total)",
                         "main_contributors": [s.label for s in top[:3]]})
    loudest = max((s.lufs for s in stems if math.isfinite(s.lufs)), default=-math.inf)
    quiet = [s.label for s in stems if math.isfinite(s.lufs) and s.lufs < loudest - 25]
    if dense_top:
        buildups.append({"issue": "Dense top end: several percussive parts overlap above 2 kHz. That's normal texture; "
                                  "if it feels busy, use panning, levels or arrangement rather than EQ cuts",
                         "main_contributors": sorted(dense_top)})
    return {"stems": per_stem, "silent_stems": silent or None, "conflicts": shown, "buildups": buildups,
            "nearly_inaudible": quiet or None, "suggestions": suggestions}
