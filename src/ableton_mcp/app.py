"""The shared MCP server instance. Tool modules register with `@tool()`.

Schemas are rewritten to a portable subset (no anyOf/null unions, no titles,
no $refs) so they work with Claude, OpenAI/Copilot, Gemini, Codex and OpenCode.
Input is still validated against the full Python signature, so a client sending
3 where the schema says string ("3") is fine either way.
"""

from __future__ import annotations

import copy
import functools
import inspect
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

INSTRUCTIONS = """\
You control Ableton Live 12 through these tools. Start with get_session_overview.

Conventions
- Indexes are 0-based and match get_session_overview. Tracks, scenes, devices and parameters can
  be named instead ("bass", "Return A", "master"). Bars are 1-based in arrangement tools.
- Beats are quarter notes (a 4/4 bar = 4 beats). Parameter values use Live's display units
  ("800 Hz", "-6 dB", "Saw").
- Ask before deleting or overwriting; destructive calls need confirm=true. Use batch for multi-step
  edits so one undo reverts them all; the undo tool steps back through Live's history.

Capabilities
- Set: get_session_overview, get_track_details, set_track/clip/scene/song, create, duplicate, delete,
  playback, undo
- Compose: write_chords, write_bassline, write_drums, write_arpeggio, write_notes, transform_notes,
  read_clip
- Sound: load_device, get_device, set_device_params, automate
- Mix: get_mix_state, set_mixer, analyze_stems, apply_eq; audio files: list_audio, analyze_audio
  (agent/exports holds exports, agent/references reference tracks)
- Song structure: get_arrangement, edit_arrangement
- Mastering: plan_master (measures an export or capture, plans/checks), apply_master (builds the chain)
- Live audio (Suite): analyzer: install the Max for Live device, capture the master, live meter
- Anything else: live_api (raw Live Object Model; start with op='describe')
Workflow prompts: organize_session, write_part, mix_check, arrangement_review, master_track.
"""

# Not every client forwards server instructions to the model, so the conventions
# also live on the parameters themselves.
PARAM_DOCS = {
    "track": "Track: 0-based index from get_session_overview, or a name ('bass'), 'Return A', or 'master'.",
    "to_track": "Target track: 0-based index or name.",
    "slot": "Session clip slot (scene row), 0-based.",
    "to_slot": "Target clip slot (scene row), 0-based.",
    "scene": "Scene: 0-based index or name.",
    "arrangement_clip": "Arrangement clip index on the track (from get_track_details).",
    "confirm": "Must be true to actually do this destructive action. Ask the user first.",
    "color": "Hex like '#ff8800' or a name: red, orange, yellow, lime, green, teal, cyan, blue, purple, magenta, pink, brown, gray, white.",
    "start_beat": "In beats (quarter notes) from the clip start.",
    "end_beat": "In beats (quarter notes) from the clip start.",
    "position_beats": "Song position in beats (quarter notes; a 4/4 bar is 4 beats).",
}

mcp = MCPServer("ableton", instructions=INSTRUCTIONS)


def portable_schema(schema: dict) -> dict:
    """Rewrite a pydantic JSON schema into the subset every major MCP client accepts."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        node = {k: walk(v) for k, v in node.items() if k not in ("title", "$defs")}
        if "anyOf" in node:
            options = [o for o in node.pop("anyOf") if o.get("type") != "null"]
            if len(options) == 1:
                node = {**options[0], **node}
            elif all(set(o) <= {"type"} for o in options):
                # e.g. str | int: send text; the tool parses digits/numbers itself.
                types = [o["type"] for o in options]
                node = {"type": "string" if "string" in types else types[0], **node}
            else:
                node["anyOf"] = options  # complex unions: leave as is (tests flag them)
        if node.get("default", 0) is None:
            del node["default"]
        return node

    return walk(schema)


def tool(read_only: bool = False, destructive: bool = False, **kwargs: Any) -> Callable:
    """Register a tool with a portable schema and safety annotations (clients use these to
    decide when to ask for approval). ValueError/KeyError become readable tool errors."""
    kwargs.setdefault("annotations", ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive and not read_only,
        openWorldHint=False,  # only talks to Live on this machine
    ))

    def decorate(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kw: Any) -> Any:
                try:
                    return await fn(*args, **kw)
                except (ValueError, KeyError) as e:
                    raise ToolError(str(e)) from e
        else:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kw: Any) -> Any:
                try:
                    return fn(*args, **kw)
                except (ValueError, KeyError) as e:
                    raise ToolError(str(e)) from e

        mcp.tool(**kwargs)(wrapper)
        registered = mcp._tool_manager.get_tool(kwargs.get("name") or fn.__name__)
        schema = portable_schema(copy.deepcopy(registered.parameters))
        for name, prop in schema.get("properties", {}).items():
            if name in PARAM_DOCS and "description" not in prop:
                prop["description"] = PARAM_DOCS[name]
        registered.parameters = schema
        return wrapper

    return decorate
