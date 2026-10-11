# Architecture

```
 MCP client (Claude Code/Desktop, OpenCode, Codex, …)
      │  MCP over stdio (tools, prompts, resources)
      ▼
 ableton-mcp server  ── src/ableton_mcp/ ─────────────────────────────────────────────┐
 │  tools/ (35 tools)  → resolve.py (names → LOM paths) → live_client.py (JSON lines)  │
 │  theory/ (music, pure)   audio/ (DSP, pure)   m4l.py (OSC/UDP)   workspace.py (agent/)│
 └──────┬───────────────────────────────────────────────────────────────┬────────────┘
        │ TCP 127.0.0.1:9877, newline-delimited JSON                      │ UDP OSC → 9879
        ▼                                                                 │ replies ← own port (/reply)
 Ableton Live 12 (Suite)                                                  ▼
 ├─ AbletonMCP Remote Script (remote_script/AbletonMCP/)        AbletonMCP Analyzer (m4l/)
 │    polled from a 10 ms main-thread timer → Live Object Model   on the master: capture + meter
 └─ agent/ folder on disk: exports, references, captures (float WAV), reports, presets
```

## Components

### Remote Script (`remote_script/AbletonMCP/`, Live's Python 3.11, stdlib only)

| File | Role |
|---|---|
| `__init__.py` | `create_instance`: reloads all modules (Live caches them across control-surface toggles), then builds the surface |
| `surface.py` | `ControlSurface` subclass; starts the TCP server and a `Live.Base.Timer` (10 ms) that calls `server.poll()`; `update_display` is the fallback |
| `server.py` | Non-blocking TCP server **without threads**: `poll()` accepts, reads, executes complete requests and writes replies, with a 50 ms budget per tick |
| `dispatcher.py` | Runs one request against the handler table; `Context` gives handlers the `song`/`app` roots and an undo-step helper |
| `handlers.py` | The command set (hot-reloadable) |
| `lom.py` | Path resolution (`song.tracks.3.devices.0`), serialization, nested `query` specs, `describe` |

The handlers in `handlers.py`:

