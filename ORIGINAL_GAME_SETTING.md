**The following is the original plan you gave me at the start - we've been building on this overall direction since. Reference this especially when you need to catch back up on the game story and each character's lore and personality.**

Love this concept — an LLM-driven Undertale-like is a genuinely good fit, and your instinct to keep it a *framework* rather than a game is the right call. A few thoughts before we plan:

## What's strong about the design

- **Turn-based + open dialogue is the sweet spot for LLMs.** Latency is hidden by turns, and text input → text reply is exactly what LLMs are best at. Real-time would've fought the model on every frame.

- **Actions-as-tools is the correct backbone.** The clean pattern: give the LLM a bounded set of "tools" (`give_quest`, `set_disposition`, `move_to`, `join_combat`, `offer_item`, `end_dialogue`, …). Each NPC turn returns *dialogue + zero-or-more action calls*, the engine validates and executes them. This is what keeps emergent behavior from going off the rails, and it's LangGraph's home turf.

- **BYO OpenAI-compatible key** is smart — no cost liability for you, and it works with local models (Ollama/LM Studio) too.

## Things I'd flag now (design tensions worth deciding early)

1. **Emergent quests need *checkable* objectives.** "Rewards and targets determined on the fly" is great, but the engine has to *know* when a quest is done. My recommendation: the LLM fills a **bounded quest schema** (objective type from an enum — `kill`/`fetch`/`deliver`/`talk_to`/`reach` — a target that must resolve to a *known* world entity, and a reward drawn from things that can actually exist). Free-form flavor text, structured spine. Otherwise you get quests nothing can ever complete.

2. **Game state is the source of truth, not the LLM.** Dispositions, inventory, who's where, quest progress — all live in engine state. The LLM *reads* it as context and *proposes* changes via actions; it never silently "remembers" a fact that the engine doesn't also record. This prevents the classic drift where an NPC believes something the game doesn't.

3. **The "talk your way out" mechanic almost writes itself.** Undertale's ACT/mercy system maps perfectly onto AI NPCs — combat where you can *negotiate* with a hostile NPC via dialogue is a killer showcase for this whole framework, and basically free given everything else you're building.

4. **Memory needs a compaction plan.** Per-NPC memory as an append log is fine to start, but plan for periodic summarization so context stays small and cheap.

A few decisions will shape the whole plan — let me get your read on these:

Every recommendation landed — good, that's a coherent stack. Here's the draft for you to approve or redline. Two parts: **the world** (needs your creative sign-off) and **the build plan** (the framework we'll actually stand up first).

---

## Part 1 — The world (draft for approval)

**Setting: *The Last Lamplighter.*** A small dying town called **Emberhold** sits at the bottom of a valley where the sun no longer rises — it's held in permanent dusk. The town survives on a single great lantern, the **Hearthlight**, which is slowly going out. Something up on the ridge is *eating* the light. The lore-goal: reach the ridge and confront whatever's up there. Whether you fight it, free it, or talk it down is the player's story to write.

Small, self-contained, melancholy-but-warm — very Undertale in tone. A handful of rooms: town square, a home, a tavern, a path, the ridge.

**The cast (4 NPCs + the boss):**

| Character | Drive | Backstory hook | Disposition seed |

|---|---|---|---|

| **Wren**, the lamplighter's apprentice | Keep the Hearthlight burning; find their vanished mentor | Their mentor walked up the ridge and never came back | Neutral, anxious, opens the starter quest |

| **Bram**, the tavernkeeper | Protect the town; distrusts outsiders | Lost a sibling to the ridge; hides grief as gruffness | Wary → warms if you help |

| **Sella**, a traveling scavenger | Profit, then leave before the light dies | Knows the ridge path but won't share it cheaply | Transactional; friend or rival depending on play |

| **Old Perrin**, the ex-lamplighter | Wants to be left to die quietly; guilt | Let the *last* light go out once before; blames himself | Hostile/closed at first; a mercy-route unlock |

| **The Gloam** (final boss) | Not evil — *lonely*. It's the dark itself, and it's cold | Was never meant to wake; the dimming is it reaching for warmth | Fight it OR ACT your way to a truce |

**Starter quest (the one authored quest):** Wren asks you to relight three dead lamps around town to buy time. Mechanically it teaches: movement, dialogue, an NPC-given quest with a checkable objective (`reach`/`interact` × 3), and a reward. Everything after that emerges from the NPCs.

The **Gloam** as a talk-down-able boss is what makes the ACT/mercy system pay off — the "final boss" can literally be resolved with words.

---

## Part 2 — The build plan (Milestone 1: framework vertical slice)

**Stack:** Python · Pygame (rendering + turn loop) · LangGraph (NPC brains) · OpenAI-compatible client (user supplies `base_url` + `api_key`).

**Architecture — game state is the single source of truth; the LLM reads it and proposes bounded actions:**

```

rpg-game/

  main.py                # pygame loop, scene/state machine

  engine/

    world.py             # tilemap, rooms, entity positions

    state.py             # WorldState: dispositions, inventory, quests, flags

    quests.py            # bounded quest schema + completion checker

  npc/

    agent.py             # LangGraph graph: perceive → reason → act

    actions.py           # tool schemas: give_quest, set_disposition,

                         #   move_to, offer_item, join_combat, end_dialogue, act_response

    memory.py            # per-NPC append log + summarization

    characters/          # wren.json, bram.json, sella.json, perrin.json (personality+backstory+seeds)

  llm/

    client.py            # OpenAI-compatible wrapper, structured/tool-call output

    config.py            # user's base_url + key (env or settings file)

  ui/

    dialogue.py          # textbox input + typewriter reply

    combat.py            # JRPG menu + ACT/mercy

  assets/                # placeholder pixel sprites/tiles

```

**The NPC turn (LangGraph graph):** player text → build context (character file + current disposition + relevant memories + nearby world facts) → LLM call with the action tools bound → returns `{ dialogue, actions[] }` → engine **validates** each action against real world entities → applies to `WorldState` → appends to memory. Invalid actions are dropped, not executed.