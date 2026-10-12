# TODO

Open tasks and investigations for AbletonMCP itself. When one is done, record the outcome where it
belongs (docs, code, `docs/decisions.md`) and remove it from this list.

Tasks for a particular song or Live set don't belong here: they go in
`agent/projects/<set name>/TODO.md` (see "Two kinds of TODOs" in `AGENTS.md`).

## How items are prioritized

Every item sits under one of four tiers (see D24 in `docs/decisions.md`). Work the highest tier
first.

| Tier | What belongs there |
|---|---|
| **P1: Core surfaces** | What an agent needs to operate Live at all: the gaps in the surfaces listed below |
| **P2: Defects and verification** | Bugs (anything that breaks existing behavior first), CI, the test pipeline, regression tests, a missing operation found in use |
| **P3: Architecture** | Long-run changes that take time to settle: scaling, performance, optimization, security hardening beyond the P1 baseline, design decisions |
| **P4: Prototypes and quality of life** | New features that make the tool nicer or faster to use |

Rules for placing an item:
- **Gap or missing operation?** If it's in a surface's "Missing" list below, it's P1. If it turns up
  in use and isn't listed, it's P2; if it's core, also add it to the surface's list.
- **A bug in a P1 surface** (it breaks something the surface already does) is handled with P1
  urgency, even though it's filed under P2.
- **Audio playback and loopback are the critical path.** Anything that touches them, including a
  bug, is handled with P1 urgency.
- **Split mixed items.** A broken display (P2) and the missing setter behind it (P1) are two items.
- **Inside a tier:** breaks existing behavior → unblocks other items → cheap → the rest.
- Every item says what "done" means.
- **Tool budget:** 35 of 40 tools are used. Surface gaps go into existing tools as actions or
  parameters where they fit; a new tool needs a decision in `docs/decisions.md`.

## P1: Core surfaces

Each surface lists what exists and what's missing. "Probed" means checked against Live 12.4.6
(details in `docs/api-notes.md`).

### 1. Control: values that change while the song plays

**Covers:** track, return and master volume, pan and sends; tempo, time signature, key and scale,
loop, metronome, groove; transport; every parameter of every device in a track's chain, racks
included; automation.

**Have:** `set_mixer` (dB, pan, sends; returns and master), `get_mix_state`, `set_song`, `playback`,
`get_device` / `set_device_params` (display units, racks with chains and macros), `automate`.

**Missing:**
- Track delay. Probed: neither `Track` nor `MixerDevice` exposes it. Confirm with `runtime_info`;
  if it's really out of reach, list it under "Known gaps" in `docs/api-notes.md`.
- Groove and swing amount (`Song.groove_amount`, `swing_amount`; probed: present).
- Crossfader, cue volume and crossfade assignment (lower value; only for DJ-style sets).

**Done when:** each item is settable through an existing tool or recorded as out of reach.

### 2. Operating: create, read, update and delete Live's objects

**Covers:** tracks, scenes, session and arrangement clips (MIDI and audio), devices in a chain,
routing, arming and monitoring.

**Have:** `create` (tracks, scenes), `duplicate` (tracks, scenes, clips), `delete` (tracks,
scenes, clips, devices); `set_track`
(name, color, mute, solo, arm, fold); `set_clip` (name, color, mute, loop); `set_scene`; MIDI notes
through `write_*`, `transform_notes` and `read_clip`; arrangement edits through `edit_arrangement`;
`load_device`.

**Missing:**
- **Routing:** set a track's input and output type and channel (`available_*_routing_types` lists
  31–48 choices on a probed track), and its monitoring state. Today routing is read-only. External
  MIDI ports are a separate P2 bug.
- **Devices:** move or reorder within a chain (`Song.move_device`) and duplicate
  (`Track.duplicate_device`). Both probed: present.
- **Audio clips:** create one from a file (`Track.create_audio_clip`, probed: present); set gain,
  pitch, warp mode and warping on `set_clip`.
- **Arrangement clips:** set the properties `set_clip` sets on session clips (name, color, mute,
  loop) and trim a clip to a range.

**Done when:** every item works through an existing tool (`set_track`, `set_clip`, `create`,
`duplicate`, `edit_arrangement`) with offline tests and a `smoke.py` check.

### 3. Telemetry: is the system healthy and fast

**Covers:** connection and versions, round-trip latency, Live's CPU load, per-track load, device and
chain latency, signal meters, and the MCP server's own call timings and errors.

