# Ableton Live 12 assistant: executive summary

## What it is

An MCP server that lets an AI assistant work directly inside Ableton Live 12 Suite. It can read and organize a set, compose parts, shape sounds, analyze and fix mixes, restructure the arrangement, master to a loudness target, and record Live's real output to check its own work.

It has three pieces:

| Piece | Role |
|---|---|
| **AbletonMCP Remote Script** ([`remote_script/AbletonMCP/`](../remote_script/AbletonMCP/), inside Live) | A thin bridge to Live's API, about 10 ms per call |
| **MCP server** ([`src/ableton_mcp/`](../src/ableton_mcp/)) | 35 tools, 5 workflow prompts, 4 resources, and all the music, audio and mastering logic |
| **AbletonMCP Analyzer** ([`m4l/`](../m4l/), Max for Live device on the master) | Records Live's real output and provides a live meter |

**Status:** Phases 0–11 of the original plan are done. 180 offline tests pass, as do 41 end-to-end checks against a running Live 12.4.6 Suite ([`scripts/smoke.py`](../scripts/smoke.py)). How it works is in [architecture.md](architecture.md), why it's built this way in
[decisions.md](decisions.md), and Live's API quirks in [api-notes.md](api-notes.md).

## How to use it

**One-time setup**
1. Install the dependencies:
   ```bash
   uv sync
   ```
2. Install the Remote Script and copy the analyzer device into the User Library:
   ```bash
   scripts/install_remote_script.sh
   ```
3. Open **Ableton Live 12 Suite** (not Standard), then go to Settings → Link, Tempo & MIDI → Control Surface and choose **AbletonMCP** (Input/Output: None).
4. Connect a client: Claude Code, Claude Desktop, OpenCode, Codex, Copilot, Cursor and Gemini CLI are covered in [clients.md](clients.md).
5. Optional: ask the assistant to install the analyzer, which runs `analyzer(action='install')`.

**Everyday use**
- **Plain language:** "write a funky bassline for the chords in scene 2", "why is my mix muddy?", "add a breakdown before the second drop", "master this for Spotify".
- **Workflow prompts:** `organize_session`, `write_part`, `mix_check`, `arrangement_review`, `master_track`. In Claude Code they appear as `/mcp__ableton__…`.
- **The [`agent/`](../agent/README.md) folder:** export mixes and stems to `agent/exports/` and put reference tracks in `agent/references/`. Captures (`agent/captures/`) and reports (`agent/reports/`) are written automatically. Audio and reports are git-ignored.
- **Undo:** each request that runs as a `batch` is one Live undo step. The `undo` tool also steps back through Live's history.
- **Health checks:** run the end-to-end check on a scratch set, and the offline tests (no Live needed):
  ```bash
  uv run python scripts/smoke.py
  ```
  ```bash
  uv run pytest
  ```

## Capabilities and applications

| Area | What it does | Typical use |
|---|---|---|
| **Set and organization** | Overview, names, colors, create/duplicate/delete, playback; refer to tracks by name | Cleaning up a messy session |
| **Composition** | Chords (Roman numerals, chord symbols, 20 presets, smooth voicing), basslines that follow a chord clip, drums matched to Drum Rack pads, arpeggios, free melodies, 15 note transforms | Sketching ideas, writing parts that fit, variations |
| **MIDI analysis** | Key, chords including extensions, rhythm grid, swing, drum step patterns | Understanding or teaching what a clip does |
| **Sound design** | Load built-in devices, presets, plugins and Max devices; set parameters by displayed value ("800 Hz"); automation (ramps, LFOs, steps, freehand points) | Building sounds and movement |
| **Mixing** | Exact dB mixer; stem analysis that finds masking over time, high-pass candidates, resonances and stereo bass; `apply_eq` writes into its own "MCP EQ" | Diagnosing and fixing a mix |
| **Audio analysis** | Broadcast-standard loudness (BS.1770), true peak, tonal balance, stereo image, reference comparison, technical issues | Quality control, matching a reference |
| **Arrangement** | Sections, energy curve, structural observations; locators; insert/delete/duplicate bars; build an arrangement from session scenes | Turning loops into songs, reviewing structure |
| **Mastering** | Plans a chain by simulating it on the audio, builds it on the master (EQ, Glue, Utility, True Peak Limiter), then re-checks until it's within ±0.5 LU | Streaming, Apple, loud or club masters, or matching a reference |
| **Live audio** | Records the real master output (32-bit float, starting at a chosen bar); live peak/RMS meter | Verifying results without manual exports |
| **Power and multi-client** | `batch` (one undo step), resources, `live_api` raw access; portable schemas tested with Claude-compatible clients, OpenCode/GPT‑5.5 and Codex | Complex edits; using any MCP client |

