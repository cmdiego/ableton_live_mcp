"""Phase 8: read the arrangement as sections and energy; edit it (locators, bars, sections, scenes)."""

from __future__ import annotations

from typing import Literal

from .. import resolve
from ..app import tool
from ..audio import report as audio_report
from ..audio.stems import guess_role
from ..live_client import LiveClient, LiveError, get_client
from ..theory import arrangement as arr
from ..util import compact, require_confirm

CLIP_PROPS = ["name", "start_time", "end_time", "is_midi_clip", "muted", "looping", "loop_start", "loop_end",
              "start_marker", "end_marker", "length"]


def _song(client: LiveClient) -> dict:
    return client.query("song", {
        "props": ["tempo", "signature_numerator", "signature_denominator"],
        "children": {
            "tracks": {"props": ["name", "mute", "has_midi_input", "is_foldable"], "children": {
                "arrangement_clips": {"props": CLIP_PROPS},
                "devices": {"props": ["class_name"]},
            }},
            "cue_points": {"props": ["name", "time"]},
        },
    })


def note_times(notes: list[float], c: dict) -> list[float]:
    """Song-beat positions of a clip's note starts, following its loop."""
    start, end = c["start_time"], c["end_time"]
    if not c.get("looping"):
        return [start + n - c["start_marker"] for n in notes
                if c["start_marker"] <= n < c["end_marker"] and start + n - c["start_marker"] < end]
    out = [start + n - c["start_marker"] for n in notes if c["start_marker"] <= n < c["loop_end"]]
    loop_len = c["loop_end"] - c["loop_start"]
    pos = start + (c["loop_end"] - c["start_marker"])
    while loop_len > 0 and pos < end - 1e-9:
        out += [pos + n - c["loop_start"] for n in notes if c["loop_start"] <= n < c["loop_end"] and pos + n - c["loop_start"] < end]
        pos += loop_len
    return out


def _activity(client: LiveClient, song: dict) -> tuple[list[arr.TrackActivity], list[int], float]:
    midi_paths = [(t["_i"], c["_i"]) for t in song["tracks"] for c in t.get("arrangement_clips") or []
                  if c["is_midi_clip"] and not c["muted"]]
    results = client.batch([{"cmd": "get_notes", "params": {"path": f"song.tracks.{ti}.arrangement_clips.{ci}"}}
                            for ti, ci in midi_paths]) if midi_paths else []
    notes = {key: [n["start_time"] for n in r] for key, r in zip(midi_paths, results)}
    pitches = {key: [n["pitch"] for n in r] for key, r in zip(midi_paths, results)}
    tracks, index, end = [], [], 0.0
    for t in song["tracks"]:
        clips = [c for c in t.get("arrangement_clips") or [] if not c["muted"]]
        if t.get("is_foldable") or not clips or t.get("mute"):
            continue
        role = guess_role(t["name"])
        if role == "other":
            classes = {d["class_name"] for d in t.get("devices") or []}
            all_pitches = [p for c in clips for p in pitches.get((t["_i"], c["_i"]), [])]
            if "DrumGroupDevice" in classes:
                role = "drums"
            elif all_pitches and sum(all_pitches) / len(all_pitches) < 50:
                role = "bass"
        times = [nt for c in clips for nt in note_times(notes.get((t["_i"], c["_i"]), []), c)]
        tracks.append(arr.TrackActivity(t["name"], role, [(c["start_time"], c["end_time"]) for c in clips], times))
        index.append(t["_i"])
        end = max(end, max(c["end_time"] for c in clips))
    return tracks, index, end


