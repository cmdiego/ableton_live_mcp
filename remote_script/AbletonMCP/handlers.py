"""Command handlers. Every function here runs on Live's main thread.

Kept generic on purpose: new features belong in the MCP server, which composes
these commands. This module can be hot-reloaded with the ``reload`` command.
"""

import contextlib
import re

from . import lom

# An outer undo group opened by undo_group(begin). Live doesn't nest undo steps, so while
# it is open, handlers must not open their own (an inner end would split the group).
_GROUP = {"open": False}


def _undo(ctx, name):
    if _GROUP["open"]:
        return contextlib.nullcontext()
    return ctx.undo_step(name)


def undo_group(ctx, params):
    """begin/end one Live undo step spanning several requests (used by the batch tool)."""
    song = ctx.roots["song"]
    if params["action"] == "begin":
        if _GROUP["open"]:
            song.end_undo_step()  # never leave a stale group open
        song.begin_undo_step()
        _GROUP["open"] = True
    else:
        if _GROUP["open"]:
            song.end_undo_step()
        _GROUP["open"] = False
    return {"open": _GROUP["open"]}

NOTE_FIELDS = (
    "note_id", "pitch", "start_time", "duration", "velocity", "mute",
    "probability", "velocity_deviation", "release_velocity",
)


def ping(ctx, params):
    app = ctx.roots["app"]
    return {
        "script_version": ctx.version,
        "live_version": "%s.%s.%s" % (
            app.get_major_version(), app.get_minor_version(), app.get_bugfix_version()),
        "song": lom.safe_getattr(ctx.roots["song"], "name", None),
    }


def runtime_info(ctx, params):
    import sys
    import Live
    if params.get("module"):
        mod = Live
        for part in params["module"].split("."):
            mod = getattr(mod, part)
        out = {}
        for n in dir(mod):
            if n.startswith("_"):
                continue
            obj = getattr(mod, n)
            out[n] = (obj.__doc__ or "").strip()[:300]
        return out
    return {
        "python": sys.version,
        "live_modules": sorted(n for n in dir(Live) if not n.startswith("_")),
        "live_base": sorted(n for n in dir(Live.Base) if not n.startswith("_")),
    }


_NUMBER = re.compile(r"^\s*([-+]?(?:inf|\d+(?:\.\d*)?|\.\d+))\s*([a-zA-Z%°]*)", re.IGNORECASE)
_UNIT_SCALE = {"khz": 1000.0, "s": 1000.0}  # normalize to Hz and ms


def parse_display(text):
    """'-6.0 dB' -> -6.0, '1.20 kHz' -> 1200.0, '2.00 s' -> 2000.0, '25L' -> -25.0, 'C' -> 0.0."""
    t = text.strip()
    if t.upper() == "C":
        return 0.0
    if t[-1:] in ("L", "R") and t[:-1].strip().replace(".", "", 1).isdigit():
        v = float(t[:-1])
        return -v if t.endswith("L") else v
    m = _NUMBER.match(t)
    if not m:
        return None
    return float(m.group(1)) * _UNIT_SCALE.get(m.group(2).lower(), 1.0)


def param_info(ctx, params):
    p = lom.resolve(params["path"], ctx.roots)
    info = {
        "name": p.name, "value": p.value, "min": p.min, "max": p.max,
        "display": lom.display(p), "quantized": bool(lom.safe_getattr(p, "is_quantized", False)),
        "display_min": p.str_for_value(p.min), "display_max": p.str_for_value(p.max),
        "enabled": lom.safe_getattr(p, "is_enabled", True),
    }
    items = lom.safe_getattr(p, "value_items")
    if items is not None and len(items):
        info["value_items"] = list(items)
    return info


def value_for_display(p, target):
    """Raw value at which parameter p displays `target`: a number in the display's unit
    (dB, Hz, ms, %, pan L/R as -/+), a string like '800 Hz', or a quantized item name."""
    items = lom.safe_getattr(p, "value_items")
    if isinstance(target, str) and items is not None and len(items):
        names = [str(i).lower() for i in items]
        if target.lower() not in names:
            raise lom.LomError("'%s' is not one of %s" % (target, list(items)))
        return p.min + names.index(target.lower())
    raw_target = target
    if isinstance(target, str):
        target = parse_display(target)
        if target is None:
            raise lom.LomError("Can't read a number from %r" % raw_target)
    f = lambda v: parse_display(p.str_for_value(v))
    lo, hi = p.min, p.max
    f_lo, f_hi = f(lo), f(hi)
    if f_lo is None or f_hi is None:
        raise lom.LomError("'%s' doesn't display numbers (%s .. %s)" % (p.name, p.str_for_value(lo), p.str_for_value(hi)))
    increasing = f_hi >= f_lo
    if target <= min(f_lo, f_hi):
        return lo if increasing else hi
    if target >= max(f_lo, f_hi):
        return hi if increasing else lo
    for _ in range(48):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if fm is None:
            break
        if (fm < target) == increasing:
            lo = mid
        else:
            hi = mid
    # Displays are rounded/stepped (e.g. pan in whole L/R steps): take the closer bound.
    return min((lo, hi), key=lambda v: abs((f(v) or 0.0) - target))


