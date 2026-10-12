# Design decisions

Short records of choices that shape the code, with the evidence behind them. Several of them look
odd without context and are easy to "fix" back into bugs. Add new entries at the end. Dates are when
the decision was made.

## D1. An MCP server; the client is the agent (2026-10-09)

**Decision:** Build an MCP server rather than our own agent app. Claude, or any MCP client, does the
reasoning and the conversation.

**Why:**
- Most of the work is the Ableton bridge and the music/audio logic, not the chat loop.
- MCP keeps the user free to switch harnesses.

## D2. Python Remote Script for control; Max for Live only for audio (2026-10-09)

**Decision:** Control goes through a stdlib-only Remote Script. Max for Live is used only for the
analyzer (capture and meter).

**Why:**
- It needs no device in the user's sets.
- It works in every project.
- It reaches at least as much of the Live API as Max for Live.
- Max for Live is the only way to tap the master's audio.

## D3. A thin, generic Remote Script (2026-10-09)

**Decision:** Live gets generic `lom_*` path commands plus a few batch commands. Features are composed
in the server.

**Why:**
- Live only loads a changed Remote Script after a control-surface toggle or a restart.
- Keeping logic server-side means most changes never touch Live.
- `handlers.py` and `lom.py` also hot-reload.

## D4. Serve requests from a 10 ms main-thread timer, without threads (2026-10-10)

**Decision:** A non-blocking socket is polled from a `Live.Base.Timer` on the main thread.

**Evidence** (ping round trips measured in Live 12.4.6):

| Design | Round trip |
|---|---|
| Socket thread + `update_display` | ~300 ms |
| Socket thread + Timer | ~40 ms (GIL handoffs) |
| No threads, Timer poll | **~10 ms** |

Timer intervals below 10 ms didn't help.

## D5. `create_instance` reloads every module (2026-10-10)

**Decision:** The surface's `create_instance` reloads all of the script's modules.

**Why:** Live keeps imported modules across control-surface toggles. Without the reload, a toggle
rebuilt the surface from stale code, and only a Live restart loaded changes.

## D6. Work with any MCP client (2026-10-10)

**Decision:**
- portable schemas (no `anyOf`/`null`/`$ref`/`title`);
- shared parameter docs;
- safety annotations;
- `LiveError` subclasses `ToolError`;
- a 40-tool budget.

**Why:**
- MCP SDK 2.x hides the text of unexpected exceptions from clients.
- Gemini and OpenAI clients are strict about schemas.
- Codex decides when to ask for approval from `readOnlyHint`/`destructiveHint`.
- VS Code Copilot caps tools at 128 across all servers.

**Evidence:** verified end to end with OpenCode (GPT‑5.5) and Codex CLI.

## D7. Set parameters by their displayed value (2026-10-10)

**Decision:** `param_set_display` bisects a parameter's raw value inside Live until
`str_for_value` shows the target. For stepped displays it takes the closer bound.

**Why:**
- Live's volume taper and other curves aren't documented, so a hardcoded curve would drift.
- The same mechanism handles Hz, ms, %, pan L/R and named options.

**Evidence:** −6, −12.5 and 0 dB land exactly; pan was 25L→26L until the closer-bound rule.

## D8. Automation as breakpoints, inside clips (2026-10-11)

**Decision:** Write automation with `EnvelopeEvent(time, value)` breakpoints. Live interpolates
linearly in the parameter's raw 0..1 range. Arrangement automation goes into arrangement clips.

**Why:**
- Breakpoints give smooth ramps and LFOs instead of stair steps.
- Tracks expose no envelope API (only `automation_state`), so track-level arrangement automation can't
  be reached.
- Recording knob moves in real time was rejected as fragile.

## D9. Locator edits as separate, verified calls (2026-10-11)

**Decision:** `cue_ops` runs server-side: move the playhead, verify it, check
`is_cue_point_selected()`, toggle, verify.

**Evidence:** a new `current_song_time` applies a tick later. A same-tick toggle created a locator at
the old position, and in a test it **deleted** an existing locator. The unsafe in-Live version was
removed.

## D10. Arrangement edits addressed by (track, start time), one undo step (2026-10-11)

**Decision:** `arrangement_ops` finds clips by start time immediately before each operation.

