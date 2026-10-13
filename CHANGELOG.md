# Changelog

Notable changes per release. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-12

Smoke-tested on Live 12.4.6 Suite (macOS): 41 of 41 end-to-end checks passed.

### Added
- `docs/setup.md`: a setup checklist you can hand to your AI agent ("set up AbletonMCP by following
  docs/setup.md"). It checks the prerequisites, installs everything, registers the server with your
  client and verifies the connection.

### Changed
- The repository now contains only what you need to install and run the tool: the MCP server, the
  Remote Script, the AbletonMCP Analyzer device, the setup and smoke-test scripts, and the user docs.
  Development files (tests, internal docs, agent instructions) are no longer published.
- The README now covers how the tool works, its limitations, getting started, usage, and how to
  report issues and ask for features.
- Contributions are issues only; pull requests aren't accepted any more (see `CONTRIBUTING.md`).
- Client setup: `docs/clients.md` now covers Claude Code with `claude mcp add`. The repo no longer
  ships Claude Code and OpenCode config files, which pointed at a path on the maintainer's machine.

## [0.1.0] - 2026-10-12

First public release. It needs Ableton Live 12 Suite and Python 3.12 or newer.
Smoke-tested on Live 12.4.6 Suite (macOS): 41 of 41 end-to-end checks passed.

### Added
- **MCP server** with 35 tools, 5 workflow prompts (`organize_session`, `write_part`, `mix_check`,
  `arrangement_review`, `master_track`) and 4 resources. It works with Claude Code, Claude Desktop,
  OpenCode, Codex, Copilot, Cursor and Gemini CLI.
- **Set and organization:** an overview of the set, names, colors, creating, duplicating and
  deleting, playback, and undo. Tracks can be addressed by name.
- **Composition:** chords, basslines that follow a chord clip, drums, arpeggios, free melodies,
  note transforms, and MIDI analysis (key, chords, rhythm, swing).
- **Sound design:** loading devices and presets, setting parameters by their displayed value, and
  automation.
- **Mixing:** an exact dB mixer, stem analysis (masking, resonances, stereo bass) and `apply_eq`.
- **Audio analysis:** BS.1770 loudness, true peak, tonal balance, stereo image and reference
  comparison.
- **Arrangement:** sections, an energy curve, locators, inserting, deleting and duplicating bars,
  and building an arrangement from session scenes.
- **Mastering:** `plan_master` and `apply_master` build and verify a chain toward a loudness target
  or a reference track.
- **Live audio:** the AbletonMCP Analyzer (Max for Live) records Live's real output and provides a
  live meter.
- Apache-2.0 license, with a CLA for contributions.
