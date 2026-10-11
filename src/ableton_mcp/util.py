"""Small shared helpers for tool modules."""

from __future__ import annotations

from typing import Any

from .live_client import LiveError

COLOR_NAMES = {
    "red": 0xFF3636, "orange": 0xFF9A1E, "yellow": 0xFFF034, "lime": 0x87FF67,
    "green": 0x3DC300, "teal": 0x00BFAF, "cyan": 0x19E9FF, "blue": 0x1065E0,
    "purple": 0x8D4BE3, "magenta": 0xE553A0, "pink": 0xFFA6E3, "brown": 0x9F7A50,
    "gray": 0x7F7F7F, "grey": 0x7F7F7F, "white": 0xFFFFFF,
}


def parse_color(color: str | int) -> int:
    """Hex string ('#ff8800'), color name, or 0xRRGGBB int -> RGB int.
    Live snaps it to the nearest palette color."""
    if isinstance(color, int):
        return color
    c = color.strip().lower()
    if c in COLOR_NAMES:
        return COLOR_NAMES[c]
    try:
        return int(c.lstrip("#"), 16)
    except ValueError:
        raise LiveError(f"Unknown color {color!r}. Use a hex value like '#ff8800' or one of: {', '.join(COLOR_NAMES)}")


def hex_color(value: Any) -> str | None:
    return f"#{value:06x}" if isinstance(value, int) else None


def compact(value: Any) -> Any:
    """Drop None values and empty lists/dicts recursively to save tokens."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None and not (isinstance(v, (list, dict)) and not v)}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def require_confirm(confirm: bool, what: str) -> None:
    if not confirm:
        raise LiveError(f"This would {what}. Ask the user, then call again with confirm=true.")
