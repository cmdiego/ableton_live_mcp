"""End-to-end checks against a running Live. Use a scratch set.

    uv run python scripts/smoke.py            # probe API + phase 1 checks
    uv run python scripts/smoke.py --probe    # only report API capabilities

Creates a temporary track/scene and removes them again.
"""

from __future__ import annotations

import json
import sys
import time

from ableton_mcp.live_client import LiveClient, LiveError
from ableton_mcp.tools import (analysis, arrangement, compose, devices, live_audio, mastering, midi, mixer, mixing,
                               session)

PROBES = {
    "song": ["begin_undo_step", "end_undo_step", "root_note", "scale_name", "scale_mode",
             "create_midi_track", "duplicate_track", "capture_midi", "tempo_follower_enabled"],
    "song.tracks.0": ["insert_device", "delete_device", "arrangement_clips", "duplicate_clip_to_arrangement",
                      "create_midi_clip", "create_audio_clip"],
    "song.view": ["selected_track", "select_device"],
    "app.browser": ["load_item", "instruments", "audio_effects", "user_library", "plugins"],
}


def probe(client: LiveClient) -> dict:
    report = {}
    for path, names in PROBES.items():
        d = client.describe(path)
        available = set(d["properties"]) | set(d["methods"]) | {c.split("[")[0].split(" ")[0] for c in d["children"]}
        report[path] = {n: n in available for n in names}
    return report


def check(name: str, fn) -> bool:
    start = time.perf_counter()
    try:
        result = fn()
        print(f"  ok   {name} ({(time.perf_counter() - start) * 1000:.0f} ms)")
        return True if result is None else bool(result)
    except Exception as e:  # noqa: BLE001 - report everything
        print(f"  FAIL {name}: {e}")
        return False


def phase1(client: LiveClient) -> bool:
    results = []
    overview = session.get_session_overview()
    print(f"  set: {len(overview.get('tracks', []))} tracks, {len(overview.get('scenes', []))} scenes, "
          f"tempo {overview['song'].get('tempo')}, key {overview['song'].get('key')}")
    tempo = overview["song"]["tempo"]
    results.append(check("create scratch track", lambda: session.create("track", "midi", name="__mcp_smoke__")))
    results.append(check("rename + recolor", lambda: session.set_track("__mcp_smoke__", name="__mcp_smoke2__", color="teal")))
    results.append(check("track details", lambda: session.get_track_details("__mcp_smoke2__")))
    slot_path = None

    def make_clip():
        nonlocal slot_path
        from ableton_mcp import resolve
        slot_path = resolve.clip_slot_path(client, "__mcp_smoke2__", 0)
        client.invoke(slot_path, "create_clip", 4.0)
        client.call("add_notes", path=slot_path + ".clip", notes=[
            {"pitch": 60, "start_time": 0.0, "duration": 1.0, "velocity": 100}])
        return client.call("get_notes", path=slot_path + ".clip")[0]["pitch"] == 60

    results.append(check("create clip + notes roundtrip", make_clip))
    results.append(check("set clip loop", lambda: session.set_clip("__mcp_smoke2__", 0, loop_start=1.0, loop_end=3.0)))
    results.append(check("tempo set/restore", lambda: session.set_song(tempo=tempo + 1) and session.set_song(tempo=tempo)))
    results.append(check("delete scratch track", lambda: session.delete("track", track="__mcp_smoke2__", confirm=True)))
    start = time.perf_counter()
    for _ in range(10):
        client.call("ping")
    print(f"  latency: {(time.perf_counter() - start) * 100:.0f} ms per round trip")
    return all(results)


def phases_2_to_4(client: LiveClient) -> bool:
    name = "__mcp_smoke_p4__"
    session.create("track", "midi", name=name)
    results = [
        check("write_chords (Roman numerals, A minor)",
              lambda: compose.write_chords(name, "i VI III VII", key="A minor", slot=0)["notes"] == 12),
        check("analyze_clip_midi finds the same chords",
              lambda: [c["chord"].split("/")[0] for c in midi.analyze_clip_midi(name, slot=0)["chords"]]
              == ["Am", "F", "C", "G"]),
        check("write_bassline follows the chord clip",
              lambda: compose.write_bassline(name, chords_from_track=name, chords_from_slot=0, key="A minor",
                                             slot=1)["chords"] == ["Am", "F", "C", "G"]),
        check("write_arpeggio", lambda: compose.write_arpeggio(name, "pop", key="C", slot=2)["notes"] > 0),
        check("write_drums (GM fallback)", lambda: compose.write_drums(name, genre="trap", bars=1, slot=3)["notes"] > 0),
        check("transform_notes transpose + quantize",
              lambda: compose.transform_notes(name, [{"op": "transpose", "semitones": 12},
                                                     {"op": "quantize", "grid": "1/8"}], slot=0)["changed"] == 12),
        check("set_mixer -6 dB / 20L",
              lambda: mixer.set_mixer(name, volume_db=-6, pan="20L") == {"name": name, "volume": "-6.0 dB", "pan": "20L"}),
        check("delete scratch track", lambda: session.delete("track", track=name, confirm=True)),
    ]
    return all(results)


