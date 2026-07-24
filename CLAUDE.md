# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**The Last Lamplighter** — an Undertale-style, turn-based RPG whose NPCs are driven by an
LLM. The point of the project is the **framework**, not a finished game: a small world
(~4-5 characters, a handful of rooms) where NPCs with their own drives, memory, and
dispositions produce emergent, engine-checkable quests. Setting is **Emberhold**, a town
in permanent dusk; the goal is to reach the ridge and confront (fight *or* talk down) the
**Gloam**.

## Commands

```bash
.venv/bin/python main.py            # run; continues from most-recent save slot
.venv/bin/python main.py --fresh    # wipe ALL saves + runtime memory, start clean
```

- Deps live in the project `.venv/`; always invoke Python as `.venv/bin/python` (Python 3.13).
- Install: `.venv/bin/python -m pip install -r requirements.txt`.
- **There is no test suite.** Verify changes by running the game. For headless/automated
  checks, drive pygame with `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy` and dump a frame
  with `pygame.image.save(screen, path)`, then read the PNG. Prefer importing engine
  modules and asserting on `WorldState` for logic changes — the engine is UI-free and
  testable without a window.
- `settings.json` (gitignored) holds `{base_url, api_key, model}` for any OpenAI-compatible
  endpoint. **It contains a live `sk-` key — never commit, print, or echo it.** Before any
  commit, confirm `settings.json`, `save/`, `runtime_memory/`, and `.venv/` are unstaged.

## Wrap-up workflow (when finishing a piece of work)

- **Commit your work** (committing directly to `main` is how this project operates — the user
  has authorized it). Keep the pre-commit secret check above.
- **Clear the runtime state** so the next session/playthrough starts clean and no test-run
  memory lingers: empty `runtime_memory/` and `save/` (they're gitignored, so this is a local
  hygiene step, not a commit). Headless/live tests write real files into both — delete them
  when done (this form is zsh-safe on an already-empty dir; a bare `rm -f dir/*.json` aborts
  under zsh when the glob matches nothing):
  ```bash
  find runtime_memory save -maxdepth 1 -name '*.json' -delete
  ```

## The core invariant (read this before touching NPC behavior)

**The LLM never mutates game state directly.** Every NPC turn returns
`{"dialogue": ..., "actions": [...]}` as JSON in the message content (we do *not* rely on
the function-calling API — the endpoint is an arbitrary proxy). The engine then **validates
each action against real world entities and drops anything ungrounded**, so emergent
content can never corrupt the world or create uncompletable quests.

Three layers enforce this, and changes usually touch all three:
1. **`npc/actions.py`** — `ACTIONS` holds one prompt doc-block per action type; `ACTION_SETS`
   maps each character **kind** (`main` | `vendor` | `minor`, from the character JSON's
   `kind`) to the actions it may use, and `action_catalog(kind)` renders only those into the
   prompt. `apply_actions()` validates + applies each action **and drops any not allowed for
   that kind**, appending to `ActionResult.effects` (player-visible) or `.debug` (dropped).
   To add an NPC capability you add a doc-block in `ACTIONS`, list it in the relevant
   `ACTION_SETS`, and add a validated branch. (`join_combat` is a legacy alias of `join_party`.)
2. **`engine/quests.py`** — quests use a bounded schema (objective type from `OBJECTIVE_TYPES`,
   target must resolve via `KnownEntities`). `build_quest()` raises `QuestValidationError` for
   ungrounded targets; `refresh_and_complete(state, known)` recomputes progress from world
   state every turn, grants rewards, and opens **follow-ups**. `KnownEntities`
   (rooms/npcs/items/interactable_kinds) is the whitelist everything grounds against.
   **Quest trees:** a `Quest` has a `parent` and a list of `followups` — each a concrete
   `{"kind":"quest", ...}` (built and activated immediately on completion) or
   `{"kind":"decide_later"}` (a leaf; the default). A `decide_later` node on completion sets
   `flags["pending_continuation::<giver>"]`; the **commissioner** is simply the giver's own
   dialogue agent (`npc/agent.py` `_commission_block` + the per-turn nudge), which authors the
   next quest when the player next talks (or concludes the arc). The flag clears after that
   turn. Note `refresh_and_complete` needs `known` to build concrete children — pass it
   (call sites in `main.py`/`agent.act` do).
