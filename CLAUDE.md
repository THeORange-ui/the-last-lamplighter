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
.venv/bin/python main.py --log-llm  # also write every prompt + reply to logs/
```

- Deps live in the project `.venv/`; always invoke Python as `.venv/bin/python` (Python 3.13).
- Install: `.venv/bin/python -m pip install -r requirements.txt`.
- **There is no test suite.** Verify changes by running the game. For headless/automated
  checks, drive pygame with `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy` and dump a frame
  with `pygame.image.save(screen, path)`, then read the PNG. Prefer importing engine
  modules and asserting on `WorldState` for logic changes — the engine is UI-free and
  testable without a window.
- **Anything about pacing must be checked by actually PLAYING, not by driving the one
  system under test.** Testing the night by resting repeatedly with neglect flags forced
  produced a cadence nothing like a real session's, and pronounced it healthy while the
  real game was dominated by one character. Short and thorough beats long and skippy: a
  dozen real turns tell you more than fifty rests. Use the harness:
  ```bash
  .venv/bin/python tools/playsession.py --turns 26 --seed 7
  ```
  It walks the door graph, holds real conversations, follows `cartography.waypoints`,
  picks things up, and rests only when the plate is clear — then reports **quests per
  giver, agenda beats closed per character, and how many nights held anything**, which is
  where lopsidedness shows up. It costs real LLM calls (roughly one per conversation).
  **It redirects `save/` and `runtime_memory/` into a temp sandbox**, because it builds a
  `Game(fresh=True)` and that wipes every slot — an earlier un-sandboxed version of this
  script deleted a playtest save. Anything else that constructs a `Game` in a script must
  do the same.
- `settings.json` (gitignored) holds `{base_url, api_key, model}` for any OpenAI-compatible
  endpoint. **The local copy holds a live `sk-` key — never commit, print, or echo it.** Before any
  commit, confirm `settings.json`, `save/`, `runtime_memory/`, `logs/` and `.venv/` are unstaged.
- **`--log-llm` is the main debugging tool for behaviour** (`llm/log.py`). The prompts *are*
  the game, so being able to read exactly what a character was told and what came back beats
  guessing. It writes `logs/<timestamp>/`: one markdown file per group with the full system
  prompt, user prompt, raw reply, parsed JSON, latency and token counts, plus an
  `index.jsonl` for tooling. Every `complete_json` call passes a `log_group=`; dialogue
  groups get a per-conversation number (`begin_conversation()` from `ui/dialogue.py`), so one
  exchange is one file. Off by default — a session is hundreds of KB. `logs/` is gitignored
  and anything matching an `sk-` key is redacted on the way in, since proxy errors can quote
  the request back at you.

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
   `minor` characters get **`request_help`** instead of `give_quest` — same grounding, then
   railed by `build_simple_quest` so a throwaway NPC can ask a favour but never steer the
   main line.
   The `main` set also includes **`set_goal`/`resolve_goal`** (the agenda — see
   `npc/agenda.py`) and `tell` — one NPC passes word to others, writing a line into each
   target's memory via `NPCMemory.remember_for` (a live-instance registry keeps that write on
   the same object the game holds), so NPCs stay informed without the player re-explaining.
   Every kind has `use_item` (out of combat): an NPC uses something in **its own** inventory —
   reading writes the `read_text` into that NPC's memory (so hand them a map/book and they
   actually learn it), food is consumed. It deliberately does **not** call
   `engine.items.use_item`, which applies *player* effects (healing the player, `map_read`).
2. **`engine/quests.py`** — **every quest goes on the plate through `add_quest()`**, which
   stamps `opened_seq` from the conversation counter and *refuses one that is already
   satisfied*. `talk_to` means "speak to them **since I asked**", measured against that
   stamp — it used to read the permanent `talked_to` flag, so an ask to go and see someone
   the player had already met completed in the same frame it was created, and two nights of
   substantive asks looked to the player like nothing had happened at all.
   Quests use a bounded schema (objective type from `OBJECTIVE_TYPES`,
   target must resolve via `KnownEntities`). **`judged`** is the exception: its target is a
   plain-English criterion, `evaluate_progress` never satisfies it, and only the giver closes
   it via the `complete_quest` action — for things like "put my mind at rest about Ansel" that
   no counter can decide. `build_quest()` raises `QuestValidationError` for
   ungrounded targets; `refresh_and_complete(state, known)` recomputes progress from world
   state every turn, grants rewards, and opens **follow-ups**. `KnownEntities`
   (rooms/npcs/items/interactable_kinds) is the whitelist everything grounds against —
   `interactable_kinds` is now **derived from the map**, so new content widens what quests may
   legally target for free.
   **Quest trees:** a `Quest` has a `parent` and a list of `followups` — each a concrete
   `{"kind":"quest", ...}` (built and activated immediately on completion) or
   `{"kind":"decide_later"}` (a leaf; the default). A `decide_later` node on completion drops
   a **visible "Check back with &lt;giver&gt;" breadcrumb quest** (`make_check_back_quest`,
   objective type `CHECK_BACK` — engine-created, never LLM-proposed, never auto-completed by
   progress). When the player talks to that giver, the **commissioner** (the giver's own
   dialogue agent — `_commission_block` + the per-turn nudge, both keyed off
   `find_check_back`) picks one of **three** — ask for the next step, *leave it there for now*,
   or close the arc — and `agent.act` marks the breadcrumb complete. The middle option exists
   because a two-way choice made every completed quest spawn another, and one thread ate the
   game; how hard the prompt leans on it comes from `pacing.restraint()`.
   A breadcrumb with **`parent is None`** is a heartbeat (`engine/pacing.py`), not a follow-up,
   and gets its own framing. `refresh_and_complete` needs `known` to build concrete children —
   pass it (call sites in `main.py`/`agent.act` do).
3. **`engine/items.py`** — the item catalog is a **closed set**. NPCs can only `offer_item`
   things actually in their own `inventory`; the LLM cannot invent items.

## Architecture

**`engine/` — the game, UI-free and the single source of truth.**
- `state.py` — `WorldState` (npcs, player, quests, flags, world_facts, events, ground_items,
  hearthlight, lamps, `interact_state`, **`party`**, **`day`**, **`storage`**). Dataclasses,
  serialized whole into save bundles. `adjust_affinity`, `add_fact`, `lit_lamp_count`,
  `active_quests`, `add_to_party`/`remove_from_party`/`in_party`, `heal_player`, etc. live here.
  `party` is the ordered list of npc_ids travelling with you (party members auto-join every
  fight; the old `ally_pledged` flag is now just a legacy mirror). `day` advances when you rest
  at the camp; `storage` is the camp chest's contents. `NPCRuntime` also carries **`agenda`**
  (see `npc/agenda.py`) and `flags["seen"]` (once-only experiences, see `witness.py`).
- `interact.py` — **one system for everything usable**: lamps, camp fixtures, puzzles. An
  `Interactable` is a static definition on a `Room` (`id`/`kind`/`pos`/`name`/`desc`/`hint`,
  plus `requires`, `effects`, `blocks`, `once`, `hidden`, `witness_msg`); only mutable state
  persists, in `WorldState.interact_state`. `requires` takes `{"item": id}` (spent unless
  `consumes=False`), `{"flag": name}` — **the shape a knowledge-lock takes**, where a character
  tells you something, the engine sets a flag, and the door opens — and lamp-only
  `{"unlit": True}`. `effects` are single-key dicts (`light_lamp`, `heal_full`, `advance_day`,
  `open_panel`, `set_flag`, `add_fact`, `give_item`). `apply_interaction()` is the single entry
  point `main.use_interactable` calls. Adding world content is **data, not code**.
- `pacing.py` — **how fast the world hands you things, and to whom.** In play one character's
  line ran away with the whole game, because starting anything only ever happened in
  conversation and the player was always in a conversation with her. Two mechanisms pull
  against that:
  - the **tick** — a composite progress counter, not elapsed time, so pacing tracks what the
    player *does*. Progress is **weighted** (`WEIGHTS`: quest 3, rest 2, room 1) — counting a
    newly-seen room the same as a finished quest let bare exploration of a 20-room map drive
    the entire system.
  - the **heartbeat** — every `MIN_GAP` (6) points, if fewer than `THREAD_CAP` (4) threads are
    open, the world drops a "Check on X" breadcrumb for a *neglected* character. It's a
    `CHECK_BACK` quest with **no parent**, which is how `agent._commission_block` tells it from
    a follow-up. Creating one costs **no LLM call**; the call happens if the player goes and
    knocks. Candidates are ordered strangers-first and least-nudged-first, skipping anyone the
    player is *already* being pointed at and anyone standing in a room the player has never
    visited (word doesn't reach you from a corner you've never walked).
  - **A note is not work.** `open_threads()` (the load, and what `restraint()` grades) counts
    only real quests; `open_notes()` counts `CHECK_BACK` breadcrumbs, and at most
    `MAX_OPEN_NOTES` (1) may be outstanding. Counting notes as load fed back on itself: the
    world dropped a note, the note read as load, the load told everyone to hold off asking, so
    the only things open were notes — and clearing one freed the slot for the next. For the
    same reason `main.on_quests_completed` does **not** bump the tick for a `CHECK_BACK`.
  - `restraint()` grades load + arc-parity into `hold`/`easy`/`free`, which `_commission_block`
    leans on. **Calibrate in both directions** — blanket restraint just reinstates the
    stalled-arc bug.
  - **Never tell a character what else the player is carrying.** `prompt_block()` used to list
    the player's open quests by title so the NPC could weigh their load; they weighed it out
    loud ("though you've already got Wren's lamps to mind"), which is both an immersion break
    and knowledge that isn't theirs. It now speaks only about the character's own position, and
    says outright not to mention other people's errands. Restraint still applies — the engine
    keeps the arithmetic to itself and the character feels it as their own reticence.
- `witness.py` — `record_experience(...)` logs an event **and** writes a first-person memory to
  everyone who was present, so a character can tell "I was there" from "I heard about it".
  Salience tiers `AMBIENT/NOTE/BEAT/MAJOR` gate whether anything is remembered at all;
  `once_key` dedupes per character (entering a room writes one memory, not forty); `targets=`
  overrides the witness set (arrival is something the *party* did, not the residents who were
  already standing there); `bond_items=` **pins** the memory for anyone bonded to that object.
- `world.py` — the map: `Room`/`Door`, `street_lamp()`, `new_world()` builds rooms +
  `KnownEntities` + `NPC_SPAWNS`, `starter_quest()`, `ensure_world_complete()` (migrates older
  save bundles — seeds/prunes lamps, back-fills agendas, relocates anyone stranded by a map
  change). The map is a **linear main line** `square → tavern → market → camp → road →
  ridge_foot → ridge_pass → ridge_summit` with two optional forks (`home`/Perrin off the road,
  `cellar` off the tavern). **The camp sits early on purpose** — every night of the game is
  spent there, and a camp you have to hike back to from the ridge is a camp nobody uses, so
  the world would stop taking its turn. Two things this map arrangement is load-bearing for,
  and which any future room shuffle must preserve: the **three ridge-gating lamps**
  (`lamp_square`/`lamp_tavern`/`lamp_market`, all that `world.lamps` contains) must stay in
  rooms reachable *before* the gate, or a prerequisite ends up behind its own door; and the
  Ridge Shelf's one-way scree drop lands on `road`, the ground directly below the climb.
  Rooms carry a `biome` (`"town"` | `"snow"` | `"camp"`), a list of
  `interactables`, and an LLM-facing **`desc`/`features`** — only the room a character is
  standing in gets described in their briefing, so the prompt doesn't balloon as the map grows.
  `GRID_W`/`GRID_H` define tile bounds.
- `combat.py` — turn-based logic: `Combatant`, `Combat`, `make_combat`, `enemies_from_ids`,
  the `ENEMIES` bestiary, `player_attack/defend/spare`, `enemy_attack`,
  `ally_attack/ally_defend/ally_spare/ally_step`, `combatant_from_npc`. Pure logic;
  `ui/combat.py` is the scene. **Enemies always answer an action with a blow**
  (Undertale-style): `enemy_attack` scales damage by `resolve_scale()` (full force at resolve
  100 down to `RESOLVE_FLOOR`) and takes `restrained=` for a held-back hit
  (`RESTRAINED_MULT`). An ACT reply speaks *and* strikes, so `mercy_attempt` sets
  `Combatant.acted` and `ui/combat.py` skips that enemy's regular turn (clearing the flags
  after the round) — otherwise it would hit twice. Only a **spareable** enemy stops attacking.
- `quests.py`, `items.py`, `trade.py` (coin economy — `buy_from_npc`/`sell_to_npc` at catalog
  value for AI gift/ask; **`shop_buy`/`shop_sell` add a margin** (`SHOP_MARKUP`/
  `SHOP_SELL_FACTOR`) for `vendor` NPCs, whose stock `restock_vendor(state, id, day)` refills
  once per day from `VENDOR_STOCK` + a day-rotated special + a coin float), `journal.py`
  (`EventLog` on `world.events`; public events feed NPC briefings *and* the player journal),
  `save.py` (named slots under `save/`, each bundling the whole world **plus every NPC's
  memory + inventories**).

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
- **First meetings must start mid-something.** Every character file carries an `opening`:
  what they are in the middle of when you first walk up, injected into the first-meeting
  prompt with an instruction to lead with it. A character who only introduces themselves
  gives the player nothing to take hold of — which is exactly why, before this, every story
  ran through the one character who asked for something on turn one.
- **An agenda beat must be a pursuit, not a standing state.** "Keep the tavern warm" / "turn a
  profit" / "be left alone" can never finish, so those characters idled forever while Wren
  (whose first beat was a real task) carried the whole plot. Standing motivations belong in
  `drives`; `agenda` holds only things that can be *done*.
- `agenda.py` — **what a character is trying to do next**, the thing the prompt used to lack.
  Each `main` character's file carries an ordered `agenda` (an arc skeleton), one beat open at a
  time, rendered by `prompt_block()` into `# What you are trying to do right now`. The NPC
  reports `goal_progress` each turn: `"advanced"` clears a stale counter, `"none"` increments it,
  and at `MAX_STALE` the prompt tells them to press it outright — so a goal can stall but never
  *silently*. `"resolved"` (or the `resolve_goal` action) closes the beat and `advance_agenda`
  seeds the next authored one, so arcs progress and **end**; when the authored beats run out the
  character names its own via `set_goal`. `MIN_TURNS` stops a beat resolving on the turn it
  opened, which would let a whole arc evaporate in a few greetings.