**Why:**
- Clip indexes shift as clips are added or deleted.
- There's no insert/delete-time API, so moving material means duplicate + delete, working from the end.
- Clips can't be split through the API, so edits through a clip are refused rather than half-done.

## D11. Playback starts from `song.start_time` (2026-10-11)

**Decision:** `playback(jump_to)` and captures set `start_time`. Captures restore the user's marker.

**Evidence:** `start_playing()` ignored `current_song_time`, so captures "from bar 33" actually started
at bar 1.

## D12. One undo per request (2026-10-11)

**Decision:** The `batch` tool opens an `undo_group`. While it's open, handlers skip their own undo
wrappers.

**Evidence:** Live doesn't nest undo steps. An inner step split the group: one undo reverted pan and
tempo but not the volume. With the flag, one undo reverts volume, pan, tempo and a newly written clip.

## D13. Musical analysis heuristics (2026-10-10/11)

**Decision and why**, each found on real or generated material:
- **Bass follows chord roots** when reading chords from a clip. A pad's inversions aren't bass notes,
  and the bass had played C under every chord.
- **Slash chords only for a real bass note** (a fifth or more below the next note, or below C2).
- **Extended chords are recognized** (9/11/13). GPT‑written Am9–Fmaj9–G13–Em7 had been read as
  Cmaj7/B, Am7, …
- **Ties go to the common reading** (Am7 over C6) unless a bass decides.
- **Section boundaries:** a 3-bar majority filter ignores one-bar fills; a "build" must rise; a dip
  after a drop is a "breakdown".

## D14. Stem analysis heuristics (2026-10-11)

Measured on real Digitakt stems:
- **Masking is judged from the sparser stem's side:** of the kick's hits, how many collide with the
  bass. Normalizing by "either plays" rated a constantly playing bass vs kick at only 17%.
- **Roles from names are checked against the audio.** An "SD" stem was a sustained 41 Hz tone.
- **Percussive overlap above 2 kHz is texture,** reported once rather than as six cuts.
- **High-passes stay below the part's body,** found by walking down from its loudest band. The 5%
  energy rule had landed inside the rumble it should remove.
- **Silent stems are skipped.**

## D15. Exact loudness measurement; pyloudnorm only in tests (2026-10-11)

**Decision:**
- K-weighting with libebur128's analog-matched design (exact ITU coefficients at 48 kHz);
- true peak at 4× oversampling with a Kaiser β=8 filter;
- tonal comparisons centered on the median band difference.

**Evidence:** the reference tone measures −20.00 LUFS (pyloudnorm reads −20.04). The β=8 filter fixed
a +0.12 dB true-peak overshoot. Centering stopped a single boosted band from making every other band
look "less".

## D16. Mastering plans are simulated on the audio and calibrated against Live (2026-10-11)

**Decision:** Pick the Glue threshold and limiter gain by bisecting a simulation of EQ → Glue → Utility
→ True Peak Limiter on the actual mix. The Glue model detects the **peak of L+R** with a ~1.2 s release.

**Evidence:**

| Version | Error vs Live's real chain |
|---|---|
| RMS-detector Glue model | 3.5 LU |
| Recalibrated (from two Glue captures at −10/−20 dB) | ~0.5 LU |
| End to end, first try, no glue | −14.08 vs −14.0 and −9.08 vs −9.0 |
| End to end, with glue | −14.61 vs −14.0; the check round corrects the rest |

## D17. Mastering only touches its own devices (2026-10-11)

**Decision:** `MCP Master EQ`, `MCP Glue`, `MCP Utility` and `MCP Limiter` are found by name. The
Limiter runs in True Peak mode, and Glue's Release is set to its raw maximum.

**Why:**
- The user's own master devices are never changed.
- Glue's Release has no named options; its top position just displays "A" (auto).
- Exports or captures newer than `applied_at` are treated as checks.

## D18. Generated Max for Live analyzer (2026-10-11)

**Decision:** Build the `.amxd` from Ableton's template with a Python script, using only built-in Max
objects.

Details that matter:
- **32-bit float:** set before `open`, because `sfrecord~` fixes the format at open time (the first
  build recorded 16-bit).
- **`/reply <port>`:** several server processes can share the device.
- **No spaces in capture paths:** Max splits messages on spaces.

**Evidence:** a −20 dBFS tone captures at −20.00 LUFS; a +3 dBFS tone survives (+2.98); real music is
within 0.02 LU of the file.

