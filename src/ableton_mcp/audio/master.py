"""Mastering planner: targets, an approximate render of the master chain (EQ, glue compressor,
utility, true-peak limiter) and settings chosen by simulating that chain on the actual mix.

The simulation approximates Live's EQ Eight / Glue Compressor / Limiter closely enough to pick
starting values (typically within ~0.5-1 LU); measuring a real export closes the loop.
"""

from __future__ import annotations

import copy
import math

import numpy as np
from scipy import ndimage, signal

from .loudness import db, loudness

TARGETS = {
    "streaming": {"lufs": -14.0, "true_peak": -1.0, "about": "Spotify/YouTube/Tidal normalize to about -14 LUFS"},
    "apple": {"lufs": -16.0, "true_peak": -1.0, "about": "Apple Music normalizes to about -16 LUFS"},
    "loud": {"lufs": -9.0, "true_peak": -1.0, "about": "Competitive modern pop/EDM loudness"},
    "club": {"lufs": -8.0, "true_peak": -0.5, "about": "DJ/club playback, no normalization"},
}
MAX_EQ_DB = 3.0          # mastering EQ moves stay gentle
TILT_RANGE = (-6.0, -2.0)  # dB/octave in our 1/3-octave measure; outside it, correct half the excess
GLUE_TARGET_GR = 1.5     # dB average gain reduction for "glue"
BLOCK_S = 0.001          # simulation block for envelopes


# -- filters (RBJ cookbook; close to EQ Eight's bell/shelf/high-pass shapes) --------------------


def _biquad(kind: str, freq: float, gain_db: float, q: float, fs: float) -> np.ndarray:
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * min(freq, fs * 0.45) / fs
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "bell":
        b = [1 + alpha * a, -2 * cw, 1 - alpha * a]
        den = [1 + alpha / a, -2 * cw, 1 - alpha / a]
    elif kind in ("low_shelf", "high_shelf"):
        sq = 2 * math.sqrt(a) * alpha
        if kind == "low_shelf":
            b = [a * ((a + 1) - (a - 1) * cw + sq), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - sq)]
            den = [(a + 1) + (a - 1) * cw + sq, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - sq]
        else:
            b = [a * ((a + 1) + (a - 1) * cw + sq), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - sq)]
            den = [(a + 1) - (a - 1) * cw + sq, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - sq]
    elif kind == "low_cut":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        den = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise ValueError(f"Unsupported band type {kind!r}")
    return np.concatenate([np.array(b) / den[0], np.array(den) / den[0]])


def apply_eq(x: np.ndarray, fs: float, bands: list[dict]) -> np.ndarray:
    if not bands:
        return x
    sos = np.stack([_biquad(b["type"], b["freq"], b.get("gain_db", 0.0), b.get("q", 0.71), fs) for b in bands])
    return signal.sosfilt(sos, x, axis=0)


# -- dynamics (block-based envelopes: fast enough for whole songs) -------------------------------


def _blocks(x: np.ndarray, fs: float) -> tuple[int, int]:
    n = max(1, int(round(BLOCK_S * fs)))
    return n, int(math.ceil(len(x) / n))


def _to_samples(gain_blocks: np.ndarray, n: int, length: int) -> np.ndarray:
    centers = np.arange(len(gain_blocks)) * n + n / 2
    return np.interp(np.arange(length), centers, gain_blocks)


def glue(x: np.ndarray, fs: float, threshold_db: float, ratio: float = 2.0, attack_ms: float = 10.0,
         release_ms: float = 1200.0) -> tuple[np.ndarray, np.ndarray]:
    """Model of Live's Glue Compressor. Returns (output, gain reduction dB per block).

    Calibrated against captures of Live's real Glue (threshold -10/-20 dB, ratio 2, attack 10 ms,
    release Auto) on a real mix: like the SSL bus compressor it models, the detector reacts to the
    *peak of L+R* (+6 dB on centred material) with a slow (~1.2 s) auto release. An RMS detector
    underestimated Live's gain reduction by ~3.5 dB; this model is within ~0.5 LU.
    """
    n, nb = _blocks(x, fs)
    pad = np.zeros((nb * n - len(x), x.shape[1]))
    xs = np.concatenate([x, pad])
    summed = xs[:, 0] + xs[:, 1] if xs.shape[1] > 1 else 2 * xs[:, 0]
    level = 20 * np.log10(np.abs(summed).reshape(nb, n).max(axis=1) + 1e-12)
    target_gr = np.maximum(0.0, level - threshold_db) * (1 - 1 / ratio)
    a_att = math.exp(-BLOCK_S / (attack_ms / 1000))
    a_rel = math.exp(-BLOCK_S / (release_ms / 1000))
    gr = np.empty(nb)
    g = 0.0
    for i, t in enumerate(target_gr):  # smoothing is recursive; ~1 ms blocks keep this fast
        g = a_att * g + (1 - a_att) * t if t > g else a_rel * g + (1 - a_rel) * t
        gr[i] = g
    gain = _to_samples(10 ** (-gr / 20), n, len(x))
    return x * gain[:, None], gr


