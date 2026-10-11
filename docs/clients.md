# Using ableton-mcp from different AI clients

The server is a standard MCP server on stdio, so every client launches it with the same command:

```
uv --directory /path/to/ableton_live_mcp run ableton-mcp
```

| Client | Status | Where the config lives |
|---|---|---|
| OpenCode | ✅ Verified end to end with `openai/gpt-5.5` (read, create, mixer, delete) | `opencode.json` in this repo |
| Codex CLI | ✅ Verified (read, create, mixer); deletes always ask for approval | `~/.codex/config.toml` (see below) |
| Claude Code | Config in repo; protocol-tested | `.mcp.json` in this repo |
| Claude Desktop | Snippet below; protocol-tested | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| VS Code Copilot, Cursor, Gemini CLI | Snippets below; not installed here, untested | see below |

"Protocol-tested" means `tests/test_client_compat.py` passes: initialize with protocol versions 2024-11-05, 2025-03-26 and 2025-06-18, list tools, call tools.

## OpenCode
Already configured for this project (`opencode.json`). Elsewhere:
```bash
opencode mcp add ableton -- uv --directory /path/to/ableton_live_mcp run ableton-mcp
opencode reload
```
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

## Rules for new tools (enforced by `tests/test_client_compat.py`)
- **Register with `@tool()` from `app.py`.** It rewrites the schema to a portable subset: no `anyOf`/`null`/`$ref`/`title`, and `str | int` becomes `string`. Input is still validated against the Python signature, so integers still work.
- **Declare safety.** Use `@tool(read_only=True)` for reads and `@tool(destructive=True)` for anything that deletes or overwrites. Clients use these hints to decide when to ask.
- **Shared parameter names get shared docs.** `track`, `slot`, `scene`, `confirm`, `color`, … are documented once in `PARAM_DOCS`, because some clients never show the server instructions to the model.
- **Tool budget: 40 tools in total (35 used after Phase 11).** VS Code Copilot caps at 128 across all servers, and models pick tools less reliably as lists grow. Prefer one tool with a `target`/`action` parameter over several near-duplicates.
- **Descriptions ≤ 1024 chars, names match `^[a-zA-Z0-9_-]{1,64}$`** (OpenAI function rules).
- **Raise `LiveError` (or `ValueError`) with an actionable message.** Other exceptions reach the client only as "Error executing tool".
