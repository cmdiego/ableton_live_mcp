"""Phase 5: devices, parameters, loading and automation against the fake set."""

import pytest

from ableton_mcp.live_client import LiveError, get_client
from ableton_mcp.tools import compose, devices, session
from ableton_mcp.tools.devices import automation_points


@pytest.fixture
def synth(client, song):
    devices.load_device("bass", "Drift")
    compose.write_chords("bass", "i iv", bars=2, slot=1)
    return song.tracks[1]


def test_load_builtin_and_refuse_second_instrument(synth):
    assert [d.name for d in synth.devices] == ["Drift"]
    with pytest.raises(LiveError, match="Delete the current instrument first"):
        devices.load_device("bass", "Wavetable")
    r = devices.load_device("bass", "EQ Eight", position=0)
    assert r["devices"] == ["EQ Eight", "Drift"]


def test_browser_fallback_ranks_presets_over_samples(client, song):
    found = devices.load_device("vox", "grand piano", search_only=True)["matches"]
    assert found[0]["name"] == "Grand Piano.adg" and found[0]["kind"] == "preset"
    assert all(m["kind"] != "sample" for m in found)
    r = devices.load_device("vox", "909 core kit")
    assert r["via"] == "browser" and "909 Core Kit" in r["devices"]
    assert song.view.selected_track is song.tracks[2]


def test_get_device_filter_understands_abbreviations(synth):
    d = devices.get_device("bass", "Drift", filter="filter", ranges=True)
    assert [row[1] for row in d["parameters"]] == ["Flt 1 Freq", "Flt 1 Res"]
    assert d["parameters"][0][3] == ["20 Hz", "20.00 kHz"]
    with pytest.raises(LiveError, match="Some names"):
        devices.get_device("bass", "Drift", filter="wobble")


def test_set_device_params_by_display_value(synth):
    r = devices.set_device_params("bass", "drift", {"Flt 1 Freq": "800 Hz", "Flt 1 Res": 35, "Osc 1 Wave": "Saw"})
    assert r["now"] == {"Flt 1 Freq": "800 Hz", "Flt 1 Res": "35.00 %", "Osc 1 Wave": "Saw"}
    assert devices.set_device_params("bass", 0, on=False)["now"] == {"Device On": "Off"}
    with pytest.raises(Exception, match="not one of"):
        devices.set_device_params("bass", "drift", {"Osc 1 Wave": "Triangle"})


def test_nested_device_path_errors_for_non_racks(synth):
    with pytest.raises(LiveError, match="is it a rack"):
        devices.device_path(get_client(), "bass", "Drift > Chain 1 > Simpler")


def test_delete_device_needs_confirm(synth):
    with pytest.raises(LiveError, match="confirm=true"):
        session.delete("device", track="bass", device="Drift")
    session.delete("device", track="bass", device="Drift", confirm=True)
    assert synth.devices == []


def test_automate_ramp_reads_back_in_display_units(synth):
    r = devices.automate("bass", "Flt 1 Freq", device="Drift", slot=1, shape="ramp",
                         start_value="200 Hz", end_value="8 kHz")
    assert r["check"][0][1] == "200 Hz" and r["check"][-1][1] in ("7.99 kHz", "8.00 kHz")
    assert r["breakpoints"] == 2 and r["range_beats"] == [0.0, 8.0]


def test_automate_mixer_volume_steps_and_send_points(synth):
    r = devices.automate("bass", "volume", slot=1, shape="steps", values=[0, -6], step_beats=4)
    assert [c[1] for c in r["check"]][:3] == ["0.0 dB", "0.0 dB", "-6.0 dB"]
    r = devices.automate("bass", "send B", slot=1, shape="points", points=[[0, -30], [8, -10]])
    # Live interpolates in the raw 0..1 range, so the midpoint isn't -20 dB; check the ends.
    assert r["parameter"] == "Send B" and r["check"][0][1] == "-30.0 dB" and r["check"][-1][1] == "-10.0 dB"


def test_automate_lfo_check_spans_one_cycle(synth):
    r = devices.automate("bass", "Flt 1 Res", device="Drift", slot=1, shape="lfo", wave="sine",
                         start_value=0, end_value=100, rate_beats=2)
    assert [c[1] for c in r["check"]] == ["0.00 %", "50.00 %", "100.00 %", "50.00 %", "0.00 %"]


def test_automate_clear_and_errors(synth):
    devices.automate("bass", "Flt 1 Res", device="Drift", slot=1, shape="ramp", start_value=0, end_value=50)
    assert devices.automate("bass", "Flt 1 Res", device="Drift", slot=1, shape="clear")["cleared"]
    assert not synth.clip_slots[1].clip.envelopes
    with pytest.raises(LiveError, match="needs start_value"):
        devices.automate("bass", "Flt 1 Res", device="Drift", slot=1, shape="ramp")
    with pytest.raises(LiveError, match="volume, pan or 'send A'"):
        devices.automate("bass", "cutoff", slot=1)


def test_automation_point_shapes():
    assert automation_points("ramp", 0, 4, 0.0, 1.0) == [(0, 0.0), (4, 1.0)]
    exp = automation_points("ramp", 0, 4, 0.0, 1.0, curve=2)
    assert exp[8][1] == pytest.approx(0.25)  # halfway in time, a quarter of the way in value
    steps = automation_points("steps", 0, 4, 0, 1, values=[0.1, 0.9], step_beats=1)
    assert [v for _, v in steps] == [0.1, 0.1, 0.9, 0.9, 0.1, 0.1, 0.9, 0.9]
    square = automation_points("lfo", 0, 2, 0.0, 1.0, wave="square", rate_beats=1)
    assert [v for _, v in square] == [1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="Unknown wave"):
        automation_points("lfo", 0, 1, 0, 1, wave="wobble")
