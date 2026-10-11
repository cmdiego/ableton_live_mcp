"""Compare a mix against a reference track (loudness-independent where it matters)."""

from __future__ import annotations

import numpy as np

EMPTY_BAND_DB = -50.0   # a band this far below the total is effectively empty
MONO_SIDE_DB = -25.0    # side this far below mid is effectively mono
MAX_TONAL_LINES = 4


def _smooth(values: list[float]) -> list[float]:
    """3-band moving average over 1/3-octave levels (about one octave wide)."""
    v = np.array(values, dtype=float)
    padded = np.concatenate([[v[0]], v, [v[-1]]])
    return list((padded[:-2] + padded[1:-1] + padded[2:]) / 3)


def compare(mix: dict, ref: dict, threshold_db: float = 1.5) -> dict:
    ml, rl = mix["loudness"], ref["loudness"]
    out: dict = {"mix": mix["file"], "reference": ref["file"]}
    if ml["integrated_lufs"] is not None and rl["integrated_lufs"] is not None:
        out["loudness"] = {
            "integrated_lufs": [ml["integrated_lufs"], rl["integrated_lufs"]],
            "gain_to_match_reference_db": round(rl["integrated_lufs"] - ml["integrated_lufs"], 1),
            "true_peak_dbtp": [ml["true_peak_dbtp"], rl["true_peak_dbtp"]],
            "lra_lu": [ml["lra_lu"], rl["lra_lu"]],
            "plr_db": [ml["plr_db"], rl["plr_db"]],
        }
    # Tonal balance is relative to each file's own total power, so it's already level-matched.
    # Differences are then centered on their median: boosting one band lowers every other band
    # relative to the total, and centering keeps the band that actually differs as the outlier.
    mg, rg = mix["tonal_balance"]["groups_db"], ref["tonal_balance"]["groups_db"]
    raw = {k: mg[k] - rg[k] for k in mg if k in rg and min(mg[k], rg[k]) > EMPTY_BAND_DB}
    center = float(np.median(list(raw.values()))) if raw else 0.0
    group_diff = {k: round(mg[k] - rg[k] - (center if k in raw else 0.0), 1) for k in mg if k in rg}
    out["tonal_difference_db"] = group_diff
    out["_group_levels"] = {k: (mg[k], rg[k]) for k in group_diff}
    out["tilt_db_per_octave"] = [mix["tonal_balance"]["tilt_db_per_octave"], ref["tonal_balance"]["tilt_db_per_octave"]]
    mb = dict((f, v) for f, v in mix["tonal_balance"]["third_octave_db"])
    rb = dict((f, v) for f, v in ref["tonal_balance"]["third_octave_db"])
    common = [f for f in mb if f in rb and f >= 25 and min(mb[f], rb[f]) > EMPTY_BAND_DB]
    third_center = float(np.median([mb[f] - rb[f] for f in common])) if common else 0.0
    diffs = _smooth([mb[f] - rb[f] - third_center for f in common]) if common else []
    out["third_octave_difference_db"] = [[f, round(d, 1)] for f, d in zip(common, diffs)]
    ms, rs = mix.get("stereo", {}), ref.get("stereo", {})
    if "correlation" in ms and "correlation" in rs:
        out["stereo"] = {
            "correlation": [ms["correlation"], rs["correlation"]],
            "side_to_mid_db": [ms["side_to_mid_db"], rs["side_to_mid_db"]],
            "low_end_correlation": [ms["correlation_by_band"].get("low (<150 Hz)"),
                                    rs["correlation_by_band"].get("low (<150 Hz)")],
        }
    out["findings"] = findings(out, threshold_db)
    out.pop("_group_levels")
    return out


def findings(c: dict, threshold_db: float) -> list[str]:
    lines = []
    if "loudness" in c:
        lu = c["loudness"]
        g = lu["gain_to_match_reference_db"]
        if abs(g) >= 1:
            lines.append(f"Mix is {abs(g):.1f} LU {'quieter' if g > 0 else 'louder'} than the reference")
        if lu["plr_db"][0] is not None and lu["plr_db"][1] is not None and abs(lu["plr_db"][0] - lu["plr_db"][1]) >= 2:
            more = "more dynamic" if lu["plr_db"][0] > lu["plr_db"][1] else "more compressed"
            lines.append(f"Mix is {more} (PLR {lu['plr_db'][0]} vs {lu['plr_db'][1]} dB)")
    levels = c.get("_group_levels", {})
    tonal = []
    for band, d in sorted(c["tonal_difference_db"].items(), key=lambda kv: -abs(kv[1])):
        if abs(d) < threshold_db:
            continue
        mix_lvl, ref_lvl = levels.get(band, (0.0, 0.0))
        if min(mix_lvl, ref_lvl) < EMPTY_BAND_DB:
            who = "The reference" if ref_lvl < mix_lvl else "The mix"
            tonal.append(f"{who} has almost no content in {band}")
        else:
            tonal.append(f"{'More' if d > 0 else 'Less'} {band} than the reference ({d:+.1f} dB vs the rest of the spectrum)")
    lines += tonal[:MAX_TONAL_LINES]
    peaks = [(f, d) for f, d in c["third_octave_difference_db"] if abs(d) >= threshold_db + 1.0]
    if peaks:
        worst = max(peaks, key=lambda fd: abs(fd[1]))
        lines.append(f"Biggest single-region difference around {worst[0]:g} Hz ({worst[1]:+.1f} dB)")
    t = c["tilt_db_per_octave"]
    if abs(t[0] - t[1]) >= 0.5:
        lines.append(f"Overall the mix is {'brighter' if t[0] > t[1] else 'darker'} (tilt {t[0]:+.2f} vs {t[1]:+.2f} dB/octave)")
    if "stereo" in c:
        sm = c["stereo"]["side_to_mid_db"]
        # Skip when both are effectively mono: then width differences are meaningless.
        if max(sm) >= MONO_SIDE_DB and abs(sm[0] - sm[1]) >= 3:
            lines.append(f"Mix is {'wider' if sm[0] > sm[1] else 'narrower'} (side/mid {sm[0]} vs {sm[1]} dB)")
    return lines or ["No differences above the threshold"]
