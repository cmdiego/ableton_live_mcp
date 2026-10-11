# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, OpenCode, Cursor, …) working on this repo.
An MCP server for Ableton Live 12 Suite: a Live Remote Script (`remote_script/AbletonMCP/`), the MCP
server (`src/ableton_mcp/`) and a Max for Live analyzer (`m4l/`). Start with
[docs/architecture.md](docs/architecture.md); [docs/decisions.md](docs/decisions.md) explains choices
that look odd but are deliberate.

## Commands

- Setup:
  ```bash
  uv sync
  ```
- Offline tests (no Live needed; a fake Live in `tests/fake_live.py` drives the real Remote Script code):
  ```bash
  uv run pytest
  ```
- End-to-end checks against a running Live Suite with the AbletonMCP control surface selected. They
  create and delete `__mcp_smoke…` tracks, briefly play audio, and skip phases whose preconditions
  aren't met:
  ```bash
  uv run python scripts/smoke.py
  ```
- Rebuild the Max for Live device after editing `m4l/build_analyzer.py` (commit the `.amxd` too):
  ```bash
  uv run python m4l/build_analyzer.py
  ```
- Install the Remote Script symlink and the device into the User Library:
  ```bash
  scripts/install_remote_script.sh
  ```

## Rules for the Remote Script (`remote_script/AbletonMCP/`)

- Live's embedded **Python 3.11, standard library only**. Never block Live's main thread or add threads.
- Keep it thin and generic. New features belong in `src/ableton_mcp/`, composed from existing commands.
- Reloading:
  - `handlers.py` and `lom.py` hot-reload with the `reload` command:
    ```bash
    uv run python -c "from ableton_mcp.live_client import LiveClient; print(LiveClient().call('reload'))"
    ```
  - `surface.py`, `server.py` and `dispatcher.py` need the control surface switched to None and back.
  - `__init__.py` needs a Live restart.
  - Bump `VERSION` in `surface.py` when the surface changes.
- The MCP server process your client started keeps running old code: restart the client's MCP server
  after changing `src/`.

## Adding or changing tools

- Register with `@tool()` from `src/ableton_mcp/app.py`, never `mcp.tool()` directly. It rewrites
  schemas to a portable subset and turns `ValueError` into readable errors.
- Declare safety: `@tool(read_only=True)` for reads, `@tool(destructive=True)` for anything that
  deletes or overwrites. Destructive actions call `require_confirm(confirm, …)`.
- Raise `LiveError` (or `ValueError`) with a message that says how to recover. Anything else reaches
  clients only as "Error executing tool".
- **Tool budget: 40 total, 35 used.** Prefer an `action`/`target` parameter on an existing tool over
  a new tool.
- `tests/test_client_compat.py` enforces these rules: no `anyOf`/`$ref`/`title`, typed params,
  descriptions ≤ 1024 chars, annotations, budget.
- When the tool set changes, update `INSTRUCTIONS` in `app.py`, and the counts in `README.md`,
  `docs/clients.md` and `docs/summary.md`.

## Working against the user's Live set

- Use scratch tracks named `__…` and delete them in `finally`. Ask before deleting or overwriting
  anything of the user's.
- Only modify devices this project owns: `MCP EQ`, `MCP Master EQ`, `MCP Glue`, `MCP Utility`,
  `MCP Limiter`. The `AbletonMCP Analyzer` stays **last** on the master.
- Captures and some smoke checks play audio through the user's speakers; say so first.
- Known API traps (details in [docs/api-notes.md](docs/api-notes.md)):
  - locators only via `cue_ops` (separate verified calls);
  - playback starts at `song.start_time`, not the playhead;
  - unknown scale names silently become Major;
  - a recording's `.asd` file changes how Live plays it.

## Repo hygiene

- Never commit `agent/exports`, `agent/captures`, `agent/reports` (user audio and analyses), transcripts
  or secrets. `.gitignore` covers `agent/`; only its layout and `agent/presets` are tracked.
- No machine-specific absolute paths in docs. Use `/path/to/ableton_live_mcp`.
- One home per fact:
  - `README.md`: setup and overview
  - `docs/summary.md`: capabilities, limits, next steps
  - `docs/architecture.md`: how it works
  - `docs/decisions.md`: why
  - `docs/api-notes.md`: Live API facts
  - `docs/clients.md`: client setup and tool rules
- Record new design decisions in `docs/decisions.md` (context, decision, evidence).
- No dated handoff files: keep "next steps" current in `docs/summary.md`.
