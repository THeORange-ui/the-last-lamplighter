# The Last Lamplighter — AI-driven RPG framework

An Undertale-style, turn-based RPG whose NPCs are driven by an LLM. The point of the
project is the **framework**, not an AAA game: a small world where NPCs with their own
drives, memory, and dispositions produce emergent quests and stories on the fly.

## Vision / pillars
- **Turn-based, open dialogue.** Player types free text; NPCs reply via LLM. No real-time.
- **Actions-as-tools.** The LLM never mutates the game directly. Each NPC turn returns
  `dialogue + a bounded list of actions` (give_quest, adjust_affinity, move_to,
  offer_item, reveal_fact, end_dialogue, …). The engine **validates** every action against
  real world entities and applies it. Invalid actions are dropped, never executed.
- **Game state is the single source of truth.** Dispositions, inventory, quests, flags,
  positions all live in `WorldState`. The LLM *reads* state as context and *proposes*
  changes; it never silently "remembers" something the engine doesn't record.
- **Emergent but checkable.** Quests use a bounded schema with an objective type from an
  enum and a target that must resolve to a known entity, so the engine can detect
  completion. Free-form flavor text, structured spine.
- **Small on purpose.** ~4-5 characters, a handful of rooms. Set up for substantive
  stories, not scale.

## World (approved)
Setting: **Emberhold**, a dying town in permanent dusk, kept alive by a failing great
lantern, the **Hearthlight**. Something on the ridge is eating the light. Lore-goal:
reach the ridge and confront **The Gloam** — a lonely, cold final "boss" you can fight
OR talk down (ACT/mercy). Tone: melancholy-but-warm, very Undertale.

Cast: **Wren** (lamplighter's apprentice, starter-quest giver), **Bram** (wary
tavernkeeper), **Sella** (transactional scavenger who knows the ridge path), **Old
Perrin** (guilt-ridden ex-lamplighter, mercy-route unlock), **The Gloam** (final boss).

Starter quest (the ONE authored quest): Wren asks the player to relight 3 dead lamps
around town. Teaches movement, NPC-given quest, checkable objective, reward. Everything
after emerges from NPCs.

## Tech stack
- **Python 3.13**, deps in project `.venv/` (activate: `.venv/bin/python`).
- **Pygame** — rendering + turn/scene loop. Placeholder programmatic art for now
  (colored rects/labels); real pixel sprites later.
- **LangGraph** — the NPC "brain" graph (perceive → reason → act). This is the engine,
  per project requirement.
- **OpenAI-compatible LLM** — user supplies `base_url`, `api_key`, `model` in
  `settings.json` (gitignored). Never commit or echo the key. Calls go through the
  `openai` SDK. Because the endpoint is an arbitrary proxy, prefer robust **JSON-in-content
  parsing** over hard dependence on the function-calling API.

## Config
`settings.json` (gitignored, already present) holds `base_url`, `api_key`, `model`.
`settings.example.json` is the committed template. Load via `llm/config.py`. Never print
the api_key in logs or commits.

## Milestones
- **M1 (current): framework vertical slice.** Town + a couple rooms, Wren + one more NPC
  live, free-text dialogue, starter quest firing AND completing via the checker, memory
  persisting across a conversation, disposition shifting. **No combat** — but wire the
  combat action hooks so M2 is additive.
- **M2: combat.** Turn-based JRPG menu (attack/defend/item/flee) PLUS **ACT/mercy** to
  talk down hostile NPCs and the Gloam via the LLM. NPCs can `join_combat`.
- **M1 polish (done):** save/load (`engine/save.py`), memory summarization
  (`npc/memory.py` + `agent.summarize_memory`), procedural pixel-art sprites
  (`ui/sprites.py`).
- **Town layout:** the square is the hub; tavern (Bram), market (Sella), and Perrin's
  house hang off its edges, with the ridge path below. All four NPCs (Wren, Bram, Sella,
  Perrin) are live on the map.
- **Later:** richer map/art, combat (M2), memory of NPC↔NPC interactions.

## Events & memory (added after first M1 pass)
- **Event log** (`engine/journal.py`, `EventLog` on `WorldState.events`) is the shared
  record of notable happenings (quest start/complete, lamp lit, arrivals, item gets).
  *Public* events are folded into every NPC's briefing under "Recent happenings" so NPCs
  are aware of world progression without being told. All events power the player-facing
  **journal** (press **J**), `ui/journal.py`.
- **Greeting continuity:** the `APPROACH` sentinel branches on whether the NPC has any
  memory of the player. First meeting → introduce; return visit → acknowledge shared
  history and react to what's changed. This fixed the original "same greeting every visit"
  bug — the root cause was the greeting prompt never instructing the NPC to *use* memory
  (the memory was in-context and the reply path used it fine; only the greeting ignored it).
