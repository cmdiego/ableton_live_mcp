"""Phase 9: batch execution (one undo step), workflow prompts, and read-only resources."""

from __future__ import annotations

import json
from typing import Any

import anyio
from mcp.server.mcpserver import Context

from .. import workspace
from ..app import mcp, tool
from ..audio import report as audio_report
from ..live_client import LiveError, get_client
from ..workspace import REPO_ROOT

MAX_STEPS = 50


@tool(destructive=True)
async def batch(steps: list[dict[str, Any]], ctx: Context, stop_on_error: bool = True,
                one_undo_step: bool = True) -> dict:
    """Run several tool calls in order, e.g. to rebalance a whole mix or build a section at once:
    steps=[{"tool": "set_mixer", "args": {"track": "bass", "volume_db": -4}},
           {"tool": "set_mixer", "args": {"track": "pad", "volume_db": -9, "pan": "15L"}}].
    Arguments are validated exactly as when calling each tool. With one_undo_step (default) the
    whole batch is a single step in Live's undo history, so one undo reverts it. Stops at the
    first error unless stop_on_error=false (earlier steps stay applied). Destructive steps still
    need their own confirm=true. Up to 50 steps; batch can't contain batch."""
    if not steps:
        raise LiveError("No steps")
    if len(steps) > MAX_STEPS:
        raise LiveError(f"At most {MAX_STEPS} steps per batch")
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or not step.get("tool"):
            raise LiveError(f"Step {i} needs a 'tool' name and optional 'args'")
        if step["tool"] == "batch":
            raise LiveError("A batch can't contain another batch")
        if mcp._tool_manager.get_tool(step["tool"]) is None:
            raise LiveError(f"Step {i}: unknown tool {step['tool']!r}")
    client = get_client()
    results, failed = [], False
    if one_undo_step:
        await anyio.to_thread.run_sync(lambda: client.call("undo_group", action="begin"))
    try:
        for i, step in enumerate(steps):
            try:
                out = await mcp._tool_manager.call_tool(step["tool"], step.get("args") or {}, ctx)
                results.append({"step": i, "tool": step["tool"], "ok": True, "result": out})
            except Exception as e:  # noqa: BLE001 - report every failure in the step list
                failed = True
                message = str(e).removeprefix(f"Error executing tool {step['tool']}: ")
                results.append({"step": i, "tool": step["tool"], "ok": False, "error": message})
                if stop_on_error:
                    break
    finally:
        if one_undo_step:
            await anyio.to_thread.run_sync(lambda: client.call("undo_group", action="end"))
    done = sum(r["ok"] for r in results)
    summary = f"{done}/{len(steps)} steps applied"
    if failed and stop_on_error:
        summary += f"; stopped at step {results[-1]['step']}"
    if one_undo_step and done:
        summary += ". One undo reverts all applied steps."
    return {"summary": summary, "results": results}


# -- resources (read-only context a client can attach) --------------------------------------------


@mcp.resource("ableton://session", name="session", mime_type="application/json",
              description="Snapshot of the current Live set (same as get_session_overview).")
def session_resource() -> str:
    from .session import get_session_overview
    return json.dumps(get_session_overview(), indent=1)


@mcp.resource("ableton://arrangement", name="arrangement", mime_type="application/json",
              description="The arrangement as sections, energy and observations (same as get_arrangement).")
def arrangement_resource() -> str:
    from .arrangement import get_arrangement
    try:
        return json.dumps(get_arrangement(curve=False), indent=1)
    except LiveError as e:
        return json.dumps({"empty": str(e)})


@mcp.resource("ableton://agent-files", name="agent-files", mime_type="application/json",
              description="Audio in the agent folder: exports, references and captures.")
def agent_files_resource() -> str:
    files = audio_report.list_files(["exports", "references", "captures"])
    return json.dumps({"agent_folder": str(workspace.agent_dir()), "files": files[:200]}, indent=1)


@mcp.resource("ableton://api-notes", name="api-notes", mime_type="text/markdown",
              description="What Live 12's API can and can't do (limits behind some tool behaviour).")
def api_notes_resource() -> str:
    return (REPO_ROOT / "docs" / "api-notes.md").read_text()


# -- workflow prompts (clients show these as commands / templates) --------------------------------


