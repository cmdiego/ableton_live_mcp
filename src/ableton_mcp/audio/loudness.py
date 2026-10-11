"""Loudness and level per ITU-R BS.1770-4 / EBU R128 (integrated, short-term, momentary, LRA),
sample/true peak, RMS and clipping. Signals are float arrays shaped (frames, channels)."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

ABS_GATE = -70.0


def k_weighting(fs: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """The two BS.1770 pre-filters for any sample rate (libebur128's analog-matched design;
    exact ITU coefficients at 48 kHz)."""
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10 ** (gain / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf_b = np.array([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0])
    shelf_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    hp_b = np.array([1.0, -2.0, 1.0])
    hp_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    return [(shelf_b, shelf_a), (hp_b, hp_a)]


def _weighted_power(x: np.ndarray, fs: float) -> np.ndarray:
    """Per-sample K-weighted power summed over channels (channel weights 1.0 for L/R/C)."""
    y = x.astype(np.float64)
    for b, a in k_weighting(fs):
        y = signal.lfilter(b, a, y, axis=0)
    return np.sum(y * y, axis=1)


def _block_means(power: np.ndarray, fs: float, window_s: float, hop_s: float) -> np.ndarray:
    win, hop = int(round(window_s * fs)), int(round(hop_s * fs))
    if len(power) < win:
        return np.array([power.mean()]) if len(power) else np.array([])
    c = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(power) - win + 1, hop)
    return (c[starts + win] - c[starts]) / win


def _lufs(z: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(z)


def loudness(x: np.ndarray, fs: float) -> dict:
    """Integrated loudness (gated), LRA, and short-term (3 s) / momentary (400 ms) series."""
    power = _weighted_power(x, fs)
    momentary_z = _block_means(power, fs, 0.4, 0.1)
    momentary = _lufs(momentary_z)
    above_abs = momentary_z[momentary > ABS_GATE]
    if len(above_abs) == 0:
        integrated = -math.inf
    else:
        rel_gate = _lufs(np.array([above_abs.mean()]))[0] - 10
        gated = momentary_z[(momentary > ABS_GATE) & (momentary > rel_gate)]
        integrated = float(_lufs(np.array([gated.mean()]))[0]) if len(gated) else -math.inf
    short_z = _block_means(power, fs, 3.0, 0.1)
    short = _lufs(short_z)
    lra = 0.0
    st_abs = short_z[short > ABS_GATE]
    if len(st_abs):
        rel = _lufs(np.array([st_abs.mean()]))[0] - 20
        kept = short[(short > ABS_GATE) & (short > rel)]
        if len(kept) > 1:
            lra = float(np.percentile(kept, 95) - np.percentile(kept, 10))
    return {
        "integrated_lufs": integrated,
        "lra_lu": lra,
        "max_short_term_lufs": float(short.max()) if len(short) else -math.inf,
        "max_momentary_lufs": float(momentary.max()) if len(momentary) else -math.inf,
        "short_term_series": short,      # one value per 0.1 s (3 s windows)
        "momentary_series": momentary,   # one value per 0.1 s (0.4 s windows)
    }


def true_peak(x: np.ndarray, oversample: int = 4, chunk: int = 1 << 18) -> float:
    """Highest inter-sample peak (linear), via polyphase oversampling in overlapping chunks."""
    pad = 64
    peak = 0.0
    n = len(x)
    for ch in range(x.shape[1]):
        col = x[:, ch].astype(np.float64)
        for start in range(0, n, chunk):
            lo, hi = max(0, start - pad), min(n, start + chunk + pad)
            up = signal.resample_poly(col[lo:hi], oversample, 1, window=("kaiser", 8.0))
            core = up[(start - lo) * oversample:(min(n, start + chunk) - lo) * oversample]
            if len(core):
                peak = max(peak, float(np.max(np.abs(core))))
    return peak


def clipping_runs(x: np.ndarray, threshold: float = 0.9999, min_run: int = 3) -> int:
    """Runs of >= min_run consecutive samples at full scale (per channel, summed)."""
    runs = 0
    for ch in range(x.shape[1]):
        hot = np.abs(x[:, ch]) >= threshold
        if not hot.any():
            continue
        edges = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
        lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
        runs += int(np.sum(lengths >= min_run))
    return runs


def db(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else -math.inf


def levels(x: np.ndarray) -> dict:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0
    return {
        "sample_peak_dbfs": db(peak),
        "rms_dbfs": db(rms),
        "crest_factor_db": db(peak) - db(rms) if rms > 0 else 0.0,
        "dc_offset": [float(v) for v in np.mean(x, axis=0)],
        "clipped_runs": clipping_runs(x),
    }