- **Completion memory:** when a quest completes, its giver gets a personal memory line
  ("The player completed the quest you gave them…") — written from `main.on_quests_completed`
  for world-triggered completions (e.g. lamps) and from `agent.act` when the giver is the
  NPC currently talking.

## Combat (M2, in progress)
- **Turn-based**, menu-driven (`engine/combat.py` logic; `ui/combat.py` scene). Player
  actions: Attack / Defend / Act / Item / Spare / Flee. HP is the player's `PlayerState.hp`;
  combat writes it back on exit. Defeat = **knocked out**, wake in the square at half HP
  minus a few coins (never a hard game-over).
- **ACT / mercy is LLM-driven** (`npc/combat_agent.py`): each enemy has `resolve`; ACT
  approaches route to the enemy's AI, which reacts in character and lowers resolve — answer
  what it truly wants and it becomes **spareable**, letting you end the fight peacefully.
  Enemy turns are also AI-chosen (attack/heavy/loom) with mechanical fallbacks on LLM error.
  Enemy turns run on a worker thread with a spinner, like dialogue.
- **The Gloam** (`npc/characters/gloam.json`) is the boss: high HP, meant to be *reached*,
  not ground down. The ridge path unseals once you've **read Ansel's ridge map** (sets
  `flags['map_read']`) and **lit all the lamps**; stepping onto the ridge starts the fight.
  Winning or sparing sets `flags['gloam_resolved']` and restores the Hearthlight.
- **Hostile NPCs:** an NPC's AI can emit `attack` mid-dialogue to turn hostile and start a
  fight (persona-driven, so they can be talked down/spared too — and they *remember* being
  spared or bested). `join_combat` makes an NPC a pledged ally who fights beside you
  (`ally_pledged` flag); allies join via `combatant_from_npc`.
- **Ridge creatures:** `gloamling` enemies ambush once on the ridge path (`path_cleared`
  flag stops repeats). Multi-enemy fights use a target-selection submenu.
- **Build order (all done):** Gloam boss → hostile NPCs + allies → ridge creatures.

## Items, inventory & economy
- **Catalog** (`engine/items.py`) is the closed set of real items — each with a display
  name, description, coin `value`, and optional `use` behavior (`eat`/`drink` heal player
  HP, `read` shows text, `key`). `use_item()` applies effects. Nothing off-catalog exists.
- **Player HP** lives on `PlayerState` (hp/max_hp); food heals it. Seeds M2 combat.
- **Inventory screen** — press **I** in the overworld (`ui/inventory.py: InventoryPanel`):
  browse items, Use or Drop them. Dropped items become **ground items**
  (`WorldState.ground_items`, rendered on the floor) that are picked up by walking onto
  them.
- **In-dialogue trade** — press **I** while talking (`TradePanel`): shows both inventories.
  On your items: **Gift** / **Sell**. On theirs: **Ask for** / **Buy**.
    - Gift and Ask route through the NPC's AI (they react in character; Ask lets them decide
      via `offer_item`) — on-theme with the AI-driven design.
    - Buy/Sell are **mechanical** at catalog `value` (`engine/trade.py`), so the economy is
      deterministic and trackable. Coins are `coin` items; NPCs and the player hold coin
      balances (player starts with a small purse). Currency is hidden from the actionable
      item lists (wallet total shows in the header).
    - In dialogue, **I** opens trade only when the input line is empty (so `i` can still be
      typed mid-message).

## Conventions / decisions
- Per-NPC character files in `npc/characters/*.json` (personality, backstory, drives,
  affinity seed). Per-NPC runtime memory is an append log; plan for summarization when it
  grows.
- Affinity is a numeric score the LLM nudges via `adjust_affinity {delta, reason}`;
  category (hostile/wary/neutral/friendly) is derived.
- **Persistence:** named save slots under `save/<name>.json`, each a bundle of the full
  `WorldState` **plus every NPC's memory** (and NPC inventories), so a slot fully restores
  a session. The in-game menu (**Esc**, `ui/menu.py`) offers Continue/Save/Load/Save As/
  Save and Quit; the game continues from the most-recently-modified slot on launch and
  autosaves to the current slot on exit. `runtime_memory/` is the live working copy
  (write-through per turn); `NPCMemory.snapshot_all/restore_all` move it in/out of slots.
  `main.py --fresh` wipes all saves + memory. Memory files are `{summary, entries}`; the
  log auto-compacts via the LLM past a threshold (in the dialogue worker thread).
- **Sprites** are built procedurally at low res and nearest-scaled (`ui/sprites.py`),
  tinted per character; no binary art assets are committed.
- **Oil is a real prerequisite:** lighting a lamp consumes an `oil_flask`; Wren reliably
  grants 3 flasks with the starter quest (deterministic, not LLM-dependent). Content flavor
  (NPCs *mentioning* oil) stays with the LLM; the mechanic stays in the engine.
- Ask the user before big direction changes (new combat model, swapping the LLM
  integration approach, major scope jumps).

## Running
```bash
.venv/bin/python main.py
```
