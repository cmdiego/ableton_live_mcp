"""Phase 10: mastering planner (simulation) and the plan -> apply -> check loop on the fake set."""

import json
import os
import time

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.audio import master as M
from ableton_mcp.audio import report as rep
from ableton_mcp.audio.loudness import db, loudness, true_peak
from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import mastering

FS = 48000


def drum_mix(seconds=12, seed=0, level=0.1):
    """Pink-ish bed plus regular transients: a dynamic, unmastered mix around -20 LUFS."""
    rng = np.random.default_rng(seed)
    n = FS * seconds
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / FS)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    bed = np.fft.irfft(spec, n)
    bed = level * bed / np.std(bed)
    t = np.arange(n) / FS
    hits = 0.6 * np.sin(2 * np.pi * 60 * (t % 0.5)) * np.exp(-(t % 0.5) / 0.05)
    x = bed + hits
    return np.stack([x, x * 0.98], 1)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_AGENT_DIR", str(tmp_path / "agent"))
    rep._cache.clear()
    return tmp_path / "agent"


def test_limiter_respects_true_peak_ceiling():
    x = drum_mix()
    y, gr = M.limiter(x, FS, 8.0, -1.0)
    assert db(true_peak(y)) <= -0.9 and gr.max() > 3


def test_glue_reduces_more_with_lower_threshold():
    x = drum_mix()
    _, gr_hi = M.glue(x, FS, -10)
    _, gr_lo = M.glue(x, FS, -30)
    assert gr_lo.mean() > gr_hi.mean()


def test_plan_hits_target_in_simulation(agent):
    x = drum_mix()
    path = agent / "exports" / "mix.wav"
    path.parent.mkdir(parents=True)
    sf.write(path, x, FS, subtype="PCM_24")
    mix = rep.analyze_file(path)
    for target, lufs in (("streaming", -14.0), ("loud", -9.0)):
        p = M.plan(x, FS, mix, lufs, -1.0)
        assert p["predicted"]["integrated_lufs"] == pytest.approx(lufs, abs=0.2)
        assert p["eq"][0]["type"] == "low_cut" and all(abs(m.get("gain_db", 0)) <= 3 for m in p["eq"])
        assert p["limiter"]["mode"] == "True Peak" and p["limiter"]["ceiling_db"] == -1.0
    assert p["glue"] is not None  # a dynamic mix gets glue


def test_bass_mono_for_out_of_phase_low_end():
    mix = {"stereo": {"correlation": 0.5, "correlation_by_band": {"low (<150 Hz)": 0.3}}, "levels": {"dc_offset": [0, 0]}}
    out = M.stereo_moves(mix, None)
    assert out["bass_mono"] and out["width"] == 100.0


def test_corrections():
    current = {"eq": [], "glue": None, "utility": {}, "limiter": {"input_gain_db": 2.0, "ceiling_db": -1.0}}
    fix = M.corrections({"loudness": {"integrated_lufs": -15.2, "true_peak_dbtp": -0.6}}, -14.0, -1.0, current)
    assert fix["settings"]["limiter"]["input_gain_db"] == 3.2 and fix["settings"]["limiter"]["ceiling_db"] == -1.5
    assert not fix["within_tolerance"] and current["limiter"]["input_gain_db"] == 2.0  # input not mutated
    ok = M.corrections({"loudness": {"integrated_lufs": -14.2, "true_peak_dbtp": -1.02}}, -14.0, -1.0, current)
    assert ok["within_tolerance"] and ok["changes"] == []


def test_targets_and_validation(agent):
    assert mastering._target("club", None, None, None)["true_peak"] == -0.5
    assert mastering._target("streaming", -12, None, None)["lufs"] == -12
    ref = {"loudness": {"integrated_lufs": -4.0}}
    assert mastering._target("reference", None, None, ref)["lufs"] == -6.0  # clamped
    with pytest.raises(LiveError, match="needs lufs"):
        mastering._target("custom", None, None, None)
    with pytest.raises(LiveError, match="between -24 and -5"):
        mastering._target("custom", -2, None, None)


def test_plan_apply_check_loop(agent, client, song):
    x = drum_mix()
    exports = agent / "exports"
    exports.mkdir(parents=True)
    sf.write(exports / "song.wav", x, FS, subtype="PCM_24")
    p = mastering.plan_master("song.wav", target="streaming")
    assert p["mode"] == "new" and p["predicted"]["integrated_lufs"] == pytest.approx(-14, abs=0.2)
    a = mastering.apply_master()
    names = [d.name for d in song.master_track.devices]
    assert names[-4:] == ["MCP Master EQ", "MCP Glue", "MCP Utility", "MCP Limiter"]
    assert a["applied"]["MCP Limiter"]["Mode"] == "True Peak" and a["applied"]["MCP Glue"]["Release"] == "A"
    # A later export that came out 1.5 LU quiet and 0.4 dB over the ceiling.
    s = p["settings"]
    y = M.apply_eq(x, FS, s["eq"])
    if s["glue"]:
        y, _ = M.glue(y, FS, s["glue"]["threshold_db"])
    y, _ = M.limiter(y, FS, s["limiter"]["input_gain_db"] - 1.5, -0.6)
    time.sleep(0.05)
    sf.write(exports / "song_master.wav", y, FS, subtype="PCM_24")
    os.utime(exports / "song_master.wav")
    chk = mastering.plan_master("song_master.wav", target="streaming")
    assert chk["mode"] == "check" and not chk["within_tolerance"]
    assert any("input gain" in c for c in chk["changes"]) and any("ceiling" in c for c in chk["changes"])
    mastering.apply_master()  # re-applying reuses the same four devices
    assert [d.name for d in song.master_track.devices].count("MCP Limiter") == 1
    with pytest.raises(LiveError, match="confirm=true"):
        mastering.apply_master(remove=True)
    assert len(mastering.apply_master(remove=True, confirm=True)["removed"]) == 4
    assert not (agent / "reports" / "master_state.json").exists()


def test_apply_without_plan(agent, client):
    with pytest.raises(LiveError, match="run plan_master first"):
        mastering.apply_master()
