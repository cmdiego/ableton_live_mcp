# Changelog

Notable changes per release. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). How releases are made:
[docs/releasing.md](docs/releasing.md).

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
