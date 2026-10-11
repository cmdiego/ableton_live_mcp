"""Phase 6: audio analysis on synthetic signals with known answers."""

import math

import numpy as np
import pyloudnorm as pyln
import pytest
import soundfile as sf

from ableton_mcp.audio import report as rep
from ableton_mcp.audio.compare import compare
from ableton_mcp.audio.loudness import db, loudness, true_peak
from ableton_mcp.audio.spectrum import tonal_balance
from ableton_mcp.audio.stereo import stereo_image
from ableton_mcp.tools import analysis

FS = 48000


def sine(freq, dbfs, seconds=5.0, fs=FS, phase=0.0):
    t = np.arange(int(fs * seconds)) / fs
    return 10 ** (dbfs / 20) * np.sin(2 * np.pi * freq * t + phase)


def stereo(left, right=None):
    return np.stack([left, left if right is None else right], axis=1).astype(np.float32)


def pink(seconds=10.0, fs=FS, seed=0):
    rng = np.random.default_rng(seed)
    n = int(fs * seconds)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    return 0.1 * x / np.std(x)


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_AGENT_DIR", str(tmp_path / "agent"))
    rep._cache.clear()
    return tmp_path / "agent"


def test_reference_sine_is_minus_20_lufs():
    assert loudness(stereo(sine(997, -20)), FS)["integrated_lufs"] == pytest.approx(-20.0, abs=0.02)


def test_mono_sine_in_one_channel_is_3db_lower():
    x = stereo(sine(997, -20), np.zeros(FS * 5))
    assert loudness(x, FS)["integrated_lufs"] == pytest.approx(-23.01, abs=0.05)


@pytest.mark.parametrize("fs", [44100, 48000, 96000])
def test_matches_pyloudnorm_on_noise(fs):
    x = stereo(pink(6, fs), pink(6, fs, seed=1))
    ours = loudness(x, fs)["integrated_lufs"]
    assert ours == pytest.approx(pyln.Meter(fs).integrated_loudness(x.astype(np.float64)), abs=0.1)


def test_gating_ignores_silence():
    x = stereo(np.concatenate([sine(997, -20, 5), np.zeros(FS * 20)]))
    ours = loudness(x, FS)["integrated_lufs"]
    # Blocks straddling the tone's end are partly silent but above the gate, so a bit under -20.
    assert -20.2 < ours < -20.0
    assert ours == pytest.approx(pyln.Meter(FS).integrated_loudness(x.astype(np.float64)), abs=0.1)


def test_lra_of_steady_vs_two_level_signal():
    assert loudness(stereo(pink(20)), FS)["lra_lu"] < 1.5
    two = np.concatenate([pink(15), pink(15, seed=2) * 10 ** (-12 / 20)])
    assert 10 < loudness(stereo(two), FS)["lra_lu"] < 14  # ~12 dB step plus noise spread


def test_true_peak_finds_intersample_peak():
    tone = sine(FS / 4, 0.0, 1.0, phase=math.pi / 4)
    fade = np.ones_like(tone)
    ramp = np.hanning(960)  # 10 ms fades: a hard start/stop really does overshoot when reconstructed
    fade[:480], fade[-480:] = ramp[:480], ramp[480:]
    x = stereo(tone * fade)
    assert db(np.abs(x).max()) == pytest.approx(-3.01, abs=0.01)
    assert db(true_peak(x)) == pytest.approx(0.0, abs=0.05)


def test_tonal_balance_pink_is_flat_and_sine_lands_in_its_band():
    assert abs(tonal_balance(stereo(pink(10)), FS)["tilt_db_per_octave"]) < 0.3
    groups = tonal_balance(stereo(sine(100, -12)), FS)["groups_db"]
    assert groups["low (60-250 Hz)"] > -0.5 and groups["air (12-20 kHz)"] < -60


