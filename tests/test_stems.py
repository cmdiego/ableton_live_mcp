"""Phase 7: stem analysis on synthetic multitracks with known problems, and apply_eq."""

import numpy as np
import pytest
import soundfile as sf

from ableton_mcp.audio import stems as S
from ableton_mcp.live_client import LiveError
from ableton_mcp.tools import mixing

FS = 48000
SECONDS = 8


def tone(freq, db, seconds=SECONDS):
    t = np.arange(FS * seconds) / FS
    return 10 ** (db / 20) * np.sin(2 * np.pi * freq * t)


def hits(freq, db, every=0.5, decay=0.08, seconds=SECONDS, offset=0.0):
    t = np.arange(FS * seconds) / FS
    phase = (t - offset) % every
    env = np.where(t >= offset, np.exp(-phase / decay), 0.0)
    return 10 ** (db / 20) * np.sin(2 * np.pi * freq * phase) * env


def noise_band(lo, hi, db, seed, seconds=SECONDS):
    from scipy import signal
    x = np.random.default_rng(seed).standard_normal(FS * seconds)
    sos = signal.butter(4, [lo, hi], "bandpass", fs=FS, output="sos")
    y = signal.sosfilt(sos, x)
    return 10 ** (db / 20) * y / np.std(y)


def write_stems(folder, stems: dict):
    folder.mkdir(parents=True, exist_ok=True)
    for name, x in stems.items():
        data = x if x.ndim == 2 else np.stack([x, x], 1)
        sf.write(folder / f"{name}.wav", data.astype(np.float32), FS, subtype="PCM_24")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_AGENT_DIR", str(tmp_path / "agent"))
    return tmp_path / "agent"


def test_role_guessing():
    assert [S.guess_role(x) for x in ["01 Kick", "DT2 01 BD", "Bass Drum", "Sub Bass", "SD", "Lead Vox", "Pad Strings",
                                      "DT2 06 SH", "Weird Thing"]] == \
           ["kick", "kick", "kick", "bass", "snare", "vocal", "pad", "perc", "other"]
    assert S.stem_label(__import__("pathlib").Path("DT2 09 BASS 0002 [2026-10-08 200140].wav")) == "DT2 09 BASS"


def test_kick_bass_conflict_suggests_sidechain_and_carve(agent, client):
    write_stems(agent / "exports" / "song" / "stems", {
        "01 Kick": hits(60, -6),
        "02 Bass": tone(60, -10) + tone(120, -16),
        "03 Hats": hits(9000, -20, every=0.25, decay=0.02),
    })
    r = mixing.analyze_stems("song/stems")
    kinds = [k for s in r["suggestions"] for k in s if k not in ("stem", "why", "track")]
    assert "sidechain_from" in kinds
    carve = next(s for s in r["suggestions"] if "eq" in s and s["stem"] == "02 Bass")
    assert carve["eq"]["type"] == "bell" and carve["eq"]["gain_db"] < 0 and 40 < carve["eq"]["freq"] < 200
    assert r["conflicts"][0]["stems"] == ["01 Kick", "02 Bass"]
    assert (agent / r["report_file"]).exists()


def test_pad_with_rumble_gets_high_pass(agent):
    write_stems(agent / "exports" / "s2", {
        "Pad": noise_band(300, 3000, -14, 1) + noise_band(30, 80, -26, 2),
        "Kick": hits(55, -6),
    })
    r = mixing.analyze_stems("s2")
    pad = [s for s in r["suggestions"] if s["stem"] == "Pad"]
    hp = next(s for s in pad if s.get("eq", {}).get("type") == "low_cut")
    assert 100 <= hp["eq"]["freq"] <= 120  # above the 30-80 Hz rumble, below the 300 Hz body
    assert not any(s.get("eq", {}).get("type") == "bell" and s["eq"]["freq"] < 100 for s in pad)  # HPF covers it


