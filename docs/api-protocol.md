# API protocol: how a new capability is specified

Every new capability, whether an option on a tool, an action or a tool, is planned the same way:

1. The agent researches what exists.
2. It asks the user the questions that the conventions below can't settle.
3. It writes a spec and gets the user's approval.
4. Only then does it build.

This file defines the conventions (section 1), the contract for each CRUD verb (section 2), the
spec (section 3) and the question protocol (section 4). [capabilities.md](capabilities.md) covers
when a capability is worth building and at which level. This file covers what its interface looks
like.

## 1. Conventions every operation follows

Don't ask the user about these. They apply unless the user explicitly asks for something else.

| Topic | Convention |
|---|---|
| **Addressing** | Objects by 0-based index or by name, through the `resolve` helpers: `track` (also `'Return A'`, `'master'`), `scene`, `slot`, `device` (nested as `'Rack > Chain > Device'`), `parameter`. Never a new address format. |
| **Parameter names** | Reuse existing names and their `PARAM_DOCS` descriptions (`track`, `slot`, `scene`, `device`, `confirm`, `color`, `position_beats`, …). Lower snake case. |
| **Types** | `str`, `int`, `float`, `bool`, `Literal[...]` enums, and lists or dicts of those. `str \| int` only for addresses. Optional means `= None`, and None means "leave unchanged". No nested models and no other unions: `portable_schema` can't express them for every client. |
| **Units** | Volume in dB, time in beats (quarter notes), bars 1-based in arrangement tools, device parameters in Live's display units (`"800 Hz"`), colors as hex or a name. |
| **Explicit end state** | For the agent layer, prefer parameters that state the result (`tracks=[…]` → exactly these are soloed) over flags whose effect depends on the current state (`keep_others`). Calling it twice gives the same result, and a test can compare the end state directly. |
| **Absolute vs relative** | When both make sense, two mutually exclusive parameters: `volume_db` / `change_db`. |
| **Safety** | Reads: `@tool(read_only=True)`. Anything that deletes or overwrites user material: `destructive=True` and `require_confirm(confirm, "what it would do")`. Protected objects (the master track, the AbletonMCP Analyzer) are refused. |
| **Undo** | One call is one undo step (`undo_group`, or `batch`). |
| **Errors** | `LiveError` or `ValueError` with a message that says how to recover. Nothing else reaches the client readably. |
| **Return** | A compact dict: the address of each object touched, plus what changed. Never a raw dump of the Live Object Model. |
| **Side effects** | The playhead, start marker and selection are restored if the operation moves them. Anything that plays audio says so in its description. |
| **Description** | At most 1024 characters. Says what it does, the units, what's refused, and the confirm rule. |

### Two layers: Live's behavior and the agent layer

AbletonMCP has two kinds of operation, and they're kept apart:

- **Live layer.** A parameter that sets one of Live's features behaves exactly as that feature
  does in Live, including the user's Live preferences. It never adds or changes behavior.
- **Agent layer.** Anything beyond what Live does (soloing several tracks at once, a recipe, an
  action) is an explicitly named parameter or action of our own. It's off by default, and its
  description says it's an AbletonMCP extension.
- **When the raw API differs from the feature in Live,** record the difference in
  `docs/api-notes.md`. The planning questions then settle which behavior the Live-layer parameter
  follows. Example: `Track.solo` set through the API is additive, while soloing in Live with
  Exclusive Solo on unsolos the other tracks.

So a capability never changes what an existing parameter does. New behavior gets a new parameter
or action.

### Hidden behavior: what Live does beyond the property

A feature in Live is often more than the property the API sets. Clicking a button can apply a
preference, change other objects, wait for quantization or change the selection. Setting the same
property through the API usually does only the bare change. A tool that wraps the bare property
therefore behaves differently from Live, without anyone noticing.

**Rule:** before specifying a capability, list its hidden behavior and test each item on scratch
tracks. Then decide, per item:
- **reproduce it in the Live layer**, because that's what the feature does in Live; or
- **leave it out and document it**, when Live's API can't reproduce it.

Behavior that Live doesn't have is never added to the Live layer; it goes to the agent layer.

**Where to look** (check every row for each capability):