**Probed and available:**
- Live's CPU: `app.average_process_usage`, `app.peak_process_usage`.
- Per-track load: `Track.performance_impact`.
- Device latency: `Device.latency_in_samples`, `latency_in_ms` (sum them per track for its chain).
- Meters: `Track.output_meter_level` / `_left` / `_right`, `input_meter_*`.
- Versions: the `ping` command (Live, Remote Script).
- Per-track memory: **not exposed** by the API. Out of scope.

**Have:** the analyzer's master meter; versions in `get_session_overview`. Nothing else is
reported.

**Missing:**
- A health report: ping round trip, CPU average and peak, the heaviest tracks by load, per-track
  chain latency, versions. Proposed home: `get_session_overview(include=["health"])`, not a new tool.
- Server-side timings: duration and error count per tool call, kept in memory and included in the
  health report.
- A call log for finding friction (`docs/capabilities.md`). Per call: tool, argument shape, result
  and duration, written as JSONL under `agent/` (git-ignored), plus a script that reports repeated
  sequences, fan-out, `live_api` use and error → retry pairs.

**Done when:** one call gives the health report, with offline tests against `tests/fake_live.py`
and a `smoke.py` check.

### 4. Data: the tool's own metadata

**Covers:**
- set identity;
- arrangement snapshots;
- per-set rules and descriptors (track roles, a do-not-touch list);
- user preferences;
- the samples the tool created or changed;
- the user's descriptions: a song's mood, the character wanted from a compressor.

**Probed and available:**
- `Song.file_path` gives the `.als` path.
- `Song.get_data` / `set_data` and `Track.get_data` / `set_data` store values that are saved inside
  the `.als`.

**Have:** the `agent/` folders, `agent/projects/<set name>/TODO.md` by convention, and
`agent/presets/`. There is no read/write access through tools.

**Missing:**
- **Storage** (decided, D28):
  - only a stable set ID goes into the set (`Song.set_data`), so it survives renaming and moving the
    `.als`;
  - everything else is versioned JSON under `agent/projects/<set id>/`, readable and diffable, and
    the set doesn't grow.
  - To build: create the ID, write the JSON, and move today's `agent/projects/<set name>/` folders
    over.
- **Arrangement snapshots:** compact enough to keep many per set. Per track: clip spans with a
  content hash, plus mixer state; for the song: locators, tempo and time signature. Uses: "what
  changed since…", diffs, and restoring beyond Live's undo history.
- **Rules and descriptors per set**, plus global user preferences (loudness target, genre, how much
  to confirm).
- **Sample provenance:** which files the tool wrote (captures, renders, transcriptions) and which
  tracks use them.
- **Descriptors to parameters:** store words like "warm, slow attack" with the parameter targets
  they led to, so they can be reused.