def _block_true_peaks(x: np.ndarray, n: int, nb: int, oversample: int = 4) -> np.ndarray:
    """Per-block peak of the 4x-oversampled signal (both channels)."""
    peaks = np.zeros(nb)
    chunk = n * 4096
    for start in range(0, len(x), chunk):
        lo, hi = max(0, start - 64), min(len(x), start + chunk + 64)
        up = signal.resample_poly(x[lo:hi], oversample, 1, axis=0, window=("kaiser", 8.0))
        core = np.abs(up[(start - lo) * oversample:(min(len(x), start + chunk) - lo) * oversample]).max(axis=1)
        nblocks = int(math.ceil(len(core) / (n * oversample)))
        padded = np.concatenate([core, np.zeros(nblocks * n * oversample - len(core))])
        b0 = start // n
        peaks[b0:b0 + nblocks] = padded.reshape(nblocks, n * oversample).max(axis=1)[: nb - b0]
    return peaks


def limiter(x: np.ndarray, fs: float, input_gain_db: float, ceiling_db: float, release_ms: float = 100.0,
            lookahead_ms: float = 1.5, peaks: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """True-peak brickwall limiter. Returns (output, gain reduction dB per block).
    `peaks` (per-block true peaks of x at 0 dB gain) can be passed in to reuse between calls."""
    n, nb = _blocks(x, fs)
    if peaks is None:
        peaks = _block_true_peaks(x, n, nb)
    g_in = 10 ** (input_gain_db / 20)
    ceiling = 10 ** (ceiling_db / 20)
    need = np.minimum(1.0, ceiling / np.maximum(peaks * g_in, 1e-12))
    look = max(1, int(round(lookahead_ms / 1000 / BLOCK_S)))
    need = ndimage.minimum_filter1d(need, size=2 * look + 1, origin=0, mode="nearest")
    a_rel = math.exp(-BLOCK_S / (release_ms / 1000))
    gain = np.empty(nb)
    g = 1.0
    for i, t in enumerate(need):
        g = t if t < g else a_rel * g + (1 - a_rel) * t
        gain[i] = g
    out = x * (g_in * _to_samples(gain, n, len(x)))[:, None]
    return out, -20 * np.log10(np.maximum(gain, 1e-12))


# -- planning ---------------------------------------------------------------------------------------


def _round(v: float, step: float = 0.1) -> float:
    return round(round(v / step) * step, 2)


def tonal_moves(mix: dict, comparison: dict | None) -> list[dict]:
    """Gentle EQ moves, from a reference comparison if given, else only clear outliers."""
    moves = [{"type": "low_cut", "freq": 20.0, "slope": 12, "why": "remove inaudible sub-sonic energy"}]
    groups = mix["tonal_balance"]["groups_db"]
    if comparison:
        diff = comparison["tonal_difference_db"]
        plan = [("sub (20-60 Hz)", "low_shelf", 60, 0.71), ("low (60-250 Hz)", "bell", 120, 0.7),
                ("low-mid (250-500 Hz)", "bell", 350, 0.8), ("mid (500 Hz-2 kHz)", "bell", 1000, 0.7),
                ("high-mid (2-6 kHz)", "bell", 3500, 0.8), ("presence (6-12 kHz)", "high_shelf", 8000, 0.71)]
        for band, kind, freq, q in plan:
            d = diff.get(band, 0.0)
            if abs(d) >= 1.0:
                gain = max(-MAX_EQ_DB, min(MAX_EQ_DB, -0.6 * d))
                moves.append({"type": kind, "freq": float(freq), "gain_db": _round(gain), "q": q, "band": band,
                              "why": f"{band} is {d:+.1f} dB vs the reference; correcting about 60% of that"})
        return moves[:6]
    tilt = mix["tonal_balance"]["tilt_db_per_octave"]
    if tilt < TILT_RANGE[0]:
        gain = min(MAX_EQ_DB, 0.5 * (TILT_RANGE[0] - tilt) * 2)
        moves.append({"type": "high_shelf", "freq": 6000.0, "gain_db": _round(gain), "q": 0.71, "band": "presence (6-12 kHz)",
                      "why": f"the mix is dark (tilt {tilt:+.1f} dB/oct); lifting the top a little"})
    elif tilt > TILT_RANGE[1]:
        gain = -min(MAX_EQ_DB, 0.5 * (tilt - TILT_RANGE[1]) * 2)
        moves.append({"type": "high_shelf", "freq": 6000.0, "gain_db": _round(gain), "q": 0.71, "band": "presence (6-12 kHz)",
                      "why": f"the mix is bright (tilt {tilt:+.1f} dB/oct); taming the top a little"})
    neighbours = (groups["low (60-250 Hz)"] + groups["mid (500 Hz-2 kHz)"]) / 2
    if groups["low-mid (250-500 Hz)"] - neighbours > 3:
        moves.append({"type": "bell", "freq": 300.0, "gain_db": -1.5, "q": 0.8, "band": "low-mid (250-500 Hz)",
                      "why": "low-mid build-up (250-500 Hz well above its neighbours)"})
    if groups["high-mid (2-6 kHz)"] - groups["mid (500 Hz-2 kHz)"] > 0:
        moves.append({"type": "bell", "freq": 3500.0, "gain_db": -1.5, "q": 0.9, "band": "high-mid (2-6 kHz)",
                      "why": "2-6 kHz is as loud as the mids, which tends to sound harsh"})
    return moves


def stereo_moves(mix: dict, comparison: dict | None) -> dict:
    st = mix.get("stereo") or {}
    out: dict = {"bass_mono": False, "bass_freq": 120.0, "width": 100.0, "dc_filter": False, "why": []}
    low = (st.get("correlation_by_band") or {}).get("low (<150 Hz)")
    if low is not None and low < 0.9:
        out["bass_mono"] = True
        out["why"].append(f"low end correlation {low} < 0.9: mono below 120 Hz")
    if any(abs(v) > 0.003 for v in mix["levels"]["dc_offset"]):
        out["dc_filter"] = True
        out["why"].append("DC offset present")
    if comparison and "stereo" in comparison:
        sm = comparison["stereo"]["side_to_mid_db"]
        if sm[0] < sm[1] - 3 and sm[1] > -25:
            out["width"] = 115.0
            out["why"].append(f"narrower than the reference (side/mid {sm[0]} vs {sm[1]} dB)")
    if st.get("correlation") is not None and st["correlation"] < 0.2:
        out["width"] = 85.0
        out["why"].append(f"very wide/phasey (correlation {st['correlation']}): narrowing a little")
    return out


def plan(x: np.ndarray, fs: float, mix: dict, target_lufs: float, target_tp: float,
         comparison: dict | None = None) -> dict:
    """Choose a full chain for an unmastered mix by simulating it on the audio."""
    x = x.astype(np.float64)
    eq = tonal_moves(mix, comparison)
    y = apply_eq(x, fs, [m for m in eq])
    # Glue only a dynamic mix; already-squashed mixes go straight to the limiter.
    plr = mix["loudness"].get("plr_db") or 0.0
    glue_settings = None
    if plr >= 10:
        lo, hi = -40.0, 0.0
        for _ in range(14):
            mid = (lo + hi) / 2
            _, gr = glue(y, fs, mid)
            loud_blocks = gr[gr > 0.05]
            avg = float(loud_blocks.mean()) if len(loud_blocks) else 0.0
            lo, hi = (mid, hi) if avg > GLUE_TARGET_GR else (lo, mid)
        threshold = _round((lo + hi) / 2, 0.5)
        y, gr = glue(y, fs, threshold)
        glue_settings = {"threshold_db": threshold, "ratio": 2, "attack_ms": 10, "release": "auto",
                         "avg_gr_db": round(float(gr[gr > 0.05].mean()) if (gr > 0.05).any() else 0.0, 1),
                         "why": f"the mix is still dynamic (PLR {plr:.1f} dB): ~{GLUE_TARGET_GR} dB of glue"}
    stereo = stereo_moves(mix, comparison)
    if stereo["bass_mono"]:
        sos = signal.butter(4, stereo["bass_freq"], "lowpass", fs=fs, output="sos")
        low = signal.sosfilt(sos, y, axis=0)
        y = y - low + low.mean(axis=1, keepdims=True)
    # Limiter input gain: bisection on the simulated loudness.
    n, nb = _blocks(y, fs)
    peaks = _block_true_peaks(y, n, nb)
    pre_lufs = loudness(y, fs)["integrated_lufs"]
    lo, hi = -6.0, 24.0
    best = None
    for _ in range(18):
        mid = (lo + hi) / 2
        out, gr = limiter(y, fs, mid, target_tp, peaks=peaks)
        lufs = loudness(out, fs)["integrated_lufs"]
        best = (mid, lufs, gr)
        if abs(lufs - target_lufs) < 0.05:
            break
        lo, hi = (mid, hi) if lufs < target_lufs else (lo, mid)
    gain, predicted_lufs, gr = best
    cost = gain - (predicted_lufs - pre_lufs)  # loudness the limiter "eats" instead of passing on
    lim = {"input_gain_db": _round(gain), "ceiling_db": target_tp, "mode": "True Peak", "auto_release": True,
           "gr_p95_db": round(float(np.percentile(gr, 95)), 1) + 0.0, "max_gr_db": round(float(gr.max()), 1) + 0.0,
           "time_over_3db_gr": f"{float(np.mean(gr > 3)):.0%}", "loudness_cost_db": round(max(0.0, cost), 1)}
    warnings = []
    if lim["gr_p95_db"] > 4 or lim["max_gr_db"] > 9 or cost > 2.5:
        warnings.append(f"Heavy limiting to reach {target_lufs} LUFS (95% of the time under "
                        f"{lim['gr_p95_db']} dB, peaks {lim['max_gr_db']} dB, {cost:.1f} dB of loudness lost to "
                        "limiting): transients will flatten. Consider a lower target, or more compression/"
                        "saturation in the mix itself.")
    return {"eq": eq, "glue": glue_settings, "utility": stereo, "limiter": lim,
            "predicted": {"integrated_lufs": round(predicted_lufs, 1), "true_peak_dbtp": target_tp},
            "warnings": warnings}


def corrections(measured: dict, target_lufs: float, target_tp: float, current: dict,
                comparison: dict | None = None) -> dict:
    """Small changes to an applied chain, from an export made with it."""
    L = measured["loudness"]
    err = target_lufs - (L["integrated_lufs"] or target_lufs)
    tp_over = L["true_peak_dbtp"] - target_tp
    new = copy.deepcopy(current)
    changes = []
    if abs(err) > 0.3:
        step = max(-6.0, min(6.0, err))
        new["limiter"]["input_gain_db"] = _round(current["limiter"]["input_gain_db"] + step)
        changes.append(f"limiter input gain {step:+.1f} dB (measured {L['integrated_lufs']} LUFS, target {target_lufs})")
    if tp_over > 0.05:
        new["limiter"]["ceiling_db"] = _round(current["limiter"]["ceiling_db"] - tp_over - 0.1)
        changes.append(f"ceiling down {tp_over + 0.1:.1f} dB (measured {L['true_peak_dbtp']} dBTP)")
    if comparison:
        diff = comparison["tonal_difference_db"]
        for move in new["eq"]:
            band = move.get("band")
            d = diff.get(band, 0.0) if band else 0.0
            if abs(d) >= 1.5:
                adj = max(-1.0, min(1.0, -0.5 * d))
                move["gain_db"] = _round(max(-MAX_EQ_DB, min(MAX_EQ_DB, move.get("gain_db", 0.0) + adj)))
                changes.append(f"EQ {move['type']} {move['freq']:g} Hz {adj:+.1f} dB ({band} still {d:+.1f} dB vs reference)")
    within = abs(err) <= 0.5 and tp_over <= 0.1
    return {"settings": new, "changes": changes, "within_tolerance": within,
            "error": {"lufs": round(-err, 2), "true_peak_over_db": round(max(0.0, tp_over), 2)}}