| Source | Questions to ask | Known or likely cases |
|---|---|---|
| **Preferences** | Does a Live setting change what the action does? | Exclusive Solo (probed: the API ignores it), Exclusive Arm (`Song.exclusive_arm`), Select on Launch, launch quantization |
| **Changes to other objects** | Does the action change other tracks, clips or devices in Live? | Exclusive solo and arm; launching a clip stops the track's other clip; soloing a group track; `muted_via_solo` |
| **Hierarchy** | Do groups, racks or returns pass it on? | Group tracks fold, mute and solo with their children; rack chains; returns and the master |
| **Timing** | Does it wait for quantization, the next bar or the transport? | Clip and scene launch quantization; changes while playing vs. stopped |
| **Modifier variants** | Does Cmd/Ctrl, Shift or Alt change it in Live? | Cmd-click Solo or Arm adds to the current solos or arms |
| **Defaults** | Does Live fill in values the API leaves empty? | Names and colors of new tracks and clips; warp settings and fades on new audio clips; where a device is inserted |
| **Selection and view** | Does Live select, show or scroll to something? | Select on Launch; inserting a device selects it |
| **Undo** | Is it one undo step in Live, but several through the API? | Any action that changes several objects |
| **What the API stores** | Does Live store hidden state with it? | `.asd` files next to audio (see the known traps) |

**How to test:**
1. On scratch tracks, with playback stopped unless timing is the question:
   - do it through the API;
   - read every object it could have touched;
   - compare with what the same action does when clicked in Live.
2. Ask the user only for what can't be tested through the API, e.g. what the click does in their
   set.
3. Test with the relevant preference both on and off.

**Record:**
- each difference in `docs/api-notes.md` under "API vs. Live";
- the decision for each item in the spec ("Hidden behavior" field);
- a test for each reproduced behavior.

Existing Live-layer parameters were built before this rule and may wrap bare properties. Auditing
them is a P2 item in `TODO.md`.

## 2. CRUD contracts

| Verb | Annotation | Takes | Returns | Rules |
|---|---|---|---|---|
| **Create** | none (destructive if it can overwrite) | Parent address, `index` (-1 appends), optional initial fields | `{"created": path, "name": …}` | The new object exists afterwards; the result gives its address so the next call can use it. |
| **Read** | `read_only` | Address, optional `filter` / `include` | Stable keys, compact values, only what was asked for | Changes nothing, not even selection or playhead. |
| **Update** | none (destructive if it overwrites content) | Address plus only the fields to change, all optional | `{"<target>": path, "updated": [fields]}` | No fields → `"Nothing to change"`. Setting the current value is a successful no-op. |
| **Delete** | `destructive`, `confirm` | Address | `{"deleted": target, "name": …}` | Names what will be lost in the confirm message; refuses protected objects. |
| **Action** (not CRUD: transport, analysis, capture) | depends | Address plus options | What happened, plus measurements | States its side effects (audio, time taken). |

An update that also changes other objects (soloing one track unsolos the rest) is still an update.
Its result lists every object it touched.

## 3. The capability spec

The questions fill this in. While it's being planned, the approved spec goes into the capability's
`TODO.md` entry, then into the PR description. Once it's built, the docstring and the tests are its
home, so the spec isn't kept as a separate file.

```markdown
### Spec: <name>

- **Intent:** one sentence. **Users say:** "…", "…"
- **Surface / verb / level:** Operating / Update / L1 (see capabilities.md)
- **Lives in:** existing tool `<tool>` (new parameter / new action value) | action layer | new tool
- **Target:** object type, how it's addressed, one or several
- **Parameters:**
  | Name | Type | Required | Default | Units / range / values | Description |
  |---|---|---|---|---|---|
- **Rules between parameters:** mutually exclusive or dependent parameters
- **Hidden behavior:** for each source in section 1: what Live does, what the API does, and what
  this capability does (reproduce, or leave out and document)
- **Behavior:** what changes in Live; the Live API members used (checked in api-notes.md or with
  `live_api describe`); what happens when it's already in that state
- **Postcondition:** what's checked afterwards
- **Safety:** read-only / destructive; the confirm message; protected objects
- **Undo and side effects:** one undo step; playback, playhead, selection
- **Returns:** the keys and an example
- **Errors:** each case, with its recovery message
- **Tests:** offline cases (fake_live), smoke check
- **Docs to update:** INSTRUCTIONS, tool counts, api-notes, capabilities
```