def param_set_display(ctx, params):
    p = lom.resolve(params["path"], ctx.roots)
    p.value = value_for_display(p, params["target"])
    return {"value": p.value, "display": lom.display(p)}


def param_values_for_display(ctx, params):
    """Convert display targets to raw values without changing the parameter."""
    p = lom.resolve(params["path"], ctx.roots)
    return [value_for_display(p, t) for t in params["targets"]]


def envelope(ctx, params):
    """Clip automation for one parameter. action: write | clear | read.

    write: points [[time, raw value], ...] in beats from the clip start; Live interpolates
    linearly between breakpoints (two points at nearly the same time make a jump). The
    envelope is replaced unless keep=true. read: values at `times` (None if no envelope)."""
    import Live
    clip = lom.resolve(params["clip"], ctx.roots)
    param = lom.resolve(params["param"], ctx.roots)
    action = params.get("action", "write")
    if action == "clear":
        clip.clear_envelope(param)
        return {"cleared": True}
    env = clip.automation_envelope(param)
    if action == "read":
        if env is None:
            return None
        return [env.value_at_time(t) for t in params["times"]]
    if env is not None and not params.get("keep"):
        clip.clear_envelope(param)
        env = None
    if env is None:
        env = clip.create_automation_envelope(param)
    if env is None:
        raise lom.LomError("Live won't automate '%s' in this clip" % param.name)
    lo, hi = param.min, param.max
    for t, v in params["points"]:
        env.create_event(Live.Envelope.EnvelopeEvent(float(t), min(hi, max(lo, float(v)))))
    return {"points": len(params["points"])}


def _clip_at(track, start):
    for clip in track.arrangement_clips:
        if abs(clip.start_time - start) < 1e-6:
            return clip
    raise lom.LomError("No arrangement clip starts at beat %g on '%s'" % (start, track.name))


def _copy_trimmed(track, src, to, length):
    """New MIDI clip of exactly `length` beats at `to` with src's notes from its start marker."""
    import Live
    new = track.create_midi_clip(float(to), float(length))
    offset = src.start_marker
    specs = []
    for n in src.get_notes_extended(0, 128, offset, length):
        specs.append(Live.Clip.MidiNoteSpecification(
            pitch=n.pitch, start_time=n.start_time - offset, duration=min(n.duration, length - (n.start_time - offset)),
            velocity=n.velocity, mute=n.mute))
    if specs:
        new.add_new_notes(tuple(specs))
    new.name = src.name
    new.color = src.color
    return new


def arrangement_ops(ctx, params):
    """Edit arrangement clips addressed by (track path, start beat), as one undo step.

    ops: [{"track": path, "op": "copy"|"move"|"delete"|"copy_trimmed", "start": beat, "to": beat,
    "length": beats}]. Clips are looked up by start time right before each op, so earlier ops
    can't shift later addresses. Session clips can be copied in with {"op": "copy", "clip": path}."""
    done = []
    with _undo(ctx, params.get("undo_name") or "Arrangement edit"):
        for op in params["ops"]:
            track = lom.resolve(op["track"], ctx.roots)
            kind = op["op"]
            src = lom.resolve(op["clip"], ctx.roots) if op.get("clip") else _clip_at(track, op["start"])
            if kind == "copy":
                track.duplicate_clip_to_arrangement(src, float(op["to"]))
            elif kind == "move":
                track.duplicate_clip_to_arrangement(src, float(op["to"]))
                track.delete_clip(_clip_at(track, op["start"]))
            elif kind == "delete":
                track.delete_clip(src)
            elif kind == "copy_trimmed":
                _copy_trimmed(track, src, op["to"], op["length"])
            else:
                raise lom.LomError("Unknown arrangement op %r" % kind)
            done.append(kind)
    return {"applied": len(done)}


BROWSER_ROOTS = ("instruments", "audio_effects", "midi_effects", "drums", "sounds", "plugins",
                 "max_for_live", "user_library", "packs", "samples", "clips")


def browser_search(ctx, params):
    """Breadth-first search of Live's browser by name. Returns paths usable as {"$path": ...}."""
    words = [w for w in params["query"].lower().split() if w]
    roots = params.get("roots") or list(BROWSER_ROOTS)
    limit = params.get("limit", 20)
    max_depth = params.get("max_depth", 4)
    max_visits = params.get("max_visits", 20000)
    browser = ctx.roots["app"].browser
    results, visits = [], 0
    queue = [(getattr(browser, r), "app.browser." + r, 0) for r in roots if hasattr(browser, r)]
    while queue and len(results) < limit and visits < max_visits:
        item, path, depth = queue.pop(0)
        visits += 1
        name = lom.safe_getattr(item, "name", "") or ""
        lowered = name.lower()
        if depth > 0 and all(w in lowered for w in words) and (item.is_loadable or not params.get("loadable_only", True)):
            results.append({"name": name, "path": path, "loadable": bool(item.is_loadable),
                            "folder": bool(item.is_folder), "uri": lom.safe_getattr(item, "uri")})
        if depth < max_depth and (item.is_folder or depth == 0):
            for i, child in enumerate(item.children):
                queue.append((child, "%s.children.%d" % (path, i), depth + 1))
    return {"results": results, "visited": visits, "truncated": visits >= max_visits}


