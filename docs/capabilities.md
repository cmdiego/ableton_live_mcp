# Growing capabilities: from friction to deterministic actions

How to spot work an agent does the slow way, and turn it into something the server does in one
deterministic call. The method is agent-agnostic: everything it produces lives in the MCP server,
so every client gets it. Open candidates are tracked in [TODO.md](../TODO.md); this file is the
method.

## Why

Each agent step costs tokens, latency and a chance of a wrong call. A request like "solo the bass"
should cost one call, not a session overview, a name lookup and a call per soloed track. Fixed
sequences belong in code, where they are tested and verified. The agent's judgment is better spent
on music.

## 1. Signals: where friction shows up

| Signal | What it looks like | Strength |
|---|---|---|
| **Throwaway script** | The agent writes Python against `LiveClient` outside MCP | Strongest: the tools couldn't do it |
| **Raw `live_api`** | Any `live_api` call in a normal task | Strong: names a missing operation |
| **Repeated sequence** | The same tool sequence (same shape of arguments) across sessions | Strong when frequent |
| **Fan-out** | One user intent becomes many calls of one tool (unsolo 12 tracks) | Strong |
| **Error → retry** | A call fails, and the same or a similar call then succeeds | Medium: often a bug (P2) instead |
| **Known trap** | The task touches an API trap (locators, `start_time`, scales) | Medium, even when rare |
| **Reused `batch` payload** | The same batch built twice | Medium |

Signals come from two places:
- **The call log** (Telemetry surface, P1): tool, argument shape, result and duration per call,
  stored in `agent/` and git-ignored. Mining it finds repeated sequences and fan-out.
- **Agents themselves:** `AGENTS.md` asks every agent to report friction at the end of a task.
  Scripts and `live_api` calls never reach a log, so this matters most for them.

## 2. Qualify: is it worth automating?

Answer in order. A "no" at a ✋ question stops the candidate there.

1. ✋ **General?** Would it still matter in a different Live set? (The test from "Two kinds of
   TODOs".) If not, it's a song task.
2. ✋ **Deterministic?** Do the same inputs always mean the same Live changes? If it needs musical
   judgment (which EQ cut, which section), it's a workflow prompt (L4), not an action.
3. **Is it worth it?** Weigh how often it happens against the calls and tokens it saves. Encapsulate
   rare tasks anyway if they hit a known trap or are destructive.
4. **Can it be checked?** An action states a postcondition and verifies it ("only the bass is
   soloed"). If it can't be verified, say so in its description.
5. **What does it need?** Existing tools only (a recipe), server-side logic, or a new Remote Script
   command. Remote Script additions stay generic (see D3).

## 3. Pick the level: the cheapest that removes the friction

| Level | What it is | Costs | Example |
|---|---|---|---|
| **L0 Hint** | It already exists; fix discoverability in a tool description, `PARAM_DOCS` or `INSTRUCTIONS` | Nothing | The agent didn't know `set_mixer` takes `change_db` |
| **L1 Parameter** | An option or `action` value on an existing tool | Nothing | `gain_db` on `set_clip` for audio clips |
| **L2 Recipe** | A named, declarative sequence of existing tool calls, run by the action layer | Nothing per recipe | "Prepare to record": arm, monitoring In, metronome on |
| **L3 Coded action** | Python in `src/` that reads state, loops and verifies, run by the action layer | Nothing per action | `solo_tracks(tracks=[…])`, `set_part_ranges` |
| **L4 Workflow prompt** | Text that guides the agent through a task needing judgment | Nothing, but prompts don't reach every client | `mix_check` |
| **L5 New tool** | Only when the action layer can't express it | 1 of the 40 slots, plus a decision | — |

Every tool's schema sits in the model's context on every turn, so new tools cost tokens even when
unused. L1–L3 add capabilities without making the tool list longer.

## 4. The action layer (proposed)

One entry point runs named actions. Each action is a recipe (L2) or a coded action (L3).

- **Registry:** each action declares a name, intent phrases ("solo X", "listen to X alone"),
  parameters, its safety (read-only or destructive), steps or a function, and a postcondition.
- **Discovery:** the catalog is listed compactly in `INSTRUCTIONS` (name and one line each). It's
  also available through `action="list"` and as an `ableton://actions` resource, so a model can
  match a request to an action without many tool schemas.
- **Deterministic:**
  - arguments are validated like a tool call;
  - recipes run through the same machinery as `batch`, so an action is one undo step;
  - destructive actions still need `confirm=true`;
  - the postcondition is checked and reported.
- **Agent-agnostic:** it's server code, with no client-specific skills. Claude Code skills or
  slash commands can wrap actions, but never replace them.
- **Tested:** every action gets an offline test against `tests/fake_live.py`; the registry test
  fails if one is missing.
- **Measured:**
  - the call log counts uses;
  - an action is worth keeping if its intent now costs fewer calls than before;
  - unused actions are candidates for removal.

Open question: recipes as data files (JSON in `src/ableton_mcp/actions/`) or Python only.
User-defined recipes in `agent/` would be possible with data files. They can only call existing
tools, and confirmations still apply.

## 5. Lifecycle

1. **Spotted:** an agent or the call-log review writes a candidate into `TODO.md` under
   "Capability candidates" with its signal and an example.
2. **Qualified:** the section 2 questions are answered and a level is picked.
3. **Specified:** the interface is planned with [api-protocol.md](api-protocol.md): research,
   questions to the user, a spec, and the user's approval.
4. **Built:** from the spec, tests first, with a postcondition. `INSTRUCTIONS` and the docs are
   updated as for any tool change.
5. **Checked:** after real use, the call log shows whether the intent got cheaper. If it didn't,
   go one level up or remove the action.