- **Access:** CRUD for all of the above. How agents reach it is a P3 investigation ("a tool for
  the Data surface").

**Done when:** a set's ID, a snapshot, rules and preferences can be created, read, updated and
deleted, with offline tests.

### 5. Security: the baseline needed to operate safely

**Covers:** who can reach Live, what needs confirmation, what files are touched, and what text the
model is asked to trust. Hardening beyond this baseline is P3.

**Have:**
- The Remote Script listens on 127.0.0.1 only.
- Destructive tools need `confirm=true`.
- Audio and reports are git-ignored, and dependencies are locked (`uv.lock`, `mcp` pinned).

**Missing:**
- **Threat model:** a short section in `docs/architecture.md`:
  - any local process can drive Live through port 9877 without a credential, including raw LOM
    calls (`lom_call`, `runtime_info`);
  - `live_api` bypasses confirmations;
  - check whether the analyzer's UDP port 9879 listens on all interfaces.
- **Prompt injection through data:** set, track and clip names, audio file metadata, and the per-set
  rules from the Data surface all reach the model. A shared `.als` can carry stored data. Mark
  stored rules with where they came from, and never treat them as instructions from the user.
- **File writes:** check that tools write only under `agent/`.

**Done when:** the threat model is written, the analyzer port is checked, and stored per-set data is
returned labeled as data.

### 6. Audio playback and loopback: hear and measure the result (critical path)

**Covers:**
- playback: transport, the start marker, launching clips and scenes;
- loopback: capturing Live's output through the analyzer;
- meters, loudness, spectrum and stereo analysis.

This is how the agent checks its own mix and mastering work. Any work touching it gets P1 urgency
and must pass the `smoke.py` phases that play audio; say so before running them.

**Have:** `playback`, `analyzer` (capture of the master, meter), `analyze_audio`, `analyze_stems`,
`plan_master`.

**Missing:** capture of a single track or return (only the master today); measurements of a
running set without exporting (see the P4 `mix_check` item).

**Done when:** a single track or return can be captured and analyzed like the master.

### 7. Recovery: undo, roll back, restore

**Covers:** getting back to a known state after the agent or the user makes a mistake.

**Have:** `undo` and redo, one undo step per `batch`; locator and capture operations restore the
playhead and the start marker.

**Missing:**
- An automatic snapshot (Data surface) before destructive edits, with a way to compare or restore
  from it.
- A note on what Live's undo doesn't cover (track automation lanes, files written to disk).

**Done when:** a destructive edit leaves a snapshot behind that can be diffed against the current
set, and the undo limits are documented.

## P2: Defects and verification

### Bug: `get_arrangement` calls the same section a drop and an intro

With a 32-bar arrangement where every part plays throughout, the only section was labelled `drop`,
while the observations said "Intro is 32 bars (100% of the song)". The intro observation in
`theory/arrangement.py` looks at the first section without checking its label. Only report a long
intro when the first section is labelled `intro`, and say something else when there is only one
section ("the whole song is one section").

**Done when:** an offline test with a single unchanging section gives consistent labels and
observations.

### Bug: the overview's song length doesn't match the arrangement

`get_session_overview` reported `song_length_beats` ≈ 400.8 while the arrangement's clips ended at
beat 128 (`get_arrangement`: 32 bars). Live's `song_length` adds space after the last clip (and may
count the loop). Either label it for what it is or report the end of the last arrangement clip as
well (`arrangement_end_beats`), and say which one tools such as `get_arrangement` use.

**Done when:** the overview reports the arrangement's end next to (or instead of) Live's
`song_length`, with a label that says what each one means.

### Bug: `set_track(solo=true)` doesn't follow Live's Exclusive Solo

Through the API, `Track.solo` is additive. With Live's Exclusive Solo preference on, soloing a track
in Live unsolos the others, but `set_track(solo=true)` leaves them soloed (`docs/api-notes.md`).
Following D29, the parameter should behave like Live:
- read `Song.exclusive_solo`;
- when it's on, unsolo the tracks Live would unsolo, in the same undo step;
- list them in the result.

First check in Live which tracks it unsolos: returns, and tracks inside groups. This is a Control
surface bug, so it gets P1 urgency.

**Done when:** offline tests cover Exclusive Solo on and off, and a `smoke.py` check on scratch
tracks matches what clicking Solo in Live does.

### Audit: hidden Live behavior in existing parameters

Existing Live-layer parameters set bare API properties, which can skip what the same action does in
Live. Check each against the "Hidden behavior" table in `docs/api-protocol.md`: test on scratch
tracks with the relevant preferences on and off, record differences in `docs/api-notes.md`, and
file a bug for each gap. Solo is already filed (above). Start with:
- `set_track`: arm (Exclusive Arm), mute and solo on group tracks, fold;
- `playback`: `fire_clip` and `fire_scene` (launch quantization, Select on Launch, scene tempo);
- `create`: the names, colors and position a new track gets in Live;
- `load_device`: where the device goes and what gets selected.

This touches the Control and Operating surfaces, so it gets P1 urgency.

**Done when:** every Live-layer parameter of `set_track`, `set_clip`, `set_scene`, `set_song`,
`set_mixer`, `playback`, `create` and `load_device` is checked, with differences recorded and a bug
filed for each gap.

### Bug: the first `analyzer install` in a set reports that the analyzer doesn't answer

A first install reported "on the master but doesn't answer", and `status` right afterwards reported
it responding. `install` pings 10 times (`tools/live_audio.py`), and the first Max for Live load can
take longer. Wait longer, or report "still loading, check `status` in a few seconds" instead of an
error.

**Done when:** a first install in a fresh set returns success or "still loading", never an error.

### Bug: track routing to external MIDI ports shows as empty