## D19. Tool consolidation; no state cache (2026-10-11)

**Decision:** Merge near-duplicate tools (36 → 31 before Phases 10–11). Skip the planned
listener-based cache.

**Why:** 10 ms round trips make a full overview take about 6 ms, so a cache would add complexity for no
visible gain.

## D20. Dependencies and data (2026-10-11)

**Decision:**
- numpy/scipy/soundfile are core dependencies, so every client's `uv run ableton-mcp` works.
- `watchdog` was dropped; scanning the folder on request is enough.
- User audio lives in the repo's `agent/` folder, git-ignored except its layout and `presets/`.

## D21. No GUI (2026-10-11)

**Decision:** No GUI and no HTTP shim for now. If one is built, it will be Claude-only (Claude Agent
SDK).

**Why:** MCP clients are the interface for now. Asked at the Phase 9 checkpoint and reconfirmed after
Phase 11.

## D22. Pin the MCP SDK to 2.3.x; CI checks the Remote Script on 3.11 (2026-10-11)

**Decision:** `mcp>=2.3,<2.4` in `pyproject.toml`. GitHub Actions runs the offline suite on Python
3.12 and 3.13 (`uv sync --locked`), and separately compiles and imports the Remote Script on Python
3.11 with only the standard library.

**Why:**
- `app.tool()` rewrites registered schemas and `batch` calls tools through `MCPServer._tool_manager`,
  a private API that a minor release may change. Patch releases are fine; minor upgrades should be a
  deliberate change with the compatibility tests re-run.
- The Remote Script runs in Live's embedded Python 3.11. The test suite runs on 3.12+, so newer
  syntax or a third-party import would otherwise only fail inside Live.

## D23. Tool TODOs and song TODOs live apart (2026-10-11)

**Decision:** `TODO.md` at the repo root holds only tasks for AbletonMCP itself. Tasks for one Live
set go in `agent/projects/<set name>/TODO.md`, named after `live.song` from `get_session_overview`
and git-ignored with the rest of `agent/`. `workspace.py` creates `agent/projects/`.

**Why:** working on a song produces both kinds of task. Mixed together, the tracked tool backlog
fills up with one user's song notes (and their set and track names), and the song notes get lost
among tool work. The test: would the task still matter in a different Live set?

## D24. Four priority tiers, with P1 defined by surfaces (2026-10-12)

**Decision:** Every item in `TODO.md` sits in one of four tiers:
- **P1, core surfaces:** what an agent needs to operate Live at all. The surfaces are Control,
  Operating, Telemetry, Data and Security, plus Listening and Recovery. Each lists what exists and
  what's missing.
- **P2, defects and verification:** bugs, CI, tests, and missing operations found in use.
- **P3, architecture:** scaling, performance, security hardening and design decisions.
- **P4, prototypes and quality of life.**

A bug in a P1 surface gets P1 urgency, and so does anything touching audio playback and loopback,
the critical path (confirmed by the user on 2026-10-12). Items that mix a defect with a missing
capability are split.

**Why:** the backlog had been ordered by what the last session ran into. Defining the core surfaces
first shows which basic operations are still missing (for example, setting routing, moving
devices, reading CPU load, storing per-set data) before quality-of-life features are built on top.