## 4. Planning protocol

### Step 0: research before asking

Don't ask what the code or Live can answer.
- Check whether a tool already does it. If it's only hard to find, fix the description (L0) and
  stop.
- Find the Live API members with `live_api op='describe'` or `docs/api-notes.md`. If Live doesn't
  expose it, say so and stop.
- List the feature's hidden behavior (section 1, every row of the table) and test it on scratch
  tracks. Turn whatever the tests can't settle into questions for Round C.
- Fill in every field the conventions in section 1 settle, and note which fields are still open.

### Step 1: ask, in rounds

- Ask only about the open fields, at most 4 questions per round.
- Each question offers concrete options with the recommended one first, and the user can always
  answer in their own words.
- Use the client's structured-question UI if there is one; otherwise ask in plain text.
- Skip a round when there's nothing open in it.

| Round | Questions (ask only the open ones) |
|---|---|
| **A. Intent and scope** | What exactly should happen? Does it affect other objects too (other tracks, clips)? One target or several (a list, all matching a name)? Which object kinds: tracks only, returns, master? |
| **B. Parameters** | For each value the user provides: required or optional, type (on/off, number, text, a fixed set of choices, a list), default, unit and range. Absolute, relative or both? Which parameters exclude or depend on each other? |
| **C. Behavior and safety** | If it's already in that state: success or error? Does it delete or overwrite anything of the user's (→ confirm)? Should it be one undo step with the edits around it? Does it play audio or move the playhead? |
| **D. Placement and result** | Usually settled by the level rules and section 2. Ask only if it isn't: which existing tool it extends, and what the agent needs back. |

### Step 2: show the spec and get approval

- Show the filled spec.
- Point out every value that came from a convention, not from an answer, so the user can override
  it.
- Build only after an explicit yes.

### Step 3: build from the spec

1. Write the tests first, from the spec: each parameter, each rule between parameters, each error,
   the postcondition, and the already-in-that-state case.
2. Then the code, following AGENTS.md ("Adding or changing tools").
3. Update the docs the spec lists.

## Worked example: solo

**Step 0 research:**
- `set_track(track, solo: bool)` exists.
- `Song.exclusive_solo` exists, and it's on in the probed set.
- Probe: `Track.solo` set through the API is additive. Soloing A and then B leaves both soloed,
  while in Live, with Exclusive Solo on, B would replace A.
- The two-layer rule applies: the raw API and Live's feature differ.
- Hidden-behavior checklist (section 1):
  - Preferences: Exclusive Solo, which differs (probed).
  - Modifier variants: Cmd-click adds to the current solos, which the agent layer covers
    (question 2).
  - Hierarchy: group tracks and returns, not tested yet (a note on the bug).
  - Undo: unsoloing others must be in the same undo step.

**Questions asked:**
1. What should `set_track(solo=true)` do?
   - (a) Follow Live's Exclusive Solo preference (recommended).
   - (b) Stay additive, like the raw API.
2. How should several tracks be soloed at once?
   - (a) A `keep_others` flag on `set_track`.
   - (b) A list-based action.
   - The user's answer: (b), because "explicit and can easily be tested and debugged".

**Spec outcome:**
- **Live layer.** `set_track(solo=true)` follows `Song.exclusive_solo`:
  - with Exclusive Solo on, it unsolos the tracks Live would unsolo;
  - with it off, it's additive.

  Today's additive behavior is a bug (`TODO.md`, P2). The result lists any tracks it unsoloed.
- **Agent layer.** A `solo_tracks(tracks: list[str | int])` action:
  - afterwards exactly these tracks are soloed and every other track isn't, whatever was soloed
    before;
  - an empty list clears all solos;
  - one undo step;
  - postcondition: the set of soloed tracks equals `tracks`;
  - returns `{"soloed": [names], "unsoloed": [names]}`;
  - error: an unknown track name, before anything changes.

  It's an L3 action in the action layer (`TODO.md`, P3).

Two conventions decided this example:
- An earlier idea, `solo="only"`, would mix `bool` and `str` in one parameter, which section 1
  rules out.
- A `keep_others` flag would make the result depend on what was soloed before. The list states the
  end result, which section 1 prefers.