**Measured accuracy**
- **Latency:** about 10 ms per call.
- **Loudness meter:** exactly −20.00 LUFS on the standard's reference tone.
- **Capture:** matches the source file within 0.02 LU.
- **Mastering:** lands within 0.1 LU of target on the first try for non-dynamic material, and within about 0.6 LU when glue compression is involved, after which one correction round closes the gap.

## Things to watch out for

**Limits of Live's API** (handled, but worth knowing)
- **No group tracks:** they can't be created through the API.
- **Clips can't be split:** arrangement edits that would cut through a clip are refused.
- **Track automation lanes are out of reach:** they can't be read, written or moved. Arrangement automation goes inside clips instead, and it doesn't move with insert/delete edits.
- **No export through the API:** captures are the substitute, and they're real-time (30 s of audio takes 30 s).
- **One instrument per track.**

**Possible unexpected behavior**
- **Don't touch Live during locator edits, captures or batches.** Locators can only be toggled at the playhead, so moving the playhead mid-operation could hit the wrong locator. Stopping or moving playback during a capture corrupts it. Manual edits made during a batch get folded into the batch's undo step.
- **Playback starts from Live's start marker (`song.start_time`), not the playhead.** The tools handle this and capture restores the marker, but raw `live_api` calls don't.
- **Unknown scale names silently become Major in Live.** `set_song` validates them; `live_api` bypasses that and every other safety check (confirmations, validation).
- **Old recordings carry stored clip settings.** A `.asd` file next to the audio can make Live play it warped or from a different start than the file.
- **Where the analyzer sits matters.** It must be last on the master, or captures miss later devices; `apply_master` keeps it last. Only one analyzer per set (it uses fixed UDP port 9879). Two clients capturing at the same time will interfere.
- **The agent folder path must not contain spaces.** Max splits messages on spaces, so captures would break if `ABLETON_MCP_AGENT_DIR` pointed at such a path. The default path is fine.
- **Standard vs Suite:** Suite-only instruments and Max for Live fail in Live 12 Standard.

**Heuristics are judgments, not facts**
- Chord names can be ambiguous (the same notes can be Am7 or C6), and section labels and stem roles are educated guesses.
- Mastering EQ without a reference only fixes obvious outliers. The Glue model was calibrated on one mix (about 0.5 LU residual), so unusual material may need an extra correction round.
- Bar math assumes one time signature and a constant tempo. A mix's loudness curve assumes the export starts at bar 1.

**Maintenance risks**
- **SDK internals:** schema rewriting and `batch` use private internals of the MCP SDK 2.x (`_tool_manager`), so an SDK upgrade could break them. The version isn't pinned yet.
- **Reloading the Live script:** changes to `surface.py`, `server.py` or `dispatcher.py` need the control surface toggled off and on; changes to `__init__.py` need a Live restart. `handlers.py` and `lom.py` hot-reload.
- **Tool budget:** 35 of 40 used, and some clients cap the total number of tools (VS Code Copilot: 128 across all servers).

## Next steps (suggested priority)

Concrete open tasks and investigations are tracked in [TODO.md](../TODO.md).

1. **Validate on a real song.** Export the stems and mix of a track in progress, then run `mix_check` and `master_track`, and tune the heuristics on real material.
2. **Hardening (small):**
   - pin the `mcp` version;
   - add GitHub Actions to run `pytest` (it needs no Live);
   - make the client configs portable;
   - handle agent paths with spaces.
3. **Better models:** calibrate Glue and Limiter on more mixes, and add genre target curves for mastering without a reference.
4. **Features, if useful:** tempo and time-signature changes in the bar math; a true LUFS meter inside the Max device; bouncing session clips to audio via capture; Max for Live MIDI Tools.
5. **GUI:** deferred for now. If it's ever built, it will be Claude-only, on the Claude Agent SDK.