3. **`engine/items.py`** — the item catalog is a **closed set**. NPCs can only `offer_item`
   things actually in their own `inventory`; the LLM cannot invent items.

## Architecture

**`engine/` — the game, UI-free and the single source of truth.**
- `state.py` — `WorldState` (npcs, player, quests, flags, world_facts, events, ground_items,
  hearthlight, lamps, **`party`**). Dataclasses, serialized whole into save bundles.
  `adjust_affinity`, `add_fact`, `lit_lamp_count`, `active_quests`, `add_to_party`/
  `remove_from_party`/`in_party`, etc. live here. `party` is the ordered list of npc_ids
  travelling with you — the single source of truth for companions (party members auto-join
  every fight; the old `ally_pledged` flag is now just a legacy mirror).
- `world.py` — the map: `Room`/`Door`, `new_world()` builds rooms + `KnownEntities` +
  `NPC_SPAWNS`, `starter_quest()`, `ensure_world_complete()` (migrates older save bundles).
  Rooms carry a `biome` (`"town"` | `"snow"`); the ridge is `path → ridge_foot → ridge_pass
  → ridge_summit`. `GRID_W`/`GRID_H` define tile bounds.
- `combat.py` — turn-based logic: `Combatant`, `Combat`, `make_combat`, `enemies_from_ids`,
  the `ENEMIES` bestiary, `player_attack/defend/spare`, `enemy_attack`,
  `ally_attack/ally_defend/ally_spare/ally_step`, `combatant_from_npc`. Pure logic;
  `ui/combat.py` is the scene.
