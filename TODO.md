# TODO

Open tasks and investigations. When one is done, record the outcome where it belongs (docs, code,
`docs/decisions.md`) and remove it from this list.

## Investigate: workflow prompts in MCP clients other than Claude Code

**Context**
- The server defines five MCP prompts in `src/ableton_mcp/tools/workflow.py`:

  | Prompt | Arguments |
  |---|---|
  | `organize_session` | none |
  | `write_part` | `part`, `style`, `bars` |
  | `mix_check` | `stems_folder`, `mix` |
  | `arrangement_review` | none |
  | `master_track` | `target`, `reference` |

- Claude Code shows them as slash commands: `/mcp__ableton__<name>`.
- It hasn't been checked how, or whether, other clients expose MCP prompts. The tools themselves are
  verified in OpenCode (GPT‑5.5) and Codex CLI.

**Questions, per client** (OpenCode, Codex CLI, Claude Desktop, VS Code Copilot, Cursor, Gemini CLI):
1. Does it list MCP prompts (`prompts/list`) at all?
2. How do you invoke one: a slash command, a menu, an attachment, or not at all?
3. Are prompt arguments supported? How are they entered, and are the defaults applied?
4. Does it also expose MCP resources (`ableton://session`, `ableton://arrangement`,
   `ableton://agent-files`, `ableton://api-notes`)?

**How to test**
- Connect each client using `docs/clients.md`, with Live running and the AbletonMCP control surface on.
- Try `write_part` with arguments (e.g. part=`bassline`, style=`funk`, bars=`4`) and check that the
  expanded text reaches the model. `arrangement_review` is a good read-mostly test.
- Use a scratch set; the prompts lead to edits and may ask to play audio.

**Outcome**
- Add a per-client table (prompts and resources: supported, how to invoke, arguments) to
  `docs/clients.md`.
- Update the "Workflow prompts" lines in `README.md` and `docs/summary.md`, which currently only
  describe Claude Code.
- If important clients can't use prompts, decide on a fallback and record it in `docs/decisions.md`.
  Options:
  - the README tells users to ask in plain language ("review my arrangement") instead;
  - mention the workflows in the server `INSTRUCTIONS` so the model follows them unprompted;
  - expose the workflow text through a tool. That costs one of the remaining 5 tool slots, so it's
    the last resort.

**Done when:** `docs/clients.md` has a verified prompts/resources row for each client tested, and
the docs no longer imply Claude Code's slash commands are the only way in.
