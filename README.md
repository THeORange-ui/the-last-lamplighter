# The Last Lamplighter

A small, turn-based RPG whose NPCs are driven by an LLM. Each character has a
personality, backstory, drives, memory, and a disposition toward you — and they
act on the world through a **bounded, validated action vocabulary** (give a quest,
warm/cool toward you, hand over an item, reveal a secret, leave, end the talk).
The point of the project is the *framework*, not a finished game.

Setting: **Emberhold**, a town dying in permanent dusk. Its great lantern, the
Hearthlight, is failing; something on the ridge — the **Gloam** — is eating the
light. Talk to the townsfolk, take up quests that emerge from them, and find your
way to the ridge.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp settings.example.json settings.json   # then fill in your endpoint
```

`settings.json` (gitignored) points at any OpenAI-compatible endpoint:

```json
{"base_url": "https://api.openai.com/v1", "api_key": "sk-...", "model": "gpt-4o-mini"}
```

## Run

```bash
.venv/bin/python main.py            # persists NPC memory across runs
.venv/bin/python main.py --fresh    # wipe NPC memory and start clean
```

Move with **Arrows/WASD**, interact with **E**, open your **inventory** with **I**,
your **party** with **P**, the **journal** with **J**, and the **menu** with **Esc**
(Continue / Save / Load / Save As / Save and Quit). Walk up to an NPC and press E to
talk; type freely; press Enter to send.

Ask a townsperson to travel with you and they may **join your party**: companions follow
you room to room, fight at your side (each takes its own AI-driven turn — attack, guard,
or *speak*, and a companion's words can even help talk a foe down), and you can address
them mid-battle. Open the party view with **P** to see who's with you or part ways —
they'll say goodbye and wander off somewhere of their own choosing. Different kinds of
character offer different things: a main character can join you and give quests; lesser
NPCs can only trade or share a word.

In the inventory you can **Use** items (read the ridge map, eat food to heal) or
**Drop** them on the ground. Press **Ctrl/Cmd** *during a conversation* to open a trade
view with both inventories: **Gift** or **Sell** your items, **Ask for** or **Buy** theirs.
Gifting and asking go through the NPC's own judgement; buying and selling use coins at
fixed catalog prices.

The world runs as a **main line** from the town out to the ridge —
Square → Tavern → Market → the Old Road → the Waystation camp → the ridge — with a couple of
optional side rooms to explore. **Sella** keeps a market stall: press **Ctrl/Cmd** at her to
**shop** (buy from her daily stock, sell your finds — she takes a margin). At the **camp**,
rest by the fire to heal, pass to the next **day** (which restocks the shop), or stash things
in the **chest**. Townsfolk drift about their part of town as you play.

Read Ansel's ridge map and light every lamp, and the ridge path opens — a snow-swept climb
with creatures to fight (or spare) and, at the summit, the Gloam. In combat, **Act** opens
a free-text box: you say something to the enemy, and it answers — and still reaches for you
in the same breath, so talking is never free. The more your words wear down its will, the
weaker its blows land, until it no longer strikes at all and you can spare it instead of
destroying it.

Saves are named slots under `save/` — each one bundles the whole world *and* every
NPC's memory, so loading a slot restores the relationships and conversations exactly
as they were. The game continues from your most recent slot on launch and autosaves
on exit; `--fresh` wipes all saves and memory.

## How it fits together

| Layer | What it does |
|-------|--------------|
| `engine/` | `WorldState` (the single source of truth), the bounded+checkable quest system, and the Emberhold map. |
| `npc/` | Character files (`characters/*.json`), per-NPC memory, the action vocabulary + validation, and the LangGraph brain (`agent.py`: perceive → reason → act). |
| `llm/` | Loads `settings.json` and talks to the OpenAI-compatible endpoint, parsing JSON defensively (no reliance on the function-calling API). |
| `ui/` | Pygame rendering + the dialogue overlay (free-text input, threaded turn, typewriter). |

Quests **continue**: finishing one doesn't end the thread. Most quests leave their next
step "to be decided", so when you return to whoever gave it, they react to what you did and
set you on the next stage — an arc that grows out of the conversation rather than a fixed
script (and can be brought to a close when your story with them is done).

The LLM never mutates the game directly: it returns `dialogue + actions`, and
`npc/actions.py` validates every action against real world entities before
applying it. Ungrounded actions are dropped, so emergent quests stay completable.

**Milestones:** M1 (this) is the social/quest framework. M2 adds turn-based
combat with an ACT/mercy route (talk enemies — and the Gloam — down). See
`CLAUDE.md` for the full design.