def phase5(client: LiveClient) -> bool:
    name = "__mcp_smoke_p5__"
    session.create("track", "midi", name=name)
    compose.write_chords(name, "i iv", key="A minor", bars=2, slot=0)
    results = [
        check("load_device Wavetable (built-in)", lambda: devices.load_device(name, "Wavetable")["via"] == "insert_device"),
        check("load_device preset search (grand piano)",
              lambda: devices.load_device(name, "grand piano", kind="preset", search_only=True)["matches"][0]["kind"] == "preset"),
        check("get_device filter=filter (abbreviated names)",
              lambda: len(devices.get_device(name, "Wavetable", filter="filter")["parameters"]) > 0),
        check("set_device_params 800 Hz",
              lambda: "800" in devices.set_device_params(name, "Wavetable", {"Flt 1 Freq": "800 Hz"})["now"]["Flt 1 Freq"]),
        check("automate ramp 200 Hz -> 8 kHz",
              lambda: devices.automate(name, "Flt 1 Freq", device="Wavetable", slot=0, shape="ramp",
                                       start_value="200 Hz", end_value="8 kHz")["check"][0][1] == "200 Hz"),
        check("automate volume lfo",
              lambda: devices.automate(name, "volume", slot=0, shape="lfo", start_value=-12, end_value=0)["breakpoints"] > 8),
        check("delete device", lambda: session.delete("device", track=name, device="Wavetable", confirm=True)),
        check("delete scratch track", lambda: session.delete("track", track=name, confirm=True)),
    ]
    return all(results)


def phase6() -> bool:
    import shutil

    import numpy as np
    import soundfile as sf

    from ableton_mcp import workspace

    folder = workspace.folder("exports") / "_smoke"
    folder.mkdir(exist_ok=True)
    t = np.arange(48000 * 5) / 48000
    tone = 0.1 * np.sin(2 * np.pi * 997 * t)  # -20 dBFS in both channels = -20 LUFS
    sf.write(folder / "tone.wav", np.stack([tone, tone], 1), 48000, subtype="PCM_24")
    try:
        return all([
            check("analyze_audio: 997 Hz at -20 dBFS reads -20 LUFS",
                  lambda: abs(analysis.analyze_audio("_smoke/tone.wav")["loudness"]["integrated_lufs"] + 20) < 0.05),
            check("list_audio finds it", lambda: any("_smoke/tone.wav" in f["path"] for f in analysis.list_audio()["files"])),
            check("analyze_audio(reference=) against itself", lambda: analysis.analyze_audio("_smoke/tone.wav", reference="_smoke/tone.wav")["findings"]
                  == ["No differences above the threshold"]),
        ])
    finally:
        shutil.rmtree(folder)
        for f in workspace.folder("reports").glob("tone*.json"):
            f.unlink()


def phase7() -> bool:
    import shutil

    import numpy as np
    import soundfile as sf

    from ableton_mcp import workspace

    folder = workspace.folder("exports") / "_smoke_stems"
    folder.mkdir(exist_ok=True)
    fs, n = 48000, 48000 * 6
    t = np.arange(n) / fs
    kick = 0.5 * np.sin(2 * np.pi * 60 * (t % 0.5)) * np.exp(-(t % 0.5) / 0.08)
    bass = 0.3 * np.sin(2 * np.pi * 60 * t)
    for name, x in (("Kick", kick), ("Bass", bass)):
        sf.write(folder / f"{name}.wav", np.stack([x, x], 1), fs, subtype="PCM_24")
    name = "__mcp_smoke_p7__"
    session.create("track", "audio", name=name)
    try:
        return all([
            check("analyze_stems finds kick/bass conflict + sidechain idea",
                  lambda: any("sidechain_from" in s for s in mixing.analyze_stems("_smoke_stems")["suggestions"])),
            check("apply_eq writes MCP EQ bands",
                  lambda: mixing.apply_eq(name, [{"type": "bell", "freq": 63, "gain_db": -3, "q": 2},
                                                 {"type": "low_cut", "freq": 30, "slope": 48}])["bands"][0]["freq"] == "63.0 Hz"),
            check("delete scratch track", lambda: session.delete("track", track=name, confirm=True)),
        ])
    finally:
        shutil.rmtree(folder)
        for f in workspace.folder("reports").glob("_smoke_stems*"):
            f.unlink()