@mcp.prompt(name="organize_session", description="Tidy the set: names, colors, order, and what's unused.")
def organize_session() -> str:
    return """Help me organize my Ableton Live set.

1. Call get_session_overview. Group the tracks by role (drums, bass, harmony, leads, vocals, fx, returns).
2. Propose clear names and one color per role. List empty tracks, empty scenes and clips with
   default names. Show the plan as a short table and wait for my OK.
3. Apply it with one batch call (set_track / set_clip / set_scene steps) so one undo reverts it.
   Ask before deleting anything; delete needs confirm=true.
4. Summarize what changed."""


@mcp.prompt(name="write_part", description="Write a new musical part that fits the song.")
def write_part(part: str = "bassline", style: str = "", bars: int = 8) -> str:
    return f"""Write a {part}{f' in a {style} style' if style else ''} for my song, {bars} bars long.

1. Call get_session_overview for tempo, key/scale and existing tracks. If there are chord or melody
   clips, read_clip them (analysis) so the new part fits their harmony and rhythm.
2. Pick the tool: write_chords / write_bassline / write_drums / write_arpeggio for those parts
   (basslines and arps can follow an existing chord clip), or write_notes for melodies and hooks.
3. Put it on a fitting track (create one if needed) and give the track an instrument with
   load_device if it has none. Never overwrite an existing clip unless I say so.
4. read_clip(include='analysis') to check it, explain what you wrote in musical terms, and offer
   two variations (e.g. busier/sparser, different rhythm)."""


@mcp.prompt(name="mix_check", description="Find mix problems from exported stems and fix them with EQ.")
def mix_check(stems_folder: str = "", mix: str = "latest") -> str:
    return f"""Check my mix and suggest fixes.

1. list_audio to see what's exported{f'; the stems are in {stems_folder}' if stems_folder else ''}.
   If there are no stems, ask me to export them (File > Export Audio/Video > All Individual Tracks,
   WAV, no normalization) into agent/exports/<song>/stems/.
2. analyze_audio on the mix ({mix}) for loudness, peaks, tonal balance and technical issues.
   If agent/references has a reference track, analyze_audio with reference= to compare.
3. analyze_stems on the stems folder. Prioritize: technical issues first (clipping, phase, stereo
   low end), then masking in the low and mid range, then everything else.
4. Present at most 5 moves with the reason for each. Wait for my OK, then apply EQ with apply_eq
   (it uses its own 'MCP EQ' device) and other changes with set_mixer / set_device_params, in
   one batch so one undo reverts it. Suggest re-exporting to verify."""


@mcp.prompt(name="arrangement_review", description="Review the song structure and improve it.")
def arrangement_review() -> str:
    return """Review my arrangement.

1. get_arrangement. Summarize the structure (sections, lengths, energy curve) in a few lines.
2. Go through the observations and judge which matter for this song; name the 3 most useful changes
   (e.g. a breakdown before the second drop, an element entering at bar 17, a shorter intro).
3. For changes that are pure arrangement edits, offer to do them with edit_arrangement (insert_bars,
   duplicate_section, delete_bars, locators). For changes that need new material, say which part
   to write and offer to write it. Ask before each edit; delete_bars needs confirm=true."""


@mcp.prompt(name="master_track", description="Master an exported mix to a loudness target, iterating until it's in spec.")
def master_track(target: str = "streaming", reference: str = "") -> str:
    return f"""Master my track for {target}{f', using {reference} as the reference' if reference else ''}.

1. list_audio: find the newest mix in agent/exports (ask me to export one if there's none:
   File > Export Audio/Video, WAV 24-bit, no normalization, master output without any limiter of mine).
2. plan_master(target='{target}'{f", reference='{reference}'" if reference else ''}). Explain the plan in plain words: the EQ moves
   and why, whether glue is used, the limiter gain and how hard it limits, and any warnings
   (heavy limiting means the target is too loud for this mix).
3. With my OK, apply_master. Then ask me to export the master again into agent/exports.
4. plan_master again on the new export: it measures the real result through the chain. Apply the
   corrections until it reports within_tolerance (usually 1-2 rounds).
5. Finish with the final numbers (LUFS, true peak, LRA, PLR) and the streaming playback gains."""
