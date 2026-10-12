# ableton_live_mcp

[![CI](https://github.com/cmdiego/ableton_live_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/cmdiego/ableton_live_mcp/actions/workflows/ci.yml)

An MCP server that lets an AI assistant work directly inside **Ableton Live 12**. Ask Claude (or any
MCP client: OpenCode, Codex, Copilot, Cursor, Gemini CLI) in plain language and it organizes your
session, writes parts, shapes sounds, diagnoses and fixes your mix, restructures the arrangement and
masters to a loudness target. It uses only the standard Live API and Max for Live: no plugins to buy,
no patched Live.

## How it works

```
AI client ──MCP stdio──▶ ableton-mcp server ──JSON lines, TCP 9877──▶ AbletonMCP Remote Script (in Live)
                                 │
                                 └──OSC/UDP 9879──▶ AbletonMCP Analyzer (Max for Live, on the master)
```

It has three pieces:

| Piece | Where | Role |
|---|---|---|
| **AbletonMCP Remote Script** | [`remote_script/AbletonMCP/`](remote_script/AbletonMCP/), runs inside Live | A thin, generic bridge to Live's Object Model (about 10 ms per call) |
| **MCP server** | [`src/ableton_mcp/`](src/ableton_mcp/) | 35 tools, 5 workflow prompts and 4 resources, plus all the music theory, audio analysis and mastering logic |
| **AbletonMCP Analyzer** | [`m4l/`](m4l/), a Max for Live device on the master | Records Live's real output and provides a live meter, so the assistant can check its own work |

Your AI client starts the MCP server; the server talks to the Remote Script, which Live loads as a
control surface. Exports, reference tracks, captures and reports live in the [`agent/`](agent/README.md)
working folder. More detail in [docs/architecture.md](docs/architecture.md).

## Limitations

- **Live 12 Suite is recommended.** Live 12 Standard works for most things, but Suite-only instruments,
  Max for Live and therefore the analyzer (captures, live meter) are unavailable.
- **macOS is the tested platform.** `scripts/install_remote_script.sh` targets the macOS User Library
  layout; on Windows, copy `remote_script/AbletonMCP` into your User Library's `Remote Scripts`
  folder by hand.
- **Live's API can't do everything:** no creating group tracks, no splitting clips (arrangement edits
  that would cut through a clip are refused), no access to track automation lanes, no rendering
  exports (captures are the substitute, and they run in real time), one instrument per track.
- **Don't touch Live while it works** on locators, captures or batches: moving the playhead or stopping
  playback mid-operation can hit the wrong locator or corrupt a capture.
- **Heuristics are judgments, not facts:** chord names, section labels and stem roles are educated
  guesses; mastering without a reference only fixes obvious outliers; bar math assumes one time
  signature and a constant tempo.
- The full list, with workarounds, is in [docs/summary.md](docs/summary.md#things-to-watch-out-for)
  and [docs/api-notes.md](docs/api-notes.md).

## Getting started

**Requirements:** Ableton Live 12 (Suite recommended), [uv](https://docs.astral.sh/uv/) (it installs
Python 3.12+ for you), and an MCP client such as Claude Code or Claude Desktop.

1. Clone the repo and install the dependencies:
   ```bash
   git clone https://github.com/cmdiego/ableton_live_mcp.git
   cd ableton_live_mcp
   uv sync
   ```
2. Install the Remote Script and copy the analyzer device into your User Library:
   ```bash
   scripts/install_remote_script.sh
   ```
3. Restart Live, then go to **Settings → Link, Tempo & MIDI → Control Surface** and choose
   **AbletonMCP** (Input/Output: None). Live's status bar should say it's listening on port 9877.
4. Connect your AI client. Every client launches the server with the same command:
   ```
   uv --directory /path/to/ableton_live_mcp run ableton-mcp
   ```
   Claude Code (`.mcp.json`) and OpenCode (`opencode.json`) pick up the config in this repo; set the
   path in it to your clone. For Codex, Claude Desktop, Copilot, Cursor and Gemini CLI, see
   [docs/clients.md](docs/clients.md).
5. Check it on a scratch set (it adds and removes a test track and briefly plays audio):
   ```bash
   uv run python scripts/smoke.py
   ```
6. Optional (Suite): ask the assistant to install the analyzer, which runs `analyzer(action='install')`.

## Usage

Just ask in plain language. The assistant picks the tools:

- "Rename and color my tracks by instrument."
- "Write a funky bassline for the chords in scene 2."
- "Why does my mix sound muddy?"
- "Add a breakdown before the second drop."
- "Master this for Spotify."

Useful to know:

- **Workflow prompts** guide multi-step jobs: `organize_session`, `write_part`, `mix_check`,
  `arrangement_review`, `master_track`. In Claude Code they appear as `/mcp__ableton__…` commands.
- **Resources** a client can attach as context: `ableton://session`, `ableton://arrangement`,
  `ableton://agent-files`, `ableton://api-notes`.
- **One undo per request:** multi-step changes go through the `batch` tool, so one Undo in Live (or the
  `undo` tool) reverts all of it. The assistant asks before deleting or overwriting anything.
- **Mixing and mastering from audio:** export your mix or stems into `agent/exports/` (and reference
  tracks into `agent/references/`). `plan_master` (target: streaming, apple, loud, club, a reference
  track, or custom) plans a chain and `apply_master` builds it on the master; re-run `plan_master` on a
  new export to check the result. It corrects until it's within ±0.5 LU and under the true-peak ceiling.
- **Live audio (Suite):** with the analyzer installed, `capture` records Live's real output and analyzes
  it like an export, and `meter` gives live peak/RMS. Mastering can then iterate on captures
  (`plan_master(path='latest_capture')`) without manual exports. Captures play through your speakers.
- **Everything else:** `live_api` gives raw access to the Live Object Model, without the other tools'
  safety checks.

The full capability list, measured accuracy and tips are in [docs/summary.md](docs/summary.md).

## Contribute

Bug reports, feature ideas and pull requests are all welcome.

### Reporting an issue

1. Search the [existing issues](https://github.com/cmdiego/ableton_live_mcp/issues) first; if yours is
   already there, add your details to it.
2. [Open a new issue](https://github.com/cmdiego/ableton_live_mcp/issues/new) with a short, specific title
   ("`capture` returns silence when the analyzer isn't last on the master") and include:
   - **What you asked or ran:** the prompt, or the tool call and its arguments if you have them;
   - **What happened vs. what you expected**, with the exact error message;
   - **Versions:** Live (e.g. 12.4.6, Suite or Standard), OS, MCP client, and this project's release
     (see [CHANGELOG.md](CHANGELOG.md)) or commit;
   - **Logs:** Live's `Log.txt` lines containing `AbletonMCP` (on macOS:
     `~/Library/Preferences/Ableton/Live <version>/Log.txt`), and the output of
     `uv run python scripts/smoke.py` if it fails;
   - **Steps to reproduce**, ideally on a small scratch set.
3. Don't attach your songs, stems or anything private; describe the set instead (track count, devices
   involved).

### Asking for a new feature

1. Check [TODO.md](TODO.md) and the open issues: it may already be planned.
2. Open an issue titled "Feature: …" that describes:
   - **The problem:** what you're trying to do in Live and what gets in the way today;
   - **An example request** you'd like to be able to make ("sidechain every bass to the kick");
   - **What would count as done**, and anything you tried as a workaround.
3. Describe the outcome rather than a specific tool. The server has a budget of 40 tools, so most
   features land as a new action or parameter on an existing tool; new capabilities are planned with
   [docs/api-protocol.md](docs/api-protocol.md) and [docs/capabilities.md](docs/capabilities.md).

### Pull requests

Open an issue before a large pull request so we can agree on the approach. Run `uv run pytest`
(no Live needed: a fake Live in `tests/fake_live.py` drives the real Remote Script code) and, if you
can, `scripts/smoke.py` against a real Live. Development rules for the Remote Script and for new tools
are in [AGENTS.md](AGENTS.md) and apply to people too; how pull requests land and the one-time CLA are
in [CONTRIBUTING.md](CONTRIBUTING.md).

## Docs

- [docs/summary.md](docs/summary.md): capabilities, limits and next steps
- [docs/architecture.md](docs/architecture.md): how it works
- [docs/decisions.md](docs/decisions.md): why it's built this way
- [docs/api-notes.md](docs/api-notes.md): Live API facts and quirks
- [docs/clients.md](docs/clients.md): setting up MCP clients
- [AGENTS.md](AGENTS.md): instructions for AI coding agents (and people) working on the repo
- [CHANGELOG.md](CHANGELOG.md): what changed in each release; `main` always holds the latest release

## License

[Apache License 2.0](LICENSE). Contributions need a one-time CLA; see [CONTRIBUTING.md](CONTRIBUTING.md).
Paid features, if any are ever offered, will ship as a separate add-on; nothing in this repo will move
behind a paywall ([D31](docs/decisions.md)).

Ableton, Live and Max for Live are trademarks of Ableton AG. This project is not affiliated with or
endorsed by Ableton.