def test_masking_requires_simultaneous_playing(agent):
    # Two leads in the same range but never at the same time: no conflict.
    a = np.concatenate([noise_band(800, 2000, -12, 3, 4), np.zeros(FS * 4)])
    b = np.concatenate([np.zeros(FS * 4), noise_band(800, 2000, -12, 4, 4)])
    write_stems(agent / "exports" / "s3", {"Lead A": a, "Lead B": b})
    assert mixing.analyze_stems("s3").get("conflicts", []) == []
    write_stems(agent / "exports" / "s4", {"Lead A": noise_band(800, 2000, -12, 3), "Lead B": noise_band(800, 2000, -12, 4)})
    assert mixing.analyze_stems("s4")["conflicts"][0]["stems"] == ["Lead A", "Lead B"]


def test_mislabeled_and_silent_stems(agent):
    write_stems(agent / "exports" / "s5", {
        "SD": tone(41, -12),               # named snare, but a sustained sub tone
        "CL": hits(1500, -14),
        "STAB": np.zeros(FS * SECONDS),
    })
    r = mixing.analyze_stems("s5")
    sd = next(s for s in r["stems"] if s["stem"] == "SD")
    assert sd["role"] == "bass" and "Named like a snare" in sd["role_note"]
    assert r["silent_stems"] == ["STAB"]
    assert not any(s["stem"] == "SD" and s.get("eq", {}).get("type") == "low_cut" for s in r.get("suggestions", []))
    r2 = mixing.analyze_stems("s5", roles={"SD": "snare"})
    assert next(s for s in r2["stems"] if s["stem"] == "SD")["role"] == "snare"


def test_stereo_low_end_gets_mono_hint(agent):
    left, right = tone(70, -10), tone(70, -10)
    right = np.roll(right, FS // 280)  # ~90 degrees at 70 Hz
    write_stems(agent / "exports" / "s6", {"Bass": np.stack([left, right], 1), "Hats": hits(9000, -20)})
    r = mixing.analyze_stems("s6")
    assert any(s.get("utility", {}).get("bass_mono_hz") for s in r["suggestions"] if s["stem"] == "Bass")


def test_errors(agent):
    with pytest.raises(LiveError, match="Folder not found"):
        mixing.analyze_stems("nope")
    write_stems(agent / "exports" / "one", {"Only": tone(440, -12)})
    with pytest.raises(LiveError, match="at least 2 stems"):
        mixing.analyze_stems("one")


def test_track_matching_expands_abbreviations():
    m = mixing.match_tracks(["DT2 01 BD", "DT2 04 CH", "DT2 05 OH", "Song Lead Vox", "Mystery"],
                            ["Kick", "Closed Hat", "Open Hat", "Vocal", "1-MIDI"])
    assert m == {"DT2 01 BD": "Kick", "DT2 04 CH": "Closed Hat", "DT2 05 OH": "Open Hat", "Song Lead Vox": "Vocal"}


def test_apply_eq_creates_flat_mcp_eq_and_fills_bands(client, song):
    r = mixing.apply_eq("vox", [{"type": "bell", "freq": 250, "gain_db": -3, "q": 1.2},
                                {"type": "low_cut", "freq": 90, "slope": 48}])
    assert r["created"] and [b["type"] for b in r["bands"]] == ["Bell", "High Pass 48dB"]
    assert r["bands"][0]["freq"] == "250 Hz" and r["bands"][0]["gain"] == "-3.00 dB" and r["bands"][0]["q"] == "1.20"
    eq = song.tracks[2].devices[0]
    assert eq.name == "MCP EQ"
    on = [p.str_for_value(p.value) for p in eq.parameters if p.name.endswith("Filter On A")]
    assert on == ["On", "On"] + ["Off"] * 6  # unused bands were switched off
    r2 = mixing.apply_eq("vox", [{"type": "high_shelf", "freq": 10000, "gain_db": 2}])
    assert not r2["created"] and r2["bands"][0]["band"] == 3
    with pytest.raises(LiveError, match="free bands"):
        mixing.apply_eq("vox", [{"type": "bell", "freq": 1000}] * 6)
    r3 = mixing.apply_eq("vox", [{"type": "notch", "freq": 3000, "q": 8}], replace=True)
    assert r3["bands"][0]["band"] == 1 and r3["bands"][0]["type"] == "Notch"
    with pytest.raises(LiveError, match="Unknown band type"):
        mixing.apply_eq("vox", [{"type": "wobble", "freq": 100}])
