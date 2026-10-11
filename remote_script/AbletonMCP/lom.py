"""Path resolution and JSON serialization for the Live Object Model.

Paths are dot-separated attribute names and integer indexes rooted at a named
object, e.g. ``song.tracks.3.mixer_device.volume`` or ``app.browser``.

Must stay compatible with Live's embedded Python 3.11 and the stdlib only.
"""

PRIMITIVES = (bool, int, float, str)
REF_KEY = "$path"


class LomError(Exception):
    pass


def resolve(path, roots):
    if not path:
        raise LomError("Empty path")
    parts = path.split(".")
    if parts[0] not in roots:
        raise LomError("Unknown root '%s' (expected one of %s)" % (parts[0], ", ".join(sorted(roots))))
    obj = roots[parts[0]]
    walked = parts[0]
    for part in parts[1:]:
        try:
            if _is_index(part):
                obj = _index(obj, int(part))
            else:
                obj = getattr(obj, part)
        except LomError:
            raise
        except Exception as e:
            raise LomError("Cannot resolve '%s' at '%s.%s': %s" % (path, walked, part, e))
        if obj is None:
            raise LomError("'%s.%s' is None" % (walked, part))
        walked += "." + part
    return obj


def _is_index(part):
    return part.lstrip("-").isdigit()


def _index(obj, i):
    try:
        return obj[i]
    except TypeError:
        return list(obj)[i]
    except IndexError:
        raise LomError("Index %d out of range (length %d)" % (i, len(obj)))


def is_sequence(value):
    if isinstance(value, (str, bytes, dict)):
        return False
    return isinstance(value, (list, tuple)) or (
        hasattr(value, "__len__") and hasattr(value, "__getitem__") and not hasattr(value, "canonical_parent")
    )


def type_name(obj):
    return type(obj).__name__


def display(obj):
    """Human-readable value, e.g. '-6.0 dB' for a DeviceParameter."""
    if hasattr(obj, "str_for_value") and hasattr(obj, "value"):
        try:
            return obj.str_for_value(obj.value)
        except Exception:
            pass
    return str(obj)


def to_json(value, depth=0):
    """Serialize a value. Live objects become small reference dicts."""
    if value is None or isinstance(value, PRIMITIVES):
        return value
    if isinstance(value, dict):
        return {str(k): to_json(v, depth + 1) for k, v in value.items()}
    if is_sequence(value):
        if depth > 2:
            return "<sequence len=%d>" % len(value)
        return [to_json(v, depth + 1) for v in value]
    ref = {"_type": type_name(value)}
    name = safe_getattr(value, "name")
    if isinstance(name, str):
        ref["name"] = name
    return ref


def safe_getattr(obj, attr, default=None):
    try:
        return getattr(obj, attr)
    except Exception:
        return default


def get_prop(obj, prop):
    """Read a property; supports pseudo-props ``$type`` and ``$display``."""
    if prop == "$type":
        return type_name(obj)
    if prop == "$display":
        return display(obj)
    value = getattr(obj, prop)
    if callable(value) and not hasattr(value, "__len__"):
        raise LomError("'%s' is a method; use lom_call" % prop)
    return value


def query(obj, spec, depth=0):
    """Read a nested shape in one pass.

    spec = {"props": [...], "children": {attr: spec}, "where": {prop: value}}
    Applied element-wise to sequences; elements get an "_i" index. Props that
    raise are returned as None so one bad attribute doesn't sink the query.
    """
    if depth > 8:
        raise LomError("Query nested too deeply")
    if is_sequence(obj):
        out = []
        where = spec.get("where") or {}
        for i, item in enumerate(obj):
            if item is None:
                continue
            if where and not all(_matches(item, k, v) for k, v in where.items()):
                continue
            entry = query(item, spec, depth + 1)
            if isinstance(entry, dict):
                entry["_i"] = i
            out.append(entry)
        return out
    if obj is None or isinstance(obj, PRIMITIVES):
        return obj
    result = {}
    for prop in spec.get("props") or []:
        try:
            result[prop] = to_json(get_prop(obj, prop))
        except Exception:
            result[prop] = None
    for attr, child_spec in (spec.get("children") or {}).items():
        try:
            child = getattr(obj, attr)
        except Exception:
            result[attr] = None
            continue
        result[attr] = query(child, child_spec or {}, depth + 1) if child is not None else None
    return result


def _matches(obj, prop, expected):
    try:
        return get_prop(obj, prop) == expected
    except Exception:
        return False


def describe(obj, include_listeners=False):
    """List public properties (with current values where primitive) and methods."""
    props, methods, children = {}, [], []
    for name in sorted(dir(obj)):
        if name.startswith("_"):
            continue
        if not include_listeners and "listener" in name:
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            props[name] = "<unreadable>"
            continue
        if callable(value) and not is_sequence(value):
            methods.append(name)
        elif value is None or isinstance(value, PRIMITIVES):
            props[name] = value
        elif is_sequence(value):
            children.append("%s[%d]" % (name, len(value)))
        else:
            children.append("%s (%s)" % (name, type_name(value)))
    return {"_type": type_name(obj), "properties": props, "children": children, "methods": methods}


def decode_arg(value, roots):
    """Turn {"$path": "..."} references back into Live objects."""
    if isinstance(value, dict):
        if REF_KEY in value and len(value) == 1:
            return resolve(value[REF_KEY], roots)
        return {k: decode_arg(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_arg(v, roots) for v in value]
    return value