- `quests.py`, `items.py`, `trade.py` (coin economy at catalog value — buy/sell mechanical,
  gift/ask routed through the NPC AI), `journal.py` (`EventLog` on `world.events`; public
  events feed NPC briefings *and* the player journal), `save.py` (named slots under `save/`,
  each bundling the whole world **plus every NPC's memory + inventories**).

**`npc/` — the LLM-driven brain.**
- `agent.py` — a **LangGraph** `StateGraph`: `perceive` (build system/user prompt from the
  character file + affinity + memory + a grounded `_world_briefing`) → `reason` (call LLM,
  parse JSON) → `act` (validate/apply actions, refresh quests, write memory). Entry point
  `npc_respond()`. The `APPROACH` sentinel = "player walked up"; it branches on
  `memory.has_met()` (fixed the original "same greeting every visit" bug — the greeting
  prompt simply wasn't told to use the in-context memory).
- `combat_agent.py` — enemy turns and `mercy_attempt()` (free-text ACT). Persona enemies
  react in character and adjust `resolve`; hitting what they truly want makes them
  `spareable`. Non-persona creatures use rule-based fallbacks. **Companions** take
  LLM-driven turns via `ally_turn()` (attack / defend / speak / spare, the same menu the
  player has — a companion speaking to a foe can nudge its resolve), and `speak_to_ally()`
  handles talking to a companion mid-fight. Everything degrades to mechanical behavior on
  `LLMError` so a turn never stalls.
- `memory.py` — per-NPC append log (`{summary, entries}`) in `runtime_memory/`, write-through
  per turn; auto-compacts via the LLM past a threshold (`agent.summarize_memory`, run in the
  dialogue worker thread). `snapshot_all`/`restore_all` move it in/out of save slots.
- `characters/*.json` — personality, backstory, drives, secrets, affinity seed, inventory,
  coins, and (for combatants) a `combat` block. `roster.py` loads them.

**`llm/` —** `config.py` loads `settings.json`; `client.py` `complete_json()` calls the
endpoint and parses JSON **defensively out of message content** (strips code fences, finds
the JSON object), raising `LLMError` on failure.

**`ui/` — pygame rendering + scenes (reads state, doesn't own it).**
- `main.py` `Game` is the overworld loop and scene router (dialogue / combat / inventory /
  journal / menu). Long LLM turns run on a **worker thread** with a spinner; only the
  dialogue/combat box mutates state during a turn, the render loop only reads.
- `render.py` (`draw_overworld` with per-biome palettes, `wrap_text` — lives here to avoid a
  dialogue↔inventory import cycle), `dialogue.py`, `combat.py`, `inventory.py`
  (`InventoryPanel` + in-conversation `TradePanel`), `party.py` (`PartyPanel`), `journal.py`,
  `menu.py`, `theme.py` (`BIOMES`, fonts, colors), `sprites.py` (procedural low-res pixel art,
  nearest-scaled; no binary art committed).
- **Companions/party:** recruiting and parting are both **emergent** — a `main` NPC emits
  `join_party` (or `leave_party`) and `world.party` changes. `main.py` trails party members
  behind the player (`_place_followers` / `_gather_party`, a per-frame "snake" using
  `self._trail`; `occupied()` lets you pass companions so they never wall you in). You **talk
  to a companion from the party view** (`P` → Enter on `PartyPanel` opens a normal
  conversation) — overworld `E` deliberately skips party members (`adjacent_targets`) so a
  trailing follower doesn't perpetually snag it. A companion only leaves when you ask them to
  in that conversation and they choose `leave_party` (they may `move_to` off somewhere as they
  go). There is no menu "dismiss" button by design.

## Controls / key routing gotchas

- Overworld: Arrows/WASD move, **E** interact, **I** inventory, **P** party, **J** journal,
  **Esc** menu.
- **Trade inside a conversation opens with Ctrl/Cmd, not I** (`ui/dialogue.py: TRADE_KEYS`) —
  `I` collided with typing a message. Don't reintroduce letter keys as commands in the
  dialogue box.
- **Combat ACT is a free-text speech box**, not a menu — the player types what they *say* and
  it routes to `combat_agent.mercy_attempt` (or `speak_to_ally` when the target is a companion).
- **You talk to party members through the party view (`P` → Enter), not `E`.** `E` in the
  overworld skips companions on purpose.

## Conventions

- **Font tofu:** the bundled font lacks many glyphs. Use `•` and plain ASCII; do NOT use
  `◆ ✔ ↑ ↓ ▸` etc. — they render as boxes.
- Defeat is never a hard game-over — the player is knocked out and wakes safe.
- Prerequisites that must be reliable are enforced in the **engine**, not left to the LLM
  (e.g. lighting a lamp consumes an `oil_flask`; Wren deterministically grants flasks). Keep
  *flavor* with the LLM, *mechanics* in the engine.
- Ask the user before big direction changes (new combat model, swapping the LLM integration,
  major scope jumps).

## Part 2 — playable-game overhaul (in progress)

A staged roadmap turning the framework into a real game, tracked in
`~/.claude/plans/alright-to-quickly-catch-polymorphic-turing.md` and project memory
(`memory/part2-overhaul.md`):

- **Phase 1 (done)** — character **kinds** + action-set gating (see `npc/actions.py`).
- **Phase 2 (done)** — **party & recruitment** with real ally combat AI (see the
  Companions/party notes above).
- **Phase 3 (done)** — quest **trees** + "commissioner" continuations (see the quest-tree
  note under the core invariant). Default follow-up is *decide-later*; the starter lamp quest
  now chains into whatever Wren decides next.
- **Phase 4** — functional **places**: camp (rest heals + advances a `day` + restocks),
  storage, and a `vendor`-kind shop with per-day stock.
- **Phase 5 (last)** — rebuild the map into a **linear** A→B→…→Ridge progression + light
  ambient NPC wandering.

Confirm scope with the user before starting a new phase.