def phase8(client: LiveClient) -> bool:
    song = arrangement._song(client)
    if song.get("cue_points") or any(t.get("arrangement_clips") for t in song["tracks"]):
        print("  skip: the arrangement isn't empty (Phase 8 smoke needs a blank arrangement)")
        return True
    names = ["__mcp_smoke_a__", "__mcp_smoke_b__"]
    for n in names:
        session.create("track", "midi", name=n)
    try:
        compose.write_chords(names[0], "i VI III VII", key="A minor", slot=0)
        compose.write_chords(names[0], "i VI III VII", key="A minor", slot=1, rhythm="offbeat")
        compose.write_drums(names[1], genre="house", bars=4, slot=1)
        seq = [{"scene": 0, "bars": 8, "name": "Intro"}, {"scene": 1, "bars": 16, "name": "Drop"},
               {"scene": 0, "bars": 8, "name": "Outro"}]
        return all([
            check("build_from_scenes", lambda: arrangement.edit_arrangement("build_from_scenes", sequence=seq)["ends_at_bar"] == 33),
            check("detect sections (locators ignored)",
                  lambda: [s["name"] for s in arrangement.get_arrangement(use_locators=False)["sections"]] == ["intro", "drop", "outro"]),
            check("insert_bars + duplicate_section + delete_bars", lambda: (
                arrangement.edit_arrangement("insert_bars", bar=9, bars=4),
                arrangement.edit_arrangement("duplicate_section", bar=13, end_bar=29),
                arrangement.edit_arrangement("delete_bars", bar=9, end_bar=13, confirm=True),
                [s["start_bar"] for s in arrangement.get_arrangement()["sections"]] == [1, 9, 41])[-1]),
        ])
    finally:
        cues = arrangement._song(client).get("cue_points") or []
        if cues:
            arrangement.cue_ops(client, [{"op": "delete", "time": c["time"]} for c in cues])
        for n in names:
            session.delete("track", track=n, confirm=True)


def phase9(client: LiveClient) -> bool:
    import asyncio
    import json

    from ableton_mcp.server import mcp

    name = "__mcp_smoke_p9__"
    session.create("track", "midi", name=name)
    tempo = client.get("song", "tempo")["tempo"]

    def run_batch() -> bool:
        res = asyncio.run(mcp.call_tool("batch", {"steps": [
            {"tool": "set_mixer", "args": {"track": name, "volume_db": -9, "pan": "30L"}},
            {"tool": "set_song", "args": {"tempo": tempo + 7}},
            {"tool": "write_chords", "args": {"track": name, "progression": "i iv", "slot": 0}},
        ]}))
        return json.loads(res.content[0].text)["summary"].startswith("3/3")

    def one_undo_reverts_all() -> bool:
        client.invoke("song", "undo")
        mix = mixer.get_mix_state()["tracks"][-1]
        return (mix.get("volume") == "0.0 dB" and mix.get("pan") == "C"
                and client.get("song", "tempo")["tempo"] == tempo
                and not client.get(f"song.tracks.{len(client.get('song', 'tracks')['tracks']) - 1}.clip_slots.0",
                                   "has_clip")["has_clip"])

    try:
        return all([
            check("batch: mixer + tempo + chords via the MCP call path", run_batch),
            check("one undo reverts the whole batch", one_undo_reverts_all),
            check("resources: session snapshot", lambda: "song" in json.loads(
                asyncio.run(mcp.read_resource("ableton://session"))[0].content)),
        ])
    finally:
        client.set("song", tempo=tempo)
        session.delete("track", track=name, confirm=True)