def lom_get(ctx, params):
    obj = lom.resolve(params["path"], ctx.roots)
    props = params.get("props")
    if not props:
        return lom.to_json(obj)
    return {p: lom.to_json(lom.get_prop(obj, p)) for p in props}


def lom_set(ctx, params):
    obj = lom.resolve(params["path"], ctx.roots)
    values = params.get("values")
    if values is None:
        values = {params["prop"]: params["value"]}
    for prop, value in values.items():
        setattr(obj, prop, lom.decode_arg(value, ctx.roots))
    return {p: lom.to_json(lom.safe_getattr(obj, p)) for p in values}


def lom_call(ctx, params):
    obj = lom.resolve(params["path"], ctx.roots)
    method = getattr(obj, params["method"])
    args = lom.decode_arg(params.get("args") or [], ctx.roots)
    kwargs = lom.decode_arg(params.get("kwargs") or {}, ctx.roots)
    result = method(*args, **kwargs)
    if params.get("describe") and result is not None and not isinstance(result, lom.PRIMITIVES):
        return lom.describe(result)
    return lom.to_json(result)


def lom_describe(ctx, params):
    obj = lom.resolve(params["path"], ctx.roots)
    return lom.describe(obj, params.get("include_listeners", False))


def lom_query(ctx, params):
    obj = lom.resolve(params["path"], ctx.roots)
    return lom.query(obj, params.get("spec") or {})


def get_notes(ctx, params):
    clip = _midi_clip(ctx, params["path"])
    notes = clip.get_notes_extended(
        params.get("from_pitch", 0), params.get("pitch_span", 128),
        params.get("from_time", 0.0), params.get("time_span", max(clip.length, 1.0) + 1e6))
    out = []
    for n in notes:
        out.append({f: lom.safe_getattr(n, f) for f in NOTE_FIELDS})
    out.sort(key=lambda n: (n["start_time"], n["pitch"]))
    return out


def add_notes(ctx, params):
    import Live  # only available inside Live
    clip = _midi_clip(ctx, params["path"])
    specs = []
    for n in params["notes"]:
        kwargs = {
            "pitch": int(n["pitch"]),
            "start_time": float(n["start_time"]),
            "duration": float(n["duration"]),
            "velocity": float(n.get("velocity", 100)),
            "mute": bool(n.get("mute", False)),
        }
        for opt in ("probability", "velocity_deviation", "release_velocity"):
            if opt in n:
                kwargs[opt] = float(n[opt])
        specs.append(Live.Clip.MidiNoteSpecification(**kwargs))
    ids = clip.add_new_notes(tuple(specs))
    return {"added": len(specs), "note_ids": list(ids) if ids is not None else []}


def remove_notes(ctx, params):
    clip = _midi_clip(ctx, params["path"])
    if params.get("note_ids"):
        clip.remove_notes_by_id(tuple(params["note_ids"]))
        return {"removed_ids": len(params["note_ids"])}
    clip.remove_notes_extended(
        params.get("from_pitch", 0), params.get("pitch_span", 128),
        params.get("from_time", 0.0), params.get("time_span", clip.length + 1e6))
    return {"removed_range": True}


def batch(ctx, params):
    """Run several commands in one main-thread tick, optionally as one undo step."""
    results = []
    with _undo(ctx, params.get("undo_name")):
        for i, cmd in enumerate(params["commands"]):
            name = cmd["cmd"]
            if name not in COMMANDS or name == "batch":
                raise lom.LomError("batch[%d]: unknown command '%s'" % (i, name))
            try:
                results.append(COMMANDS[name](ctx, cmd.get("params") or {}))
            except Exception as e:
                raise lom.LomError("batch[%d] %s failed: %s" % (i, name, e))
    return results


def _midi_clip(ctx, path):
    clip = lom.resolve(path, ctx.roots)
    if not lom.safe_getattr(clip, "is_midi_clip", False):
        raise lom.LomError("'%s' is not a MIDI clip" % path)
    return clip


COMMANDS = {
    "ping": ping,
    "undo_group": undo_group,
    "runtime_info": runtime_info,
    "param_info": param_info,
    "param_set_display": param_set_display,
    "param_values_for_display": param_values_for_display,
    "envelope": envelope,
    "browser_search": browser_search,
    "arrangement_ops": arrangement_ops,
    "lom_get": lom_get,
    "lom_set": lom_set,
    "lom_call": lom_call,
    "lom_describe": lom_describe,
    "lom_query": lom_query,
    "get_notes": get_notes,
    "add_notes": add_notes,
    "remove_notes": remove_notes,
    "batch": batch,
}
