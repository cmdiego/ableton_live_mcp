"""Stereo image: correlation (overall and per band), width, balance, mono compatibility."""

from __future__ import annotations

import math

import numpy as np
from scipy import signal

from .loudness import loudness

BANDS = {"low (<150 Hz)": (None, 150), "mid (150 Hz-5 kHz)": (150, 5000), "high (>5 kHz)": (5000, None)}


def _corr(left: np.ndarray, right: np.ndarray) -> float | None:
    denom = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
    return float(np.dot(left, right) / denom) if denom > 0 else None


def _band(x: np.ndarray, fs: float, lo: float | None, hi: float | None) -> np.ndarray:
    if lo is None:
        sos = signal.butter(4, hi, "lowpass", fs=fs, output="sos")
    elif hi is None:
        sos = signal.butter(4, lo, "highpass", fs=fs, output="sos")
    else:
        sos = signal.butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
    return signal.sosfilt(sos, x, axis=0)


def stereo_image(x: np.ndarray, fs: float, integrated_lufs: float) -> dict:
    if x.shape[1] < 2:
        return {"channels": 1}
    left, right = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    mid, side = (left + right) / 2, (left - right) / 2
    em, es = float(np.dot(mid, mid)), float(np.dot(side, side))
    el, er = float(np.dot(left, left)), float(np.dot(right, right))
    per_band = {}
    for name, (lo, hi) in BANDS.items():
        if hi is not None and hi >= fs / 2:
            continue
        b = _band(np.stack([left, right], axis=1), fs, lo, hi)
        c = _corr(b[:, 0], b[:, 1])
        per_band[name] = round(c, 2) if c is not None else None
    mono = np.stack([mid, mid], axis=1)
    mono_lufs = loudness(mono, fs)["integrated_lufs"]
    return {
        "correlation": round(_corr(left, right) or 0.0, 2),
        "correlation_by_band": per_band,
        "side_to_mid_db": round(10 * math.log10(es / em), 1) if em > 0 and es > 0 else -120.0,
        "balance_l_minus_r_db": round(10 * math.log10(el / er), 2) if el > 0 and er > 0 else 0.0,
        # -99 = the mix cancels (almost) completely in mono
        "mono_loudness_change_db": (round(mono_lufs - integrated_lufs, 2) if math.isfinite(mono_lufs) else -99.0)
        if math.isfinite(integrated_lufs) else None,
    }