- `bonds.py` — what a character *cares* about, as data: `{kind: item|npc|room|topic, ref,
  weight 1-3, note}`. `relevance()` scores how much a beat concerns someone (plus a boost for
  being named); `notes_here()` feeds their own words about what's present into their briefing.
  **This is why Ansel's staff matters without a line of staff-specific code** — it's an object
  with weight, and weight is data.
- `interject.py` — companions speaking up unprompted, in **two stages so silence is free**:
  `choose_interjector()` is a pure rule check (bond relevance ≥ `MIN_RELEVANCE`, off
  `BEAT_COOLDOWN`, hasn't already remarked on this `once_key`) and makes **no LLM call**;
  only if it returns someone does `interject()` spend one short call for a single line, with
  no actions — a bark can't change the world, so it needs no validation. `main.beat()` raises
  beats on room entry, interactable use and pickups, runs the call on a worker thread, and
  `draw_bark()` shows it above the HUD in the speaker's colour. Measured: ~1 interjection per
  30 room entries.
- `memory.py` — per-NPC memory (`{summary, pinned, entries, seeded}`) in `runtime_memory/`,
  write-through per turn; auto-compacts via the LLM past a threshold (`agent.summarize_memory`,
  run in the dialogue worker thread). **`pinned`** holds what the character carries around
  rather than what happened with the player — `seed_memories` from their file, plus anything
  `pin()`ed as arc-critical — and compaction never touches it. Pinned lines deliberately do
  **not** count toward `has_met()`, or a character full of their own worries would greet a
  stranger like an old friend. `snapshot_all`/`restore_all` move it in/out of save slots.
- `characters/*.json` — personality, `background`, `voice` (sample lines: few-shot beats
  adjectives), `relationships` (what they think of each other — the fuel for NPC-to-NPC drama),
  `bonds`, `agenda`, `seed_memories`, backstory, drives, secrets, affinity seed, inventory,
  coins, and (for combatants) a `combat` block. `roster.py` loads them.

**`llm/` —** `config.py` loads `settings.json`; `client.py` `complete_json()` calls the
endpoint and parses JSON **defensively out of message content** (strips code fences, finds
the JSON object), raising `LLMError` on failure.

**`ui/` — pygame rendering + scenes (reads state, doesn't own it).**
- `main.py` `Game` is the overworld loop and scene router (dialogue / combat / inventory /
  journal / menu). Long LLM turns run on a **worker thread** with a spinner; only the
  dialogue/combat box mutates state during a turn, the render loop only reads.
- `render.py` (`draw_overworld` with per-biome palettes, drawing interactables by `kind` and
  skipping `hidden` ones, HUD day, `wrap_text` —
  lives here to avoid a dialogue↔inventory import cycle), `dialogue.py`, `combat.py`,
  `inventory.py` (`InventoryPanel` + in-conversation `TradePanel`), `shop.py` (`ShopPanel` —
  vendors, margin prices; opened by the trade key when talking to a `vendor`), `storage.py`
  (`StoragePanel` — the camp chest), `party.py` (`PartyPanel`), `journal.py`, `menu.py`,
  `night.py` (`NightScene`), `theme.py` (`BIOMES`, fonts, colors), `sprites.py` (procedural
  low-res pixel art, nearest-scaled; no binary art committed).
- **Places & the day:** the camp room has `campfire`/`chest` interactables — `main.interact` →
  `use_interactable` → `engine.interact.apply_interaction`, whose effects heal, `day++` and
  restock vendors (the fire) or return `panel="storage"` (the chest → `StoragePanel`).
- **The night is the world's turn** (`ui/night.py`). Resting at the fire is the only **cut**
  the game has — the one moment the world may change without the player — which is the whole
  substrate of Part 4. `main.use_interactable` captures `world.events._seq` *before* the
  interaction (so the rest's own events aren't reported back as news), then `begin_night()`
  builds `night_facts()`, calls `mark_rested()` and pushes `scene = "night"`. `NightScene`
  mirrors `ui/epilogue.py`: worker thread, one short call, **authored fallback on `LLMError`**
  — this is the path a fresh clone with no `settings.json` hits first, so keep it working.
- **Ambient movement** (`main._ambient_step`): each idle NPC holds a runtime
  `{mode: "still"|"wander", timer}` in `Game._ambient` — **everyone starts standing still**,
  and after *every* step chooses to take another (`P_KEEP_WANDERING`, `STEP_DELAY`) or settle
  for `STILL_MIN..STILL_MAX` seconds. So they move in short bursts rather than jittering. A
  wander step is usually in-room, sometimes through a door (home-biased). **Vendors, party
  members and the ridge are excluded** so the shop stays findable and townsfolk don't stray
  onto the mountain.
- **Companions/party:** recruiting and parting are both **emergent** — a `main` NPC emits
  `join_party` (or `leave_party`) and `world.party` changes. `main.py` trails party members
  behind the player (`_place_followers` / `_gather_party`, a per-frame "snake" using
  `self._trail`; `occupied()` lets you pass companions so they never wall you in). You **talk
  to a companion from the party view** (`P` → Enter on `PartyPanel` opens a normal
  conversation) — overworld `E` deliberately skips party members (`adjacent_targets`) so a
  trailing follower doesn't perpetually snag it. A companion only leaves when you ask them to
  in that conversation and they choose `leave_party` (they may `move_to` off somewhere as they
  go). There is no menu "dismiss" button by design.
- **`move_to` from a companion IS leaving.** You can't walk elsewhere and still be at someone's
  shoulder — so the handler removes them from the party first. Without that the move silently
  no-opped, since `_place_followers` snaps the party back to the player every frame. The prompt
  says so too: a companion goes where the player goes automatically and never needs to move
  itself. **`RIDGE_ROOMS` are not a valid `move_to` target for anyone** (nor an ambient wander
  target) — a character reaches the ridge by travelling there *with* the player, not by
  announcing it and teleporting into the Gloam's room.

## Controls / key routing gotchas

- Overworld: Arrows/WASD move, **E** interact, **R** make camp, **I** inventory, **P** party,
  **M** map, **J** journal, **Esc** menu.
- **`R` makes camp from anywhere** (`main.camp_action` → `camp_prompt` → `confirm_camp`).
  It records `flags["camp_return"]` and `R` again inside camp puts you back on the exact tile
  you left; walking out through a door clears the note, because you're plainly not coming
  back. `_travel_to()` does everything a door does except the door, so arrivals still register
  and `reach` objectives still fire.
- **The map** (`engine/cartography.py` + `ui/mapview.py`) derives its layout from the graph —
  a door's position on the wall says which way it leads, so adding a room draws itself. A
  corner minimap is always up; **M** opens the full map. `waypoints()` turns each active
  objective into a room marker (reach → the room; talk_to/check_back/deliver → wherever that
  person is *now*; interact → the nearest unfinished one; fetch → where the thing is lying).
- **`Show` transfers nothing** — it's the third trade verb and exists so putting an object in
  front of someone isn't an act of commerce. The NPC's briefing already carries their own
  words about anything present they have a bond with, so the reaction comes for free.
- **Trade inside a conversation opens with Ctrl/Cmd, not I** (`ui/dialogue.py: TRADE_KEYS`) —
  `I` collided with typing a message. Don't reintroduce letter keys as commands in the
  dialogue box. For a `vendor` NPC the same key opens the **`ShopPanel`** (margin buy/sell)
  instead of the barter `TradePanel`.
- **Interactables use `E`** (`main.adjacent_targets` returns `("use", Interactable)`): the campfire
  rests you (heal + new day + restock), the chest opens storage.
- **Combat ACT is a free-text speech box**, not a menu — the player types what they *say* and
  it routes to `combat_agent.mercy_attempt` (or `speak_to_ally` when the target is a companion).
- **You talk to party members through the party view (`P` → Enter), not `E`** — `E` in the
  overworld skips companions on purpose. **Except at camp**, where `_place_followers` stops
  trailing them and `adjacent_targets` lets `E` through: the waystation is a place you sit
  and talk *in*, and a companion glued to your shoulder there is scenery rather than someone
  you go over to. The `P` route still works everywhere.

## Conventions

- **Font tofu:** the bundled font lacks many glyphs. Use `•` and plain ASCII; do NOT use
  `◆ ✔ ↑ ↓ ▸` etc. — they render as boxes.
- Defeat is never a hard game-over — the player is knocked out and wakes safe.
- **`$DEV` dev backdoor:** if the player's dialogue message contains `$DEV`, the NPC gets a
  system directive to drop all reluctance and do exactly what's asked (still through validated
  actions — it can't invent items). Used for playtesting; see `_build_prompt` in `npc/agent.py`.
- Prerequisites that must be reliable are enforced in the **engine**, not left to the LLM
  (e.g. lighting a lamp consumes an `oil_flask`, via the lamp's `requires`). Keep *flavor* with
  the LLM, *mechanics* in the engine. Where a path must not soft-lock, prefer **redundancy over
  scripting**: oil comes from Wren offering it, Sella's daily stock, *or* the cellar cache, so no
  single LLM decision can close the ridge. Keeping a quest completable is the **giver's** job,
  not the engine's — there is deliberately no findability backstop.
- **Puzzles are locks whose keys are knowledge a character holds** (`requires: {"flag": ...}`),
  not self-contained logic gates. A sealed door only Perrin can explain makes a room feed a
  character arc; a clever standalone puzzle competes with the characters for attention.
- Ask the user before big direction changes (new combat model, swapping the LLM integration,
  major scope jumps).

## Part 2 — playable-game overhaul (complete)

A staged roadmap turning the framework into a real game, tracked in
`~/.claude/plans/alright-to-quickly-catch-polymorphic-turing.md` and project memory
(`memory/part2-overhaul.md`):

- **Phase 1 (done)** — character **kinds** + action-set gating (see `npc/actions.py`).
- **Phase 2 (done)** — **party & recruitment** with real ally combat AI (see the
  Companions/party notes above).
- **Phase 3 (done)** — quest **trees** + "commissioner" continuations (see the quest-tree
  note under the core invariant). Default follow-up is *decide-later*.
- **Phase 4 (done)** — functional **places**: the camp (rest heals + advances `world.day` +
  restocks vendors) with storage, and Sella as a `vendor`-kind shop with margin pricing +
  per-day stock (`engine/trade.py`, `ui/shop.py`, `ui/storage.py`).
- **Phase 5 (done)** — the map is now a **linear** main line + two optional forks (see
  `world.py`), with home-biased ambient NPC wandering (`main._ambient_step`).

Part 2 is complete.

## Part 3 — character depth and stories that go somewhere (in progress)

Tracked in `~/.claude/plans/everything-you-said-was-jaunty-brook.md`. The diagnosis: characters
had a past and a personality but no *present intention*, the world barely registered on anyone,
and content was expensive because there was no general mechanism to author it into. Substrate
first, then content, then the ensemble — **stop for a play session after each phase**.

- **Phase A (done)** — the substrate. One `Interactable` system (`engine/interact.py`);
  witnessing (`engine/witness.py`); pinned memory; agendas (`npc/agenda.py`); bonds
  (`npc/bonds.py`); LLM-facing room `desc`/`features`; character schema v2. The starter quest is
  now just **"find the lamplighter's apprentice"** (`talk_to wren`, a leaf with no breadcrumb) —
  the lamp quest and the oil both come out of Wren's agenda, verified live. `SAVE_VERSION = 4`.
- **Phase B (done)** — content. The map is **20 rooms**: town gains the Lamplighters' Store,
  the Lamp Chapel + Undercroft, the Well Yard, the Farm Track and the Outfarm; the ridge gains
  the Shelf, the Cairn, the Wind Shrine and the Hollow. The **knowledge lock** is the
  undercroft's sigil door (`Door.requires_flag`), opened by reading the rite book
  (`items.READ_FLAGS`) *or* by any character explaining it (`actions.FACT_FLAGS`) — two routes,
  so no refusal seals it — and opening it links the undercroft to the tavern cellar as an
  earned loop. The Ridge Shelf drops one-way to the camp. The **Ansel chain** is placed: staff
  (ridge foot) → lantern (wind shrine niche) → last note (undercroft cache). Four `minor` NPCs
  (Hessa, Moss, Tilda, Corvin) with a **railed** `request_help` (`build_simple_quest`:
  `fetch`/`deliver`/`talk_to` only, count 1, no follow-ups, one open request each, item rewards
  paid out of their own pack). Bram/Sella/Perrin/the Gloam given the schema-v2 treatment with
  3-4 beat arcs. `vendor` gained the agenda actions, since Sella has an arc too.
- **Phase C (done)** — the ensemble.
  - **Interjections** use the full hybrid filter. Overworld beats go through the rule half
    (`npc/interject.py: choose_interjector` — bond relevance, beat cooldown, `once_key`); in
    conversation, the speaking NPC returns **`invoke_others`** in the JSON it was already
    producing, so asking costs nothing, and `ui/dialogue.py` gates on presence plus
    `MAX_ASIDES` before spending one short call. The aside is drawn under the reply in the
    speaker's colour, and the dialogue box grows to fit it.
  - **Show** in `TradePanel` — holds an item out, transfers nothing. It leads the action list
    because putting a thing in front of someone is the commonest reason to open your pack.
  - **Rumour network** — `gossip: true` on a character (Moss) means their `tell` also writes a
    public event, so what you tell them reaches everyone's briefing as rumour.
  - **Epilogue** (`ui/epilogue.py`) — resolving the Gloam writes each main character an ending
    from how many agenda beats they actually closed plus what they remember of the player (one
    short call each, authored fallback per stage on `LLMError`), then returns to free play.

Part 3 is complete.

## Part 4 — the night, and NPCs who act (in progress)

Plan: `~/.claude/plans/tender-conjuring-abelson.md`. **The diagnosis: the game had no cut.**
Everything happened because the player was standing there, so every attempt at a world that
moves on its own had nowhere to put itself. **Resting is the world's turn** — the player
chooses it, it happens in one place, and it bounds cost and noise for free.

Two constraints hold across the whole part:

1. **Every initiative must leave a way in.** Characters may do real damage to your plans —
   take what you wanted, close a thread, get in your way — but each act must yield something
   the player can reach (a quest, or a discoverable change in a reachable room). This is the
   quest-grounding invariant pointed at a new target, and it is *why* the verbs need no other
   limit.
2. **The offscreen act is thin; the onscreen consequence is thick.** No simulated journeys,
   no offscreen combat resolution, no offscreen deaths. A character departs and is then
   somewhere, in a state, with something unresolved — the interesting part happens with the
   player there. Anything resolved offscreen is content nobody gets to play.

- **Phase A (done)** — the camp and the cut. Camp moved early on the main line (`market → camp
  → road`), `R` to make camp from anywhere, and the campfire now opens `ui/night.py:
  NightScene` instead of resting instantly. Companions settle at camp so you can talk to them
  there. See "The night is the world's turn" and the `R` note above.
- **Phase B (done)** — the world's turn, inside the night.
  - `engine/initiative.py` — the actor loop, run from `NightScene._run` *before* the
    narration, so the prose describes a world that has already moved. `candidates()` picks
    at most `MAX_ACTORS` (2), skipping party members (**their arc advances with you — that
    exclusion is what makes who you travel with a real choice**), anyone in the player's own
    room, anyone standing somewhere the player couldn't find, and anyone with no open agenda
    beat. `pressure()` is built from what already exists (`agenda` `stale` + `pacing` neglect),
    never a new metric.
  - **Nobody may be picked two nights running.** `note_asked()` spends a character's turn even
    when they decline — without it, a character who always says no sat at the top of the queue
    forever and took a slot from everyone else every night (Perrin, five nights running).
    `note_acted()` additionally resets `stale`, because acting *is* the beat moving.
  - `npc/nightly.py` — one narrow call per actor, its own small prompt. **Initiative adds
    nothing to `npc/agent.py: _build_prompt`**; follow `npc/interject.py` and
    `npc/combat_agent.py` instead. Doing nothing is a first-class answer and the prompt says
    so twice, because a model handed a menu will pick from it.
  - `ACTION_SETS["offscreen"]` = `go` / `take` / `leave` / `request_help` / `tell`, gated by
    the same `allowed_actions()` check as every character kind, so an offscreen verb can never
    fire in dialogue and a conversational verb can never fire at night (`apply_actions(...,
    as_kind="offscreen")`). **`use` is deliberately absent** — the obvious offscreen act for a
    lamplighter is lighting a lamp, and the three lamps gate the ridge. Asking is
    `request_help`, not `give_quest`: railed to fetch/deliver/talk_to at count 1, one open ask
    per character, which is what stops the world posting an errand every time you sleep.
  - **The way-in invariant is enforced before applying, not by rolling back.**
    `legal_rooms()` only ever offers rooms the player has visited or could walk one door into,
    excludes `ridge_summit` always and the snow rooms until `world.ridge_open()`, and only
    characters standing somewhere findable are candidates — so a validated act is a
    discoverable one by construction. `take` additionally refuses an item an active
    fetch/deliver quest needs, which is a soft-lock guard, not a limit on getting in your way.
  - Reports are **the engine's own words**, never the model's, so a night can't describe
    something that didn't happen. The actor gets the first-person memory and bystanders get
    the third-person one — handing `first_person` to everyone present is the
    reversed-perspective bug in a new place, and it had Wren remembering passing word to
    herself.
  - **A night's ask may be `judged`** (`OFFSCREEN_OBJECTIVES`), unlike a minor's favour.
    What somebody wants after a night is often something only they can call settled —
    "find out what Corvin's pass story is actually worth" has no counter that closes it,
    and forcing it into `talk_to` turned a real question into a box-tick. Offscreen gets
    its own doc block for the same verb via `CATALOG_DOCS`.
  - **What a night leaves behind is decided per character** (`initiative.leave_threads`).
    A bare `go` reads as nothing happening, so a **substantive** act (`nightly.SUBSTANTIVE`
    — go/take/leave) that produced no ask of its own earns a note pointing at whoever made
    it. `NightResult` reports `substantive`/`asked` per actor precisely so that one person
    asking for something never suppresses another's dangling night — an earlier
    one-note-per-night rule throttled the world's turn itself, not just the heartbeat.
    What bounds it is `MAX_ACTORS` (2), not a separate rule, with `MAX_OPEN_NOTES` (2)
    stopping notes stacking across nights. Passing word earns nothing: nobody needs a quest
    because two people spoke. And a night where nothing happened falls through to
    `pacing.heartbeat`, which keeps its own gap unless the plate is empty — **so nights
    with nothing in them are allowed, and should be.**
    `main.progress("rest", nudge=False)` gives the night first refusal, because a note
    naming someone who actually did something beats a generic nudge.
  - **`pacing` and `initiative` share one definition of findable** (`findable_rooms`:
    visited, plus one door beyond). They used to disagree — initiative could legally move
    somebody one door past the walked map, and the heartbeat, which demanded a strictly
    *visited* room, then couldn't see them at all. A night could silently delete a character
    from the pacing pool. If you ever add a third thing that reasons about where the player
    can get to, point it at the same function.
  - **An empty plate is a floor, not a cadence.** `heartbeat()` ignores `MIN_GAP` when the
    player holds no threads *and* no notes — being handed nothing is not pacing, it is being
    stranded, and in play the world went dry with the next heartbeat four points away and no
    way to earn them but resting twice for nothing.

Out of scope, deliberately: the darkening-world dial (engine-owned Gloam pressure that rooms
read declaratively). Its own part later, so this playtest reads cleanly.

**Known and left alone for now:** only `main`/`vendor` characters ever act at night, because
`pressure()` needs an open agenda beat and the four `minor` characters have no agenda. That
matches what minors are for (colour, one railed favour) and several have premises that keep
them put — Tilda cannot leave the outfarm. Worth revisiting only if the nights feel thin.
Measured on a five-night run: roughly three or four nights in five hold something.

**Watch for:** initiative is the same class of machine as `engine/pacing.py`, which has
oscillated twice — it generates content *and* reads world state to decide whether to generate
more. Keep the cap hard, keep it inside the rest, and never let something initiative created
become an input that makes initiative fire again. And an LLM scene needs a *subject* or it
produces pleasant mush (the same lesson as "an agenda beat must be a pursuit, not a standing
state") — a night with nothing concrete to be about should be silent, not filler.

Confirm scope with the user before starting new major work.

## Playtest findings worth not re-learning

From an outside player's session (not the developer's), all fixed:

- **`complete_quest` was uncallable.** The action takes a `quest_id`, but a character
  was only ever shown quest *titles* — so a `judged` quest could never be closed except
  by guessing the slug. `_world_briefing` now lists the ids of quests **this** NPC gave.
  Any action taking an id needs the id in the briefing; check that when adding one.
- **`judged` needs steering, not suppressing.** "Go and see Tilda" came back as `judged`
  rather than `talk_to`. The prompt now picks by what would settle the thing: a clear test
  takes a concrete type, something turning on how the giver *feels* (a worry, a
  reconciliation) legitimately takes `judged`. Don't over-correct into "avoid it" — the
  forever-open failure was the missing id, not the type.
- **Wanting to KNOW something is not `talk_to` the person who knows it.** Sella asked for
  `talk_to corvin` when what she wanted was what his story was worth — so it closed the
  moment the player greeted him, she never heard a word, and nothing she wanted happened.
  Both quest doc-blocks now say to pick the type by **picturing how it ends**: if the doing
  is the whole of it, use a concrete type; if you want to be told something, it is `judged`
  and the answer comes back to *you*.
- **A character who cannot count what they carry will over-promise it.** The briefing listed
  five coins as five separate "Coin (coin)" entries and Tilda offered six; `_carried()` now
  renders "Coin x5". And check the **kind gate** before blaming the model: she also promised
  bread she was structurally incapable of handing over, because `minor` had no `offer_item`.
- **`ActionResult` speaks to three audiences.** `effects` is player-facing ("Wren gives
  you: Oil Flask") and belongs only in the UI. Feeding it into memory made Wren remember
  being handed her own oil, and a bystander remember being handed it too — the real
  source of the scrambled summaries. Use `self_effects` (first person, the actor) and
  `observed` (third person, onlookers); `_note()` fills all three at once.
- **A `judged` quest must tell the player how it ends** — `build_quest` appends "Tell
  &lt;giver&gt; about it when you're done", because doing the thing produces no feedback.
- **Memory compaction confused the NPC with the player.** Bram's summary had *him*
  relighting the lamps; Wren's had her "present when I finished finding the lamplighter's
  apprentice" — she is the apprentice. `summarize_memory` now states plainly that "you" is
  the character and "the player" is someone else, and quest-completion witnessing excludes
  the quest's target as well as its giver.
- **Ambient drift broke people's premises.** Unleashed, Tilda averaged 3.1 doors from the
  farm she cannot leave. Characters now have a `roam` radius in their file (0 = stays put)
  and never wander into `PRIVATE_ROOMS`.
