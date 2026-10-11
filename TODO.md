# TODO

Open tasks and investigations for AbletonMCP itself. When one is done, record the outcome where it
belongs (docs, code, `docs/decisions.md`) and remove it from this list.

Tasks for a particular song or Live set don't belong here: they go in
`agent/projects/<set name>/TODO.md` (see "Two kinds of TODOs" in `AGENTS.md`).

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

## Feature: an "init" step for Live sets that haven't been set up for AbletonMCP

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
- **Where does "initialized" live?** Options: a marker in the set itself (e.g. the analyzer on the
  master counts as the marker), or a per-set file under `agent/` keyed by the set name. Live's API
  doesn't give the `.als` path, so a file keyed by name could collide.
- **How does it start?** MCP has no session-start hook. Options:
  - `get_session_overview` returns an `init` field listing what's missing, and `INSTRUCTIONS` tells
    the model to offer init when it's present (works in every client);
  - an `init_project` workflow prompt (only where clients support prompts; see the item above);
  - a new tool. Last resort: the tool budget is 35/40.
- What may init change without asking? Installing the analyzer only adds a device this project
  owns; unsoloing or renaming tracks changes the user's set and needs an OK.

**Related bug:** a first `analyzer install` in a set reported "on the master but doesn't answer",
and `status` right afterwards reported it responding. `install` already pings 10 times
(`tools/live_audio.py`), so the first Max for Live load can take longer than that. Wait longer, or
report "still loading, check `status` in a few seconds" instead of an error.

**Done when:** opening a fresh set and calling `get_session_overview` leads to an offer to init,
init does the steps above with one confirmation, and a second session on the same set doesn't offer
it again. Record the design choice in `docs/decisions.md`.

## Bug: adding a locator sometimes fails to move the playhead

`edit_arrangement add_locator` failed once with "Couldn't move the playhead to beat 64 (it's at 0)"
and the same call succeeded on retry. `cue_ops.move_to` in `tools/arrangement.py` reads
`current_song_time` back straight after setting it; Live applies the new position a tick later (see
`docs/api-notes.md`). Poll the position a few times before giving up.

**Done when:** adding eight locators in a row on a fresh set succeeds without retries.

## Bug: `get_arrangement` calls the same section a drop and an intro

With a 32-bar arrangement where every part plays throughout, the only section was labelled `drop`,
while the observations said "Intro is 32 bars (100% of the song)". The intro observation in
`theory/arrangement.py` looks at the first section without checking its label. Only report a long
intro when the first section is labelled `intro`, and say something else when there is only one
section ("the whole song is one section").

**Done when:** an offline test with a single unchanging section gives consistent labels and
observations.

## Bug: the overview's song length doesn't match the arrangement

`get_session_overview` reported `song_length_beats` ≈ 400.8 while the arrangement's clips ended at
beat 128 (`get_arrangement`: 32 bars). Live's `song_length` adds space after the last clip (and may
count the loop). Either label it for what it is or report the end of the last arrangement clip as
well (`arrangement_end_beats`), and say which one tools such as `get_arrangement` use.

## Feature: set where each part plays in the arrangement (`set_part_ranges`)

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

## Feature: `mix_check` without exported stems

`mix_check` starts from exported stems, but the source recordings of arrangement clips can be
measured with `analyze_audio(track=…, arrangement_clip=…)`. That was enough to find two basses and a
kick stacked at 50–100 Hz. When `agent/exports` has no stems, the prompt should fall back to this,
saying that clip gain, warping, effects and faders aren't included. Compare each part's 1/3-octave
peaks, flag overlaps in the low end, and offer an export or an analyzer capture to confirm.

## Feature: transcribe an audio clip (pitch, rhythm, chords)

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

## Bug: track routing to external MIDI ports shows as empty

`get_track_details` returned `""` for a MIDI track's output type and channel when it was routed
to an external hardware port. In Live, `output_routing_type.display_name` is empty for that type,
and the port isn't listed in `available_output_routing_types` (only tracks and "No Output"), so
the tools can neither show nor set routing to hardware. Duplicating an already-routed track was the
only way to copy it.
- Find out whether the port names are reachable another way (`category`, `attached_object`, the
  routing channel's `layout`, or `app` / control surface MIDI port info), or whether this only
  happens while the hardware is disconnected.
- At minimum, report "external MIDI port (name not available through Live's API)" instead of an
  empty string, and record the finding in `docs/api-notes.md`.
