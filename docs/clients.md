# Using ableton-mcp from different AI clients

The server is a standard MCP server on stdio, so every client launches it with the same command:

```
uv --directory /path/to/ableton_live_mcp run ableton-mcp
```

| Client | Status | Where the config lives |
|---|---|---|
| OpenCode | ✅ Verified end to end with `openai/gpt-5.5` (read, create, mixer, delete) | `opencode mcp add` (see below) |
| Codex CLI | ✅ Verified (read, create, mixer); deletes always ask for approval | `~/.codex/config.toml` (see below) |
| Claude Code | Protocol-tested | `claude mcp add` (see below) |
| Claude Desktop | Snippet below; protocol-tested | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| VS Code Copilot, Cursor, Gemini CLI | Snippets below; not installed here, untested | see below |

"Protocol-tested" means the automated compatibility tests pass: initialize with protocol versions 2024-11-05, 2025-03-26 and 2025-06-18, list tools, call tools.

## Claude Code
```bash
claude mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
```
The workflow prompts appear as `/mcp__ableton__organize_session`, `…write_part`, `…mix_check`,
`…arrangement_review` and `…master_track`.

## OpenCode
```bash
opencode mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
```
Then run `opencode reload`.
OpenCode calls MCP tools from its own code-execution tool. That works fine.

## Codex CLI
```bash
codex mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
```
Approvals follow the tool annotations:
- Read-only tools (`get_*`, `read_clip`, `list_audio`, `analyze_*`) run without asking.
- Edits ask, unless you add `default_tools_approval_mode = "auto"` under `[mcp_servers.ableton]`.
- Destructive tools (`delete`, `duplicate`, `live_api`) always ask, even with `auto`.

`codex exec` can't show prompts, so it cancels anything that needs approval.

## Claude Desktop
```json
{"mcpServers": {"ableton": {"command": "uv", "args": ["--directory", "/path/to/ableton_live_mcp", "run", "ableton-mcp"]}}}
```

## VS Code Copilot (`.vscode/mcp.json`)
```json
{"servers": {"ableton": {"type": "stdio", "command": "uv", "args": ["--directory", "/path/to/ableton_live_mcp", "run", "ableton-mcp"]}}}
```

## Cursor (`.cursor/mcp.json`) and Gemini CLI (`~/.gemini/settings.json`)
```json
{"mcpServers": {"ableton": {"command": "uv", "args": ["--directory", "/path/to/ableton_live_mcp", "run", "ableton-mcp"]}}}
```