| Group | Commands |
|---|---|
| Generic | `lom_get`, `lom_set`, `lom_call`, `lom_query`, `lom_describe` |
| Notes | `get_notes`, `add_notes`, `remove_notes` |
| Parameters | `param_info`, `param_set_display`, `param_values_for_display` (bisection against Live's own display strings) |
| Automation | `envelope` (breakpoint write/read/clear) |
| Browser | `browser_search` |
| Arrangement | `arrangement_ops` (edits addressed by track + start time) |
| Undo | `batch`, `undo_group` |
| Diagnostics | `ping`, `runtime_info` |

### MCP server (`src/ableton_mcp/`)

| Module | Role |
|---|---|
| `app.py` | `MCPServer` instance, server `INSTRUCTIONS`, and the `@tool()` decorator (portable schema rewrite, shared `PARAM_DOCS`, safety annotations, error conversion) |
| `live_client.py` | JSON-lines client with request ids; reconnects once on connection errors, never retries timeouts; `LiveError` is a `ToolError` |
| `resolve.py` | Names → LOM paths: tracks ('bass', 'Return A', 'master'), scenes, clips, devices, parameters (exact → prefix → substring → fuzzy) |
| `theory/` | Pure music logic: scales, chord parsing and voicing, patterns, generators, transforms, MIDI analysis, arrangement analysis |
| `audio/` | Pure DSP: loudness (BS.1770), spectrum, stereo, reports, reference comparison, stem analysis, mastering simulation and planning |
| `m4l.py` | Minimal OSC codec and `AnalyzerLink` (UDP to the device; replies on its own port) |
| `workspace.py` | The `agent/` folder (override with `ABLETON_MCP_AGENT_DIR`) |
| `tools/` | Tool modules (below); importing one registers its tools |

| Tools module | Tools |
|---|---|
| `session.py` | `get_session_overview`, `get_track_details`, `set_track`, `set_clip`, `set_scene`, `set_song`, `create`, `duplicate`, `delete`, `playback`, `undo`, `live_api` |
| `midi.py` | `read_clip` |
| `compose.py` | `write_chords`, `write_bassline`, `write_drums`, `write_arpeggio`, `write_notes`, `transform_notes` |
| `mixer.py` | `get_mix_state`, `set_mixer` |
| `devices.py` | `load_device`, `get_device`, `set_device_params`, `automate` |
| `analysis.py` | `list_audio`, `analyze_audio` |
| `mixing.py` | `analyze_stems`, `apply_eq` |
| `arrangement.py` | `get_arrangement`, `edit_arrangement` (locators are handled server-side by `cue_ops`) |
| `mastering.py` | `plan_master`, `apply_master` |
| `live_audio.py` | `analyzer` (install, status, capture, meter) |
| `workflow.py` | `batch`; prompts `organize_session`, `write_part`, `mix_check`, `arrangement_review`, `master_track`; resources `ableton://session`, `ableton://arrangement`, `ableton://agent-files`, `ableton://api-notes` |

### Max for Live analyzer (`m4l/`)

- `build_analyzer.py` generates `AbletonMCP Analyzer.amxd` from Ableton's own *Max Audio Effect*
  template. The file is an `ampf` header, a `meta` chunk, and a `ptch` chunk holding the patcher JSON.
  The device uses only built-in Max objects, and the built `.amxd` is committed.
- Audio passes through unchanged (`plugin~` → `plugout~`). Control messages arrive on UDP 9879:

| Message | What the device does |
|---|---|
| `/reply <port>` | Sends replies to that port |
| `/open <path>` | Sets 32-bit float, then opens the file in `sfrecord~` |
| `/start`, `/stop` | Starts and stops recording; replies `/stopped` |
| `/meter 1\|0` | Starts/stops sending `/meter peakL peakR rmsL rmsR` about every 100 ms |
| `/ping` | Replies `/pong <sample rate>` |

- Installed into `User Library/Presets/Audio Effects/Max Audio Effect/`, and loaded via the browser.

## Request flow (example: `set_mixer("bass", volume_db=-6)`)

1. The client calls the tool. The MCP SDK validates the arguments against the Python signature and runs
   the synchronous tool in a worker thread.
2. `resolve.track_path` asks Live for track names (`lom_query`) and picks `song.tracks.1`.
3. The tool sends one `batch` with `param_set_display(target=-6)` and `undo_name`.
4. `LiveClient` writes a JSON line to port 9877. On Live's next timer tick (≤ 10 ms), `server.poll()`
   reads it, and `Dispatcher.execute` runs the handler on the main thread. The handler bisects the
   volume's raw value until Live displays "-6.0 dB", wrapped in one undo step.
5. The reply goes back as a JSON line. The tool reads the new state and returns a compact dict
   (`util.compact` drops empty values to save tokens).

## Cross-cutting mechanisms

- **Undo:**
  - Handlers wrap multi-command work in `song.begin_undo_step()`/`end_undo_step()`.
  - Live doesn't nest undo steps, so while the `batch` tool holds an `undo_group`, handlers skip
    their own wrappers (the `_GROUP` flag in `handlers.py`). The result is one undo per request.
- **Locators:**
  - `Song.set_or_delete_cue()` only toggles at the playhead, and a moved playhead applies a tick later.
  - `tools/arrangement.cue_ops` therefore moves the playhead, verifies it, checks
    `is_cue_point_selected()`, toggles and verifies, each as a separate call.
- **Playback position:** `start_playing()` starts at `song.start_time`. `playback(jump_to)` and captures
  set it (captures restore it afterwards).
- **Owned devices:**
  - Tools only modify devices they created and named (`MCP …`), found by name.
  - `mixing.find_or_create_device` inserts new devices before `AbletonMCP Analyzer` so the analyzer
    stays last.
- **Mastering loop:**
  - `plan_master` simulates the chain on the mix (`audio/master.py`) and saves `agent/reports/master_plan.json`.
  - `apply_master` writes the chain and `master_state.json` (with `applied_at`).
  - When an export or capture is newer than `applied_at`, `plan_master` measures it as a check and
    proposes corrections.
- **Portability:** `app.portable_schema` removes `anyOf`/`null`/`$ref`/`title`, and `PARAM_DOCS`
  documents shared parameters. `tests/test_client_compat.py` guards both.

## Testing

| Layer | How |
|---|---|
| Remote Script + tools | `tests/conftest.py` serves a fake `Song` (`tests/fake_live.py`) through the **real** server, dispatcher and handlers, with a thread standing in for Live's timer |
| MCP layer | `tests/test_workflow.py` calls tools through `mcp.call_tool`; `test_client_compat.py` runs raw JSON-RPC over stdio with three protocol versions |
| DSP | Synthetic signals with known answers (the −20 dBFS 997 Hz tone = −20 LUFS; inter-sample peaks); cross-checked against `pyloudnorm` |
| Analyzer | A fake UDP device that speaks the same OSC protocol; structural check of the generated `.amxd` |
| Real Live | `scripts/smoke.py`: 41 checks across all phases, on scratch tracks |
