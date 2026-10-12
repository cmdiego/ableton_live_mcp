@AGENTS.md

## Claude Code specifics

- Register the server with `claude mcp add` (see docs/clients.md). After changing `src/`, reconnect
  the server (or start a new session) so it runs the new code.
- The workflow prompts appear as `/mcp__ableton__organize_session`, `…write_part`, `…mix_check`,
  `…arrangement_review` and `…master_track`.
- When planning a capability (docs/api-protocol.md), ask each round's questions with the
  AskUserQuestion tool, with the recommended option first.
- Personal memory lives outside the repo (`~/.claude/…`) and stays there. Project-level decisions go
  in `docs/decisions.md`.
- Tasks go in one of the two TODO files from AGENTS.md ("Two kinds of TODOs"), never in memory: tool
  tasks in `TODO.md`, tasks for the user's song in `agent/projects/<set name>/TODO.md`.
