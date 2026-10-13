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
| **AbletonMCP Remote Script** | `remote_script/AbletonMCP/`, runs inside Live | A thin, generic bridge to Live's Object Model (about 10 ms per call) |
| **MCP server** | `src/ableton_mcp/` | 35 tools, 5 workflow prompts and 4 resources, plus all the music theory, audio analysis and mastering logic |
| **AbletonMCP Analyzer** | `m4l/`, a Max for Live device on the master | Records Live's real output and provides a live meter, so the assistant can check its own work |

Your AI client starts the MCP server; the server talks to the Remote Script, which Live loads as a
control surface. Exports, reference tracks, captures and reports live in the [`agent/`](agent/README.md)
working folder.

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
- **One analyzer per set**, and it must stay last on the master, or captures miss the devices after it.
- **Heuristics are judgments, not facts:** chord names, section labels and stem roles are educated
  guesses; mastering without a reference only fixes obvious outliers; bar math assumes one time
  signature and a constant tempo.
- More Live API facts and quirks: [docs/api-notes.md](docs/api-notes.md).

## Getting started

**Requirements:** Ableton Live 12 (Suite recommended), [uv](https://docs.astral.sh/uv/) (it installs
Python 3.12+ for you), and an MCP client such as Claude Code or Claude Desktop.

**Let your agent do it:** open this folder in your coding agent and ask it to *"set up AbletonMCP by
following docs/setup.md"*. [docs/setup.md](docs/setup.md) is a checklist written for agents (and
people): it checks the prerequisites, installs everything, registers the server with your client and
verifies the connection. The same steps by hand:

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
4. Register the server with your AI client. Every client launches it with the same command, using the
   absolute path of your clone. For Claude Code:
   ```bash
   claude mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
   ```
   Claude Desktop, OpenCode, Codex, Copilot, Cursor and Gemini CLI: [docs/clients.md](docs/clients.md).
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
- **Tools:** 35 in total, grouped as set / compose / sound / mix / audio / arrangement. Your client lists
  them with their descriptions and parameters, and the server's instructions summarize them for the
  assistant.

## Troubleshooting

- Run `uv run python scripts/smoke.py` in a scratch set. It checks every part end to end and says which
  one fails.
- Live's log: `~/Library/Preferences/Ableton/Live <version>/Log.txt` (search for `AbletonMCP`).
- Your client still runs old code after an update: restart its MCP server (or the client).

## Contribute

This repository publishes tested releases; development happens elsewhere, so pull requests aren't
accepted here. Bug reports and feature ideas are very welcome as
[issues](https://github.com/cmdiego/ableton_live_mcp/issues), and good ideas are credited in the
[changelog](CHANGELOG.md).

### Reporting an issue

1. Search the [existing issues](https://github.com/cmdiego/ableton_live_mcp/issues) first; if yours is
   already there, add your details to it.
2. [Open a new issue](https://github.com/cmdiego/ableton_live_mcp/issues/new) with a short, specific title
   ("`capture` returns silence when the analyzer isn't last on the master") and include:
   - **What you asked or ran:** the prompt, or the tool call and its arguments if you have them;
   - **What happened vs. what you expected**, with the exact error message;
   - **Versions:** Live (e.g. 12.4.6, Suite or Standard), OS, MCP client, and this project's release
     (see [CHANGELOG.md](CHANGELOG.md));
   - **Logs:** the `AbletonMCP` lines from Live's `Log.txt`, and the output of
     `uv run python scripts/smoke.py` for setup problems;
   - **Steps to reproduce**, ideally on a small scratch set.
3. Don't attach your songs, stems or anything private; describe the set instead (track count, devices
   involved).

### Asking for a new feature

1. Check the open issues: it may already be requested. If so, add a 👍 and your use case.
2. Open an issue titled "Feature: …" that describes:
   - **The problem:** what you're trying to do in Live and what gets in the way today;
   - **An example request** you'd like to be able to make ("sidechain every bass to the kick");
   - **What would count as done**, and anything you tried as a workaround.
3. Describe the outcome rather than a specific tool. The server keeps its tool list small on purpose,
   so most features land as a new action or option on an existing tool.

## License

[Apache License 2.0](LICENSE). Paid features, if any are ever offered, will ship as a separate add-on;
nothing in this repo will move behind a paywall.

Ableton, Live and Max for Live are trademarks of Ableton AG. This project is not affiliated with or
endorsed by Ableton.
