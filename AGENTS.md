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

- Publish a tagged release to the public repo (dry run without `--publish`; process in
  [docs/releasing.md](docs/releasing.md)):
  ```bash
  scripts/release.sh vX.Y.Z
  ```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `pytest` on Python 3.12 and 3.13 with
`uv sync --locked`, and checks that the Remote Script compiles and imports on Python 3.11 with the
standard library only. After changing dependencies, run `uv lock` and commit `uv.lock`. `mcp` is pinned
to 2.3.x on purpose (see D22 in docs/decisions.md); upgrade it deliberately and re-run the
compatibility tests.

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

- Plan a new capability (a tool, a parameter or an action) with
  [docs/api-protocol.md](docs/api-protocol.md):
  1. research what exists;
  2. ask the user the open questions, in rounds;
  3. show the spec;
  4. build only after the user approves it.

  Don't skip the questions unless the user says so.
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

## Report friction

At the end of a task in a user's set, say whether it needed any of these:
- a throwaway script;
- raw `live_api` calls;
- many calls for one request;
- a retry after an error;
- a sequence you've run before.

If it did, add a candidate to "Capability candidates" in `TODO.md`: the signal, a one-line example
and a suggested level. [docs/capabilities.md](docs/capabilities.md) explains the levels; prefer
L0–L3 over a new tool.

## Two kinds of TODOs: the tool vs. the user's songs

Keep them apart. Before writing a task down, ask: would it still matter in a different Live set?

| | Tool TODOs | Song TODOs |
|---|---|---|
| About | AbletonMCP itself: bugs, features, skills/prompts, docs, investigations | One Live set: parts to record, mix moves to try, arrangement ideas |
| Where | `TODO.md` at the repo root | `agent/projects/<set name>/TODO.md` (`<set name>` = `live.song` from `get_session_overview`) |
| In git? | yes | no (`agent/` is ignored) |
| Example | "`analyzer install` reports no answer while Max is still loading" | "Sidechain the basses to the kick" |

- Never put a song's tasks, track names or set name in `TODO.md`. When a song task reveals a tool
  problem, write the tool problem in `TODO.md` in general terms and keep the song task in the song's file.
- At the start of work on a set, read its `agent/projects/<set name>/TODO.md` if there is one. At the
  end, update it: add what's still open, remove what's done.

## Two lanes: private development, public releases

This checkout's `origin` is the private dev repo (`cmdiego/ableton_live_mcp-dev`). The `public`
remote is the public repo, which gets one snapshot commit per release (D32).
- Branch from `dev` and open pull requests against `dev` in the private repo.
- Never push to the `public` remote. Only `scripts/release.sh` publishes, and only when the user asks
  for a release.
- Everything merged into `dev` becomes public at the next release, including `TODO.md` and the docs.
  Material that must stay private doesn't go in this repo at all.

## Repo hygiene

- Never commit `agent/exports`, `agent/captures`, `agent/reports` (user audio and analyses), transcripts
  or secrets. `.gitignore` covers `agent/`; only its layout and `agent/presets` are tracked.
- No machine-specific absolute paths in docs. Use `/path/to/ableton_live_mcp`.
- The repo is Apache-2.0 (D31). Don't copy code, presets or devices from other projects unless their
  license is Apache-compatible (MIT, BSD, Apache), and never from GPL or other copyleft projects. Say
  where it came from in the commit message. New runtime dependencies follow the same rule.
- One home per fact:
  - `README.md`: setup and overview
  - `docs/summary.md`: capabilities, limits, next steps
  - `docs/architecture.md`: how it works
  - `docs/decisions.md`: why
  - `docs/api-notes.md`: Live API facts
  - `docs/clients.md`: client setup and tool rules
  - `docs/capabilities.md`: how friction becomes new actions
  - `docs/api-protocol.md`: interface conventions and how a new capability is specified
  - `docs/releasing.md`: the two lanes and how a release is cut and published
  - `CHANGELOG.md`: what changed in each release
  - `TODO.md`: open tasks and investigations for the tool (song tasks: see above)
- Record new design decisions in `docs/decisions.md` (context, decision, evidence).
- Agent instruction files: `AGENTS.md` is the only home for shared rules. `CLAUDE.md` starts with
  `@AGENTS.md` and adds only Claude Code specifics. Another client's file (e.g. `GEMINI.md`) points
  to `AGENTS.md` instead of copying it. `tests/test_agent_docs.py` checks this, along with the
  prompt names and tool count both files state.
- No dated handoff files: open tasks live in `TODO.md` (tool) or `agent/projects/<set name>/TODO.md`
  (song); remove an item once its outcome is recorded.