def _mix_curve(path: str, bars: int, bpb: float, tempo: float) -> list[float | None]:
    report = audio_report.analyze_file(audio_report.resolve_path(path))
    sec_per_bar = bpb * 60 / tempo
    per_bar: list[list[float]] = [[] for _ in range(bars)]
    for t, lufs in report["short_term_lufs_curve"]:
        b = int(t // sec_per_bar)
        if 0 <= b < bars:
            per_bar[b].append(lufs)
    return [round(sum(v) / len(v), 1) if v else None for v in per_bar]


def _analyze(client: LiveClient, use_locators: bool = True, mix: str | None = None):
    song = _song(client)
    bpb = song["signature_numerator"] * 4.0 / song["signature_denominator"]
    tracks, index, end = _activity(client, song)
    if not tracks:
        raise LiveError("The arrangement is empty. Record or drag clips into Arrangement View, or build one from "
                        "session scenes with edit_arrangement(action='build_from_scenes').")
    bars = max(1, int(-(-end // bpb)))
    loud = _mix_curve(mix, bars, bpb, song["tempo"]) if mix else None
    grid = arr.build_grid(tracks, end, bpb, loud)
    cues = sorted(song.get("cue_points") or [], key=lambda c: c["time"])
    bounds, names = None, None
    if use_locators and cues:
        marks = [(int(round(c["time"] / bpb)), c["name"] or f"locator {k + 1}") for k, c in enumerate(cues)]
        marks = [(b, n) for b, n in marks if 0 <= b < bars]
        if marks:
            if marks[0][0] != 0:
                marks.insert(0, (0, "(start)"))
            bounds, names = [b for b, _ in marks], [n for _, n in marks]
    secs = arr.sections(grid, tracks, bounds, names)
    return song, bpb, tracks, index, grid, secs


@tool(read_only=True)
def get_arrangement(use_locators: bool = True, mix: str | None = None, curve: bool = True) -> dict:
    """Read the Arrangement View as a song: sections (from locators, or detected from what plays
    when) labelled intro/verse/build/drop/breakdown/outro, with energy 0..1, trend, which parts
    play, what enters and leaves; per-track activity; a per-bar energy curve; and observations
    about structure (unchanging stretches, flat energy, unprepared drops, abrupt endings, parts
    that never rest, length). Energy combines active parts (kick, drums and bass weigh more)
    and note density. mix: an exported mix of this arrangement ('latest' or a file in agent/)
    adds its loudness per bar; it must start at bar 1. use_locators=false ignores locators and
    shows detected sections."""
    client = get_client()
    song, bpb, tracks, _, grid, secs = _analyze(client, use_locators, mix)
    out = {
        "song": {"tempo": round(song["tempo"], 2), "time_signature": f"{song['signature_numerator']}/{song['signature_denominator']}",
                 "bars": grid.bars, "sections_from": "locators" if any("detected_as" in s for s in secs) else "detection"},
        "sections": secs,
        "tracks": [{"name": t.name, "role": t.role,
                    "bars": arr.bar_ranges([b for b in range(grid.bars) if i in grid.active[b]])}
                   for i, t in enumerate(tracks)],
        "observations": arr.observations(grid, tracks, secs, song["tempo"]),
    }
    if curve:
        out["energy_by_bar"] = grid.energy
    return compact(out)


# -- editing -------------------------------------------------------------------------------


def cue_ops(client: LiveClient, ops: list[dict]) -> dict:
    """Add/rename/delete locators. Live only offers set_or_delete_cue() at the playhead, and a new
    playhead position takes effect a tick later (in the same tick the toggle hits the *old*
    position and can delete the wrong locator). So each step is a separate, verified call."""
    restore = client.get("song", "current_song_time")["current_song_time"]

    def at(t: float) -> list[dict]:
        return [c for c in client.query("song.cue_points", {"props": ["name", "time"]}) if abs(c["time"] - t) < 1e-6]

    def move_to(t: float) -> None:
        client.set("song", current_song_time=float(t))
        now = client.get("song", "current_song_time")["current_song_time"]
        if abs(now - t) > 1e-6:
            raise LiveError(f"Couldn't move the playhead to beat {t:g} (it's at {now:g})")

    try:
        for op in ops:
            t = float(op["time"])
            existing = at(t)
            if op["op"] == "add":
                if not existing:
                    move_to(t)
                    if client.invoke("song", "is_cue_point_selected"):
                        raise LiveError(f"Live reports a locator at beat {t:g} that the API doesn't list")
                    client.invoke("song", "set_or_delete_cue")
                    existing = at(t)
                    if not existing:
                        raise LiveError(f"Live didn't create a locator at beat {t:g}")
                if op.get("name") is not None:
                    client.set(f"song.cue_points.{existing[0]['_i']}", name=op["name"])
            elif op["op"] == "rename":
                if not existing:
                    raise LiveError(f"No locator at beat {t:g}")
                client.set(f"song.cue_points.{existing[0]['_i']}", name=op["name"])
            elif op["op"] == "delete":
                if not existing:
                    continue
                move_to(t)
                if not client.invoke("song", "is_cue_point_selected"):
                    raise LiveError(f"The locator at beat {t:g} isn't selected at the playhead; not toggling")
                client.invoke("song", "set_or_delete_cue")
                if at(t):
                    raise LiveError(f"Live didn't delete the locator at beat {t:g}")
            else:
                raise LiveError(f"Unknown locator op {op['op']!r}")
    finally:
        client.set("song", current_song_time=restore)
    cues = client.query("song.cue_points", {"props": ["name", "time"]})
    return {"applied": len(ops), "locators": [[c["time"], c["name"]] for c in sorted(cues, key=lambda c: c["time"])]}


def _beats(bar: float, bpb: float) -> float:
    if bar < 1:
        raise LiveError("Bars are 1-based (bar 1 is the start of the song)")
    return (bar - 1) * bpb


def _shift_ops(song: dict, at: float, shift: float) -> list[dict]:
    """Move every clip starting at/after `at` by `shift` beats (order avoids collisions)."""
    ops, straddle = [], []
    for t in song["tracks"]:
        for c in t.get("arrangement_clips") or []:
            if c["start_time"] < at - 1e-6 < c["end_time"] - 2e-6:
                straddle.append(f"{t['name']} ({c['start_time']:g}-{c['end_time']:g} beats)")
            elif c["start_time"] >= at - 1e-6:
                ops.append({"track": f"song.tracks.{t['_i']}", "op": "move", "start": c["start_time"],
                            "to": c["start_time"] + shift})
    if straddle:
        raise LiveError("These clips cross the edit point and can't be split through Live's API: "
                        + ", ".join(straddle[:6]) + ". Pick a point between clips, or split them in Live first.")
    ops.sort(key=lambda o: o["start"], reverse=shift > 0)
    return ops


def _shift_cues(song: dict, at: float, shift: float, drop_until: float | None = None) -> list[dict]:
    cues = [c for c in song.get("cue_points") or [] if c["time"] >= at - 1e-6]
    ops = []
    for c in cues:
        ops.append({"op": "delete", "time": c["time"]})
    for c in cues:
        if drop_until is not None and c["time"] < drop_until - 1e-6:
            continue  # inside a deleted range
        ops.append({"op": "add", "time": c["time"] + shift, "name": c["name"]})
    return ops


@tool(destructive=True)
def edit_arrangement(
    action: Literal["add_locator", "rename_locator", "delete_locator", "locators_from_sections",
                    "insert_bars", "delete_bars", "duplicate_section", "build_from_scenes"],
    bar: float | None = None, end_bar: float | None = None, to_bar: float | None = None, bars: float | None = None,
    name: str | None = None, locator: str | int | None = None,
    sequence: list[dict[str, str | float]] | None = None, replace: bool = False, confirm: bool = False,
) -> dict:
    """Edit the arrangement. Bars are 1-based; a section [bar, end_bar) excludes end_bar.

    - add_locator: bar, name | rename_locator: locator (index/name), name | delete_locator: locator
    - locators_from_sections: a named locator at each detected section (replace=true + confirm clears old ones)
    - insert_bars: bar, bars (empty space; later clips and locators move right)
    - delete_bars: bar, end_bar, confirm=true (removes clips inside, closes the gap)
    - duplicate_section: bar, end_bar, to_bar (default: right after); later material moves right
    - build_from_scenes: sequence=[{"scene": "Intro", "bars": 8}, {"scene": 2, "bars": 16, "name": "Drop"}],
      bar (default 1): copies scenes' session clips into the arrangement, repeated to fill, with locators.
      Needs empty space there (or confirm=true to overwrite).
    Clips crossing an edit point can't be split via the API, so such edits are refused. Track
    automation lanes don't move (clip envelopes do). One undo step per edit (+1 for locators)."""
    client = get_client()
    song = _song(client)
    bpb = song["signature_numerator"] * 4.0 / song["signature_denominator"]
    cues = sorted(song.get("cue_points") or [], key=lambda c: c["time"])

    def cue_time(ref) -> float:
        names = [c["name"] for c in cues]
        if not cues:
            raise LiveError("There are no locators")
        return cues[resolve.match_name(names, ref, "locator")]["time"]

    if action == "add_locator":
        if bar is None:
            raise LiveError("add_locator needs bar")
        return cue_ops(client, [{"op": "add", "time": _beats(bar, bpb), "name": name or ""}])
    if action == "rename_locator":
        return cue_ops(client, [{"op": "rename", "time": cue_time(locator), "name": name or ""}])
    if action == "delete_locator":
        return cue_ops(client, [{"op": "delete", "time": cue_time(locator)}])
    if action == "locators_from_sections":
        _, _, _, _, grid, secs = _analyze(client, use_locators=False)
        ops = []
        if replace:
            require_confirm(confirm, f"delete all {len(cues)} existing locators")
            ops += [{"op": "delete", "time": c["time"]} for c in cues]
        taken = set() if replace else {round(c["time"], 3) for c in cues}
        ops += [{"op": "add", "time": (s["start_bar"] - 1) * bpb, "name": s["name"]} for s in secs
                if round((s["start_bar"] - 1) * bpb, 3) not in taken]
        result = cue_ops(client, ops)
        return {**result, "sections": [f"{s['start_bar']}: {s['name']}" for s in secs]}

    if action == "insert_bars":
        if bar is None or not bars:
            raise LiveError("insert_bars needs bar and bars")
        at, shift = _beats(bar, bpb), bars * bpb
        ops = _shift_ops(song, at, shift)
        res = client.call("arrangement_ops", ops=ops, undo_name="Insert bars") if ops else {"applied": 0}
        if any(c["time"] >= at for c in cues):
            cue_ops(client, _shift_cues(song, at, shift))
        return {"inserted_bars": bars, "at_bar": bar, "clips_moved": res["applied"]}

    if action == "delete_bars":
        if bar is None or end_bar is None or end_bar <= bar:
            raise LiveError("delete_bars needs bar < end_bar")
        start, end = _beats(bar, bpb), _beats(end_bar, bpb)
        inside = [(t, c) for t in song["tracks"] for c in t.get("arrangement_clips") or []
                  if c["start_time"] >= start - 1e-6 and c["end_time"] <= end + 1e-6]
        require_confirm(confirm, f"delete bars {bar:g}-{end_bar:g} ({len(inside)} clips) and close the gap")
        ops = [{"track": f"song.tracks.{t['_i']}", "op": "delete", "start": c["start_time"]} for t, c in inside]
        for edge in (start, end):
            for t in song["tracks"]:
                for c in t.get("arrangement_clips") or []:
                    if c["start_time"] < edge - 1e-6 < c["end_time"] - 2e-6:
                        raise LiveError(f"{t['name']} has a clip crossing bar {edge / bpb + 1:g}; split it in Live first")
        ops += _shift_ops(song, end, -(end - start))  # deletes first, then close the gap left-to-right
        res = client.call("arrangement_ops", ops=ops, undo_name="Delete bars")
        if any(c["time"] >= start for c in cues):
            cue_ops(client, _shift_cues(song, start, -(end - start), drop_until=end))
        return {"deleted_bars": f"{bar:g}-{end_bar:g}", "clips_removed": len(inside), "ops": res["applied"]}

    if action == "duplicate_section":
        if bar is None or end_bar is None or end_bar <= bar:
            raise LiveError("duplicate_section needs bar < end_bar")
        start, end = _beats(bar, bpb), _beats(end_bar, bpb)
        to = _beats(to_bar, bpb) if to_bar is not None else end
        if to < end - 1e-6:
            raise LiveError("to_bar must be at or after end_bar")
        length = end - start
        for edge in (start, end):
            for t in song["tracks"]:
                for c in t.get("arrangement_clips") or []:
                    if c["start_time"] < edge - 1e-6 < c["end_time"] - 2e-6:
                        raise LiveError(f"{t['name']} has a clip crossing bar {edge / bpb + 1:g}; split it in Live first")
        ops = _shift_ops(song, to, length)
        copies = [{"track": f"song.tracks.{t['_i']}", "op": "copy", "start": c["start_time"],
                   "to": c["start_time"] - start + to}
                  for t in song["tracks"] for c in t.get("arrangement_clips") or []
                  if c["start_time"] >= start - 1e-6 and c["end_time"] <= end + 1e-6]
        res = client.call("arrangement_ops", ops=ops + copies, undo_name="Duplicate section")
        if any(c["time"] >= to for c in cues):
            cue_ops(client, _shift_cues(song, to, length))
        return {"duplicated": f"bars {bar:g}-{end_bar:g}", "to_bar": to / bpb + 1, "clips_copied": len(copies),
                "clips_moved": len(ops), "applied": res["applied"]}

    if action == "build_from_scenes":
        if not sequence:
            raise LiveError('build_from_scenes needs sequence=[{"scene": ..., "bars": ...}, ...]')
        start = _beats(bar if bar is not None else 1, bpb)
        total = sum(float(item["bars"]) for item in sequence) * bpb
        busy = [t["name"] for t in song["tracks"] for c in t.get("arrangement_clips") or []
                if c["start_time"] < start + total - 1e-6 and c["end_time"] > start + 1e-6]
        if busy:
            require_confirm(confirm, f"overwrite existing arrangement clips on {', '.join(sorted(set(busy))[:5])}")
        slots = client.query("song.tracks", {"props": ["name", "has_midi_input", "is_foldable"], "children": {
            "clip_slots": {"props": ["has_clip"], "children": {"clip": {"props": ["length", "is_midi_clip"]}}}}})
        ops, cue_list, warnings, t0 = [], [], [], start
        for item in sequence:
            scene_path = resolve.scene_path(client, item["scene"])
            si = int(scene_path.rsplit(".", 1)[1])
            sec_len = float(item["bars"]) * bpb
            sec_end = t0 + sec_len
            label = str(item.get("name") or client.get(scene_path, "name")["name"] or f"Scene {si + 1}")
            cue_list.append({"op": "add", "time": t0, "name": label})
            for tr in slots:
                if tr.get("is_foldable") or si >= len(tr["clip_slots"]):
                    continue
                cs = tr["clip_slots"][si]
                if not cs["has_clip"] or not cs.get("clip"):
                    continue
                L = cs["clip"]["length"]
                clip_ref = f"song.tracks.{tr['_i']}.clip_slots.{si}.clip"
                t = t0
                while t + L <= sec_end + 1e-6:
                    ops.append({"track": f"song.tracks.{tr['_i']}", "op": "copy", "clip": clip_ref, "to": t})
                    t += L
                remainder = sec_end - t
                if remainder > 1e-6:
                    if cs["clip"]["is_midi_clip"]:
                        ops.append({"track": f"song.tracks.{tr['_i']}", "op": "copy_trimmed", "clip": clip_ref,
                                    "to": t, "length": remainder})
                    else:
                        ops.append({"track": f"song.tracks.{tr['_i']}", "op": "copy", "clip": clip_ref, "to": t})
                        warnings.append(f"{tr['name']}: audio clip ({L:g} beats) runs {L - remainder:g} beats past "
                                        f"'{label}'; trim it in Live if needed")
            t0 = sec_end
        if not ops:
            raise LiveError("Those scenes have no clips")
        res = client.call("arrangement_ops", ops=ops, undo_name="Build arrangement from scenes")
        cue_ops(client, cue_list)
        return compact({"sections": [f"bar {round(c['time'] / bpb + 1, 2):g}: {c['name']}" for c in cue_list],
                        "clips_placed": res["applied"], "ends_at_bar": t0 / bpb + 1, "warnings": warnings})
    raise LiveError(f"Unknown action {action!r}")