**Evidence:** the surface gaps were checked against Live 12.4.6 (`docs/api-notes.md`, "Telemetry and
per-set data").

## D25. Friction becomes the cheapest deterministic level, not a new tool (2026-10-12)

**Decision:** New capabilities come from friction seen in real use: scripts, `live_api` calls,
fan-out, retries and repeated sequences. Each one is promoted to the cheapest level that removes it:
a hint, a parameter, a recipe, a coded action, a workflow prompt, or, as a last resort, a new tool.
Agents report friction (see `AGENTS.md`). The method is in `docs/capabilities.md`.

**Why:**
- Agent steps cost tokens and can go wrong, so fixed sequences belong in tested server code.
- Every tool schema costs context on every turn, and the budget is 40. Actions behind an existing
  entry point grow capabilities without growing the tool list.
- Server-side actions work in every MCP client, unlike Claude-only skills.

**Open:** the action layer's entry point and recipe format (see `TODO.md`, P3).

## D26. AGENTS.md is the source; CLAUDE.md imports it, and a test checks drift (2026-10-12)

**Decision:** Shared agent rules live only in `AGENTS.md`. `CLAUDE.md` starts with `@AGENTS.md`
(Claude Code inlines the file) and adds only Claude Code specifics. `tests/test_agent_docs.py`, run
in CI, fails when:
- the import is missing or isn't the first line;
- `CLAUDE.md` repeats a section heading or a sentence of 8 or more words from `AGENTS.md`;
- the prompt names in `CLAUDE.md` differ from the server's prompts;
- the tool budget line in `AGENTS.md` differs from the real count or from `TOOL_BUDGET`, which now
  lives in `app.py`;
- another client's instruction file exists without pointing to `AGENTS.md`.

**Why:** an import keeps the shared rules in sync by construction, so there's nothing to copy.
Drift can still come from a removed import, copied rules that later diverge, and counts and names
going stale. A test catches all of these on every push. A generator script or a pre-commit hook would
add a step that's easy to skip.

**Evidence:** removing the import, appending a rule from `AGENTS.md`, and dropping a prompt name from
`CLAUDE.md` each made exactly one of the new tests fail.

## D27. New capabilities are planned through a question protocol (2026-10-12)

**Decision:** Before building a tool, a parameter or an action, the agent follows
`docs/api-protocol.md`:
1. It researches what exists and what Live exposes.
2. It asks the user only the questions the conventions don't settle, in rounds (intent and scope,
   parameters, behavior and safety, placement).
3. It shows a spec and waits for approval.
4. It builds from the spec, tests first.

The conventions and the CRUD contracts are written down in the same file. The spec lives in the
`TODO.md` entry and the PR; after the build, the docstring and the tests are its home.

**Why:** capabilities that are built ad hoc drift in naming, types, return shapes and safety, and
a deterministic interface needs its decisions made up front. Writing the conventions down means the
user is only asked about real choices. Mixed types are an example: `solo="only"` was rejected
because a `bool | str` parameter can't be expressed for every client.

## D28. Per-set data: an ID in the set, JSON in `agent/` (2026-10-12)

**Decision:**
- A stable set ID is stored in the Live Set with `Song.set_data`.
- Everything else (snapshots, rules, preferences, provenance, descriptors) is versioned JSON under
  `agent/projects/<set id>/`.
- How agents reach this data (a new `project_data` tool or not) is still being investigated
  (`TODO.md`, P3).

**Why:**
- An ID inside the set survives renaming and moving the `.als`, which a folder keyed by the set name
  or path doesn't.
- JSON files stay readable and diffable, and the set file doesn't grow.

## D29. Live's behavior and the agent layer are kept apart (2026-10-12)

**Decision:**
- A parameter that sets one of Live's features behaves as the feature does in Live.
- Extra behavior (for example, soloing several tracks at once) is a separate, explicitly named
  parameter or action of AbletonMCP's own, off by default.
- A capability never changes what an existing parameter does.

The rule is in `docs/api-protocol.md`, section 1.

**Why:**
- The user should be able to predict a tool from knowing Live, and agents from knowing the tool.
- Mixing extensions into Live's own features hides which behavior comes from where.

For several targets, the agent layer prefers explicit, list-based actions that state the end result
(`solo_tracks(tracks=[…])`) over flags that depend on the current state. The user chose this for
being explicit, easy to test and debug, and easy to maintain.

**Evidence:** the raw API already differs from Live in places. `Track.solo` is additive through the
API but exclusive in Live with Exclusive Solo on (`docs/api-notes.md`). Such differences are
recorded and settled per feature.

## D30. Find and test hidden Live behavior before specifying a capability (2026-10-12)

**Decision:**
- Every capability's research step lists and tests its hidden behavior. The sources are
  preferences, changes to other objects, hierarchy, timing, modifier variants, defaults, selection,
  undo and stored state.
- Per item, the Live layer reproduces it, or leaves it out with a note in `docs/api-notes.md`
  ("API vs. Live").
- Existing parameters get the same audit (`TODO.md`, P2).

The checklist is in `docs/api-protocol.md`, section 1.

**Why:** Live's API sets bare properties, while doing the same thing in Live applies preferences
and side effects. A tool that wraps the property drifts from Live unnoticed, which breaks D29's
promise that the Live layer behaves like Live. Solo was the case that showed this; Exclusive Arm and
launch quantization are likely to follow the same pattern.