def phase10(client: LiveClient) -> bool:
    import shutil

    import numpy as np
    import soundfile as sf

    from ableton_mcp import workspace

    if any(d["name"].startswith("MCP ") for d in client.query("song.master_track.devices", {"props": ["name"]})):
        print("  skip: the master already has an MCP chain")
        return True
    folder = workspace.folder("exports") / "_smoke_master"
    folder.mkdir(exist_ok=True)
    rng = np.random.default_rng(0)
    t = np.arange(48000 * 10) / 48000
    x = 0.05 * rng.standard_normal(len(t)) + 0.5 * np.sin(2 * np.pi * 60 * (t % 0.5)) * np.exp(-(t % 0.5) / 0.05)
    sf.write(folder / "mix.wav", np.stack([x, x], 1), 48000, subtype="PCM_24")
    try:
        return all([
            check("plan_master: simulation hits -14 LUFS",
                  lambda: abs(mastering.plan_master("_smoke_master/mix.wav")["predicted"]["integrated_lufs"] + 14) <= 0.2),
            check("apply_master builds the chain (True Peak limiter at -1 dB)", lambda: (
                lambda a: a["applied"]["MCP Limiter"]["Mode"] == "True Peak" and a["applied"]["MCP Limiter"]["Ceiling"] == "-1.0 dB"
            )(mastering.apply_master())),
            check("remove the chain", lambda: len(mastering.apply_master(remove=True, confirm=True)["removed"]) == 4),
        ])
    finally:
        shutil.rmtree(folder)
        for name in ("mix.analysis.json", "master_plan.json"):
            (workspace.folder("reports") / name).unlink(missing_ok=True)


def phase11(client: LiveClient) -> bool:
    import threading

    import numpy as np
    import soundfile as sf

    from ableton_mcp import workspace

    tone = workspace.folder("captures") / "_smoke_tone.wav"
    t = np.arange(48000 * 12) / 48000
    x = 0.1 * np.sin(2 * np.pi * 997 * t)  # -20 dBFS both channels = -20 LUFS
    sf.write(tone, np.stack([x, x], 1), 48000, subtype="PCM_24")
    track = session.create("track", "audio", name="__mcp_smoke_p11__")["created"]
    made = []
    try:
        client.invoke(track, "create_audio_clip", str(tone), 0.0)
        client.set(f"{track}.arrangement_clips.0", warping=False)

        def capture_ok() -> bool:
            r = live_audio.analyzer("capture", seconds=3, start_bar=1, name="smoke")
            made.append(workspace.agent_dir() / r["capture"])
            return abs(r["loudness"]["integrated_lufs"] + 20) < 0.1

        def meter_ok() -> bool:
            client.set("song", start_time=0.0)
            client.invoke("song", "start_playing")
            try:
                m = live_audio.analyzer("meter", seconds=1.5)
            finally:
                client.invoke("song", "stop_playing")
            return m["readings"] >= 8 and abs(m["peak_dbfs"][0] + 20) < 0.3

        return all([
            check("analyzer install (idempotent) + status", lambda: live_audio.analyzer("install")["responding"]),
            check("capture Live's master: -20 dBFS tone reads -20 LUFS", capture_ok),
            check("live meter: peak -20 dBFS while playing", meter_ok),
        ])
    finally:
        session.delete("track", track="__mcp_smoke_p11__", confirm=True)
        for f in made + [tone, tone.with_suffix(".wav.asd")]:
            f.unlink(missing_ok=True)
            (workspace.folder("reports") / f"{f.stem}.analysis.json").unlink(missing_ok=True)


def main() -> int:
    client = LiveClient()
    try:
        print("ping:", client.call("ping"))
    except LiveError as e:
        print(e)
        return 1
    import ableton_mcp.live_client as lc
    lc._client = client
    print("API probe:")
    print(json.dumps(probe(client), indent=2))
    if "--probe" in sys.argv:
        return 0
    print("Phase 1:")
    ok = phase1(client)
    print("Phases 2-4:")
    ok = phases_2_to_4(client) and ok
    print("Phase 5:")
    ok = phase5(client) and ok
    print("Phase 6:")
    ok = phase6() and ok
    print("Phase 7:")
    ok = phase7() and ok
    print("Phase 8:")
    ok = phase8(client) and ok
    print("Phase 9:")
    ok = phase9(client) and ok
    print("Phase 10:")
    ok = phase10(client) and ok
    print("Phase 11:")
    ok = phase11(client) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
