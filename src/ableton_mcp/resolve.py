"""Turn human references ("bass", 3, "master", "Return A") into LOM paths."""

from __future__ import annotations

import difflib
import re
import string

from .live_client import LiveClient, LiveError

Ref = int | str


def match_name(names: list[str], spec: Ref, kind: str) -> int:
    """Index of the item `spec` refers to: an int index, or a name matched
    exactly, then by prefix/substring, then fuzzily (all case-insensitive)."""
    if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().lstrip("-").isdigit()):
        i = int(spec)
        if not -len(names) <= i < len(names):
            raise LiveError(f"{kind} index {i} out of range (there are {len(names)})")
        return i % len(names)
    want = spec.strip().lower()
    lowered = [n.lower() for n in names]
    for candidates in (
        [i for i, n in enumerate(lowered) if n == want],
        [i for i, n in enumerate(lowered) if n.startswith(want)],
        [i for i, n in enumerate(lowered) if want in n],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            options = ", ".join(f"{i}: {names[i]!r}" for i in candidates)
            raise LiveError(f"{kind} {spec!r} is ambiguous: {options}. Use an index.")
    close = difflib.get_close_matches(want, lowered, n=1, cutoff=0.6)
    if close:
        return lowered.index(close[0])
    listing = ", ".join(f"{i}: {n!r}" for i, n in enumerate(names)) or "none"
    raise LiveError(f"No {kind} matches {spec!r}. Available: {listing}")


def track_path(client: LiveClient, track: Ref) -> str:
    """Regular tracks by index or name; 'master'; return tracks by name or 'Return A'/'A'."""
    if isinstance(track, str):
        key = track.strip().lower()
        if key in ("master", "master track", "main", "main track"):
            return "song.master_track"
    names = client.query("song", {"children": {
        "tracks": {"props": ["name"]},
        "return_tracks": {"props": ["name"]},
    }})
    tracks = [t["name"] for t in names["tracks"]]
    returns = [t["name"] for t in names["return_tracks"]]
    if isinstance(track, str) and not track.strip().lstrip("-").isdigit():
        letter = re.fullmatch(r"return\s*([a-z])", track.strip().lower())
        if letter:
            idx = string.ascii_lowercase.index(letter.group(1))
            if idx >= len(returns):
                raise LiveError(f"There is no return track {letter.group(1).upper()} ({len(returns)} returns)")
            return f"song.return_tracks.{idx}"
        try:
            return f"song.tracks.{match_name(tracks, track, 'track')}"
        except LiveError as track_err:
            if not returns:
                raise
            try:
                return f"song.return_tracks.{match_name(returns, track, 'return track')}"
            except LiveError:
                raise track_err from None
    return f"song.tracks.{match_name(tracks, track, 'track')}"


def scene_path(client: LiveClient, scene: Ref) -> str:
    names = [s["name"] for s in client.query("song.scenes", {"props": ["name"]})]
    return f"song.scenes.{match_name(names, scene, 'scene')}"


def clip_slot_path(client: LiveClient, track: Ref, slot: int) -> str:
    return f"{track_path(client, track)}.clip_slots.{int(slot)}"


def clip_path(client: LiveClient, track: Ref, slot: int) -> str:
    slot_path = clip_slot_path(client, track, slot)
    if not client.get(slot_path, "has_clip")["has_clip"]:
        raise LiveError(f"Clip slot {slot} on track {track!r} is empty")
    return f"{slot_path}.clip"


def any_clip_path(client: LiveClient, track: Ref, slot: int | None = None,
                  arrangement_clip: int | None = None) -> str:
    """A session clip (track + slot) or an arrangement clip (track + index from get_track_details)."""
    if (slot is None) == (arrangement_clip is None):
        raise LiveError("Give either slot (session clip) or arrangement_clip (arrangement index)")
    if slot is not None:
        return clip_path(client, track, slot)
    tpath = track_path(client, track)
    count = len(client.get(tpath, "arrangement_clips")["arrangement_clips"])
    if not 0 <= arrangement_clip < count:
        raise LiveError(f"Track {track!r} has {count} arrangement clips; index {arrangement_clip} is out of range")
    return f"{tpath}.arrangement_clips.{arrangement_clip}"


def device_path(client: LiveClient, track: Ref, device: Ref) -> str:
    tpath = track_path(client, track)
    names = [d["name"] for d in client.query(f"{tpath}.devices", {"props": ["name"]})]
    return f"{tpath}.devices.{match_name(names, device, 'device')}"


def parameter_path(client: LiveClient, dpath: str, parameter: Ref) -> str:
    names = [p["name"] for p in client.query(f"{dpath}.parameters", {"props": ["name"]})]
    return f"{dpath}.parameters.{match_name(names, parameter, 'parameter')}"
