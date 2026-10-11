"""Tonal balance: 1/3-octave band levels, band groups, tilt and centroid (all relative to the
signal's own total power, so they don't depend on loudness)."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

THIRD_OCTAVES = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000,
                 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]

GROUPS = {
    "sub (20-60 Hz)": (20, 60),
    "low (60-250 Hz)": (60, 250),
    "low-mid (250-500 Hz)": (250, 500),
    "mid (500 Hz-2 kHz)": (500, 2000),
    "high-mid (2-6 kHz)": (2000, 6000),
    "presence (6-12 kHz)": (6000, 12000),
    "air (12-20 kHz)": (12000, 20000),
}


def psd(x: np.ndarray, fs: float, nperseg: int = 8192) -> tuple[np.ndarray, np.ndarray]:
    """Welch power spectral density of the mid (L+R)/2 signal."""
    mid = x.mean(axis=1) if x.ndim == 2 else x
    nperseg = min(nperseg, max(256, len(mid)))
    return signal.welch(mid.astype(np.float64), fs, nperseg=nperseg, scaling="density")


def _band_power(freqs: np.ndarray, power: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    return float(power[mask].sum())


def tonal_balance(x: np.ndarray, fs: float) -> dict:
    freqs, p = psd(x, fs)
    total = float(p[(freqs >= 20) & (freqs <= min(20000, fs / 2))].sum()) or 1e-30

    def rel(power: float) -> float:
        return round(10 * math.log10(power / total), 1) if power > 0 else -120.0

    bands = []
    for fc in THIRD_OCTAVES:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        if hi > fs / 2:
            break
        bands.append([fc, rel(_band_power(freqs, p, lo, hi))])
    groups = {name: rel(_band_power(freqs, p, lo, min(hi, fs / 2))) for name, (lo, hi) in GROUPS.items()}
    # Tilt: slope of band level vs octave between 40 Hz and 16 kHz (pink noise ~ 0 dB/oct here).
    fit = [(math.log2(fc), lvl) for fc, lvl in bands if 40 <= fc <= 16000 and lvl > -100]
    tilt = float(np.polyfit([f for f, _ in fit], [v for _, v in fit], 1)[0]) if len(fit) > 3 else 0.0
    audible = (freqs >= 20) & (freqs <= 20000)
    centroid = float((freqs[audible] * p[audible]).sum() / p[audible].sum()) if p[audible].sum() > 0 else 0.0
    return {"third_octave_db": bands, "groups_db": groups, "tilt_db_per_octave": round(tilt, 2),
            "centroid_hz": round(centroid)}
