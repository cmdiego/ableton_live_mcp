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
