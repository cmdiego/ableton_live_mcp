# Setting up AbletonMCP

A checklist for an AI agent (or a person) setting up AbletonMCP on the user's machine. Work through
it in order, run each check, and stop to tell the user when a check fails. Steps marked **(user)**
happen in Live's interface: ask the user to do them and wait for their answer.

The commands below run from the root of this repository. Wherever a step says
`/path/to/ableton_live_mcp`, use the absolute path of this checkout (`pwd` prints it).

## 1. Check the prerequisites

- **Ableton Live 12** is installed. Suite is recommended: on Standard, Max for Live and therefore the
  analyzer (captures, live meter) aren't available. Ask the user which edition they have.
- **macOS** is the tested platform. On Windows, step 3 is a manual copy (see there).
- **uv** is installed:
  ```bash
  uv --version
  ```
  If it's missing, point the user to https://docs.astral.sh/uv/getting-started/installation/ and ask
  before installing it for them. uv installs a suitable Python (3.12+) on its own.
- The path of this checkout has **no spaces**. Captures are written to its `agent/` folder, and the
  analyzer breaks on paths with spaces; if there are any, ask the user to move the checkout first (or
  to set `ABLETON_MCP_AGENT_DIR` to a folder without spaces).

## 2. Install the dependencies

```bash
uv sync
```

Check that the server loads and lists its tools (it should print 35):

```bash
uv run python -c "import asyncio; from ableton_mcp.server import mcp; print(len(asyncio.run(mcp.list_tools())))"
```

## 3. Install the Remote Script and the analyzer device

```bash
scripts/install_remote_script.sh
```

It links `remote_script/AbletonMCP` into `~/Music/Ableton/User Library/Remote Scripts/` and copies
the analyzer device into the User Library. If Live later doesn't list AbletonMCP, run it again with
`--copy` to copy instead of link.

On Windows, copy `remote_script/AbletonMCP` into the `Remote Scripts` folder of the user's User
Library (Live shows its location under Settings → Library), and `m4l/AbletonMCP Analyzer.amxd` into
`Presets/Audio Effects/Max Audio Effect/` in the same User Library.

## 4. Select the control surface (user)

Ask the user to:
1. restart Live (it only scans Remote Scripts at startup);
2. open **Settings → Link, Tempo & MIDI**, and under **Control Surface** choose **AbletonMCP**, with
   Input and Output set to None.

Check: Live's status bar says AbletonMCP is listening on port 9877. If it doesn't, the `AbletonMCP`
lines in Live's log (`~/Library/Preferences/Ableton/Live <version>/Log.txt` on macOS) say why.

## 5. Register the server with the user's MCP client

Every client launches the server with the same command:

```
uv --directory /path/to/ableton_live_mcp run ableton-mcp
```

Register it under the name `ableton` in the client the user works with. For Claude Code:

```bash
claude mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
```

The config for Claude Desktop, OpenCode, Codex, VS Code Copilot, Cursor and Gemini CLI is in
[clients.md](clients.md). Use the client's own command or config file; don't write configs for
clients the user doesn't use. Most clients only load a new server after a restart or reload: tell the
user if theirs needs one.

## 6. Verify

1. **End to end.** Tell the user first: this adds and removes `__mcp_smoke…` test tracks and briefly
   plays audio through their speakers, so it should run on a new, empty set, not on a song.
   ```bash
   uv run python scripts/smoke.py
   ```
   It skips the phases whose preconditions aren't met (for example the analyzer) and names the part
   that fails.
2. **From the client.** After the client reloads, call `get_session_overview`. It should return the
   open set's tempo and tracks.

## 7. Optional: install the analyzer (Suite)

Ask the user first, then call the `analyzer` tool with `action='install'`. It puts the AbletonMCP
Analyzer on the master track, where it must stay last. It enables `capture` (recording Live's real
output) and `meter`.

## Done

Tell the user what was set up, which checks passed, and anything skipped. Then they can just ask in
plain language: "why does my mix sound muddy?", "write a bassline for scene 2", "master this for
Spotify". The README lists what the tool can do and its limits.