def test_stereo_cases():
    mono = stereo(pink(5))
    assert stereo_image(mono, FS, -20)["correlation"] == 1.0
    inverted = stereo(pink(5), -pink(5))
    s = stereo_image(inverted, FS, -20)
    assert s["correlation"] == -1.0 and s["mono_loudness_change_db"] == -99.0
    wide = stereo(pink(5), pink(5, seed=3))
    w = stereo_image(wide, FS, loudness(wide, FS)["integrated_lufs"])
    assert abs(w["correlation"]) < 0.1 and abs(w["side_to_mid_db"]) < 0.5


def write(agent_dir, sub, name, x, fs=FS):
    p = agent_dir / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(p, x, fs, subtype="PCM_24")
    return p


def test_analyze_latest_export_and_issues(agent):
    hot = stereo(np.clip(sine(60, 3, 6), -1, 1), -np.clip(sine(60, 3, 6), -1, 1) * 0.9)  # clipped, out of phase bass
    write(agent, "exports", "song/mix_v1.wav", hot)
    r = analysis.analyze_audio()  # latest
    assert r["file"].endswith("mix_v1.wav") and r["report_file"].startswith("reports/")
    texts = " ".join(i["issue"] for i in r["issues"])
    assert "clipped" in texts and "True peak" in texts and "mono-safe" in texts
    assert (agent / r["report_file"]).exists()
    assert "third_octave_db" not in r["tonal_balance"]  # summary omits bulky bands
    assert "third_octave_db" in analysis.analyze_audio("song/mix_v1.wav", detail="full")["tonal_balance"]


def test_streaming_gain(agent):
    write(agent, "exports", "quiet.wav", stereo(sine(997, -20)))
    r = analysis.analyze_audio("quiet.wav")
    assert r["streaming_playback_gain_db"]["Spotify / YouTube / Tidal"] == pytest.approx(6.0, abs=0.1)


def test_list_and_missing_file(agent):
    write(agent, "references", "ref.wav", stereo(pink(3)))
    assert [f["path"] for f in analysis.list_audio("references")["files"]] == ["references/ref.wav"]
    with pytest.raises(Exception, match="not found"):
        analysis.analyze_audio("nope.wav")
    with pytest.raises(Exception, match="Export a mix"):
        analysis.analyze_audio()


def test_compare_finds_tonal_and_loudness_differences(agent):
    ref = stereo(pink(8), pink(8, seed=5))
    bassy = ref + stereo(sine(120, -14, 8))
    write(agent, "references", "ref.wav", ref)
    write(agent, "exports", "bassy.wav", bassy * 0.5)
    c = analysis.analyze_audio("bassy.wav", reference="ref.wav")
    assert c["tonal_difference_db"]["low (60-250 Hz)"] > 3
    assert any("More low (60-250 Hz)" in f for f in c["findings"])
    assert any("narrower" in f for f in c["findings"])  # the added sine is mono
    lu = c["loudness"]
    assert lu["gain_to_match_reference_db"] == pytest.approx(lu["integrated_lufs"][1] - lu["integrated_lufs"][0], abs=0.1)


def test_compare_identical_is_quiet(agent):
    x = stereo(pink(5), pink(5, seed=9))
    mix = rep.analyze_file(write(agent, "exports", "a.wav", x))
    assert compare(mix, mix)["findings"] == ["No differences above the threshold"]


def test_compare_handles_empty_bands_and_mono_files(agent):
    from scipy import signal
    full = stereo(pink(6))
    sos = signal.butter(8, 3000, "lowpass", fs=FS, output="sos")
    dull = signal.sosfilt(sos, full, axis=0).astype(np.float32)  # nothing above ~6 kHz
    write(agent, "exports", "full.wav", full)
    write(agent, "references", "dull.wav", dull)
    f = analysis.analyze_audio("full.wav", reference="dull.wav")["findings"]
    assert any("reference has almost no content in air" in x for x in f)
    assert not any("wider" in x or "narrower" in x for x in f)  # both are dual-mono
    assert all("+4" not in x and "+5" not in x for x in f if "single-region" in x)