`get_track_details` returned `""` for a MIDI track's output type and channel when it was routed
to an external hardware port. In Live, `output_routing_type.display_name` is empty for that type,
and the port isn't listed in `available_output_routing_types` (only tracks and "No Output"), so
the tools can neither show nor set routing to hardware. Duplicating an already-routed track was the
only way to copy it. (Setting routing in general is a P1 Operating gap.)
- Find out whether the port names are reachable another way (`category`, `attached_object`, the
  routing channel's `layout`, or `app` / control surface MIDI port info), or whether this only
  happens while the hardware is disconnected.
- At minimum, report "external MIDI port (name not available through Live's API)" instead of an
  empty string, and record the finding in `docs/api-notes.md`.

**Done when:** a track routed to a hardware port reports something readable, and
`docs/api-notes.md` says what Live exposes.

## P3: Architecture

### Investigate: workflow prompts in MCP clients other than Claude Code

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

### Investigate: a tool for the Data surface (`project_data`)

The Data surface (P1) needs create, read, update and delete for set IDs, snapshots, rules,
preferences, sample provenance and descriptors. Options:
- **A new tool,** `project_data(action, kind, …)`: 36/40.
- **MCP resources for reads, the action layer for writes:** no new tool, but resources don't reach
  every client (see the prompts investigation).
- **Actions on existing tools,** e.g. snapshots on `get_arrangement`: no new tool, but the data is
  spread out.

Weigh it against the action layer's entry point (below). One new tool could serve both.

**Done when:** the choice is recorded in `docs/decisions.md`, with the client support it relies on.

### Design: the action layer for deterministic actions

`docs/capabilities.md` proposes one entry point that runs named actions: recipes over existing tools
(L2) and coded actions (L3). Each action has intent phrases, a postcondition and one undo step, and
the catalog is listed in `INSTRUCTIONS`. This keeps new capabilities from using up tool slots.

**Decide:**
- **Entry point:** a new tool (`run_action`, 36/40), or an `action` parameter on `batch`, which
  already validates and runs tool calls as one undo step.
- **Format:** recipes as data files, or Python only.
- **User recipes:** whether user-defined recipes in `agent/` are allowed.

**Done when:** the decision is in `docs/decisions.md`; the first three candidates from "Capability
candidates" run as actions with offline tests; and `INSTRUCTIONS` lists the catalog.

### Review: have `CLA.md` checked by someone with legal knowledge

`CLA.md` (D31) was written for this project, modeled on the Apache Individual CLA, and hasn't had a
legal review. Whether it really grants the right to relicense contributions under commercial terms
decides whether a paid add-on can include contributed code.
Do the review before merging the first outside pull request. Changing the CLA after people have
signed means a new version (`signatures/version2/…` in `.github/workflows/cla.yml`), and earlier
contributors would have to sign again.

**Done when:** a reviewer has approved `CLA.md` or its changes are merged, and D31 records who
reviewed it and when.

## P4: Prototypes and quality of life

### Feature: an "init" step for Live sets that haven't been set up for AbletonMCP

**Context**
- In a new set, the first session spends several calls on setup by hand. In one session
  (2026-10-11) that meant: installing the AbletonMCP Analyzer last on the master, then spotting a
  soloed track, an armed track, duplicate track names (which make name-based addressing ambiguous)
  and empty return tracks.
- Nothing records that a set has been set up, so every client and session starts from scratch.

**What init should do** (report first, change only after the user's OK)
1. Check the connection and versions (`get_session_overview`).
2. Install the AbletonMCP Analyzer last on the master (`analyzer action=install`), or report that
   it's already there.
3. Health check: soloed or armed tracks, duplicate track names, empty returns and tracks, the key
   left at the default, an arrangement loop that's switched on.
4. Make sure the `agent/` folders exist (`exports`, `references`, `captures`, `reports`, `projects`).
5. Record that the set is initialized, so later sessions skip init.

**Open questions**
- **Where does "initialized" live?** Settled by the Data surface (P1): a set ID stored with
  `Song.set_data`, or a file keyed by `Song.file_path`. (Earlier notes said the `.als` path isn't
  available; it is, see `docs/api-notes.md`.)
- **How does it start?** MCP has no session-start hook. Options:
  - `get_session_overview` returns an `init` field listing what's missing, and `INSTRUCTIONS` tells
    the model to offer init when it's present (works in every client);
  - an `init_project` workflow prompt (only where clients support prompts; see the P3 investigation);
  - a new tool. Last resort: the tool budget is 35/40.
- What may init change without asking? Installing the analyzer only adds a device this project
  owns; unsoloing or renaming tracks changes the user's set and needs an OK.

**Depends on:** the Data surface (where "initialized" is stored) and the prompts investigation
(how init is offered).

**Done when:** opening a fresh set and calling `get_session_overview` leads to an offer to init,
init does the steps above with one confirmation, and a second session on the same set doesn't offer
it again. Record the design choice in `docs/decisions.md`.

### Feature: set where each part plays in the arrangement (`set_part_ranges`)

Building a song skeleton from a recorded loop (repeat it to song length, then take parts out per
section) needed a throwaway script of about 240 raw `arrangement_ops`, because clips can't be split
through the API.

- **Finding:** `duplicate_clip_to_arrangement` onto a spot covered by an existing clip makes Live
  trim that clip there; details in `docs/api-notes.md` ("Trimming by overwrite").
- **Proposal:** a `set_part_ranges` action on `edit_arrangement` (no new tool): `track` plus
  `ranges` as 1-based bar ranges. It rebuilds that track's arrangement clips from its current
  material (looped clips restart at each block, recordings repeat their length) and trims at the
  range ends, as one undo step. A companion `arrange_from_loop` workflow prompt proposes a section
  table, waits for the user's OK, then calls it for each track and adds locators.
- **Watch out for:** 4-bar loops vs. 32-bar recordings on different tracks, short "seam" clips at a
  recording's start, and clips that end a little short of the bar (step by whole bars, overwrite
  instead of abutting).

**Done when:** an offline test against `tests/fake_live.py` covers trimming, gaps and blocks of
different lengths, and the skeleton build is one `batch` of `set_part_ranges` calls.

### Feature: `mix_check` without exported stems

`mix_check` starts from exported stems, but the source recordings of arrangement clips can be
measured with `analyze_audio(track=…, arrangement_clip=…)`. That was enough to find two basses and a
kick stacked at 50–100 Hz. When `agent/exports` has no stems, the prompt should fall back to this,
saying that clip gain, warping, effects and faders aren't included. Compare each part's 1/3-octave
peaks, flag overlaps in the low end, and offer an export or an analyzer capture to confirm.

**Done when:** `mix_check` on a set with no stems in `agent/exports` gives per-part low-end
overlaps from the clips' source files and says what that leaves out.

### Feature: transcribe an audio clip (pitch, rhythm, chords)

Writing a part that fits a recorded hardware loop needed a throwaway script, because the tools can
read the notes of MIDI clips but not of audio clips. What worked, on the clip's source file over
its loop region (beats → seconds through the clip's warp markers):
- **Rhythm:** a low-passed RMS envelope per 16th plus 5 ms onsets gives a step grid (onset /
  sustain / rest) that matched the part.
- **Pitch:** autocorrelation per note was unreliable on a detuned, modulated analog bass (it gave
  sidebands about 3 Hz apart). A long FFT over each sustained note, read together with the chord
  roots, was usable.
- **Chords:** chroma per bar (FFT 16384, 90 Hz–2 kHz, top pitch classes) clearly gave the chords.

**Proposal:** a `transcribe` detail (or `include`) option on `analyze_audio` for `track` + `slot` /
`arrangement_clip` (no new tool). It returns step grids per bar, note estimates with a confidence
value, chords per bar and a likely key, in the same shape `read_clip` uses for MIDI, so
`write_notes` and `write_bassline` can build on it. Code in `audio/` (pure DSP) with offline tests
on synthetic tones, chords and a detuned saw.

**Done when:** `analyze_audio` with the transcribe option returns a `read_clip`-shaped result,
and offline tests on synthetic material get the rhythm, notes and chords right.

## Capability candidates

Friction found in real use, not yet qualified (method: `docs/capabilities.md`). Each candidate
lists its signal, an example and a suggested level. Once it's qualified, move it into a tier above.

- **Soloing several tracks** (signal: fan-out). Specified (`docs/api-protocol.md`, worked example):
  a `solo_tracks(tracks=[…])` action, L3. After it runs, exactly these tracks are soloed. Waits for
  the action layer (P3).
- **Building a song skeleton from a loop** (signal: a throwaway script of about 240 raw ops).
  Already the P4 `set_part_ranges` item. Suggested level: L3.
- **Transcribing an audio clip** (signal: a throwaway DSP script). Already the P4 transcribe
  item. Suggested level: L3, as an option on `analyze_audio`.

