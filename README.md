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

You begin with one instruction — *find the lamplighter's apprentice* — and nothing
else is scripted. Each character carries their own **agenda**: what they are trying
to do this week, which they will raise with you themselves, press you about if it
stalls, and set aside for the next thing once it's done. The lamps need relighting
because Wren has been putting it off and is frightened to go alone, not because a
quest was placed in the world.

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
your **party** with **P**, the **map** with **M**, the **journal** with **J**, and the
**menu** with **Esc**
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
view with both inventories: **Show**, **Gift** or **Sell** your items, **Ask for** or **Buy**
theirs. Show hands over nothing — you just hold the thing out, which is the only sane way to
put a dead man's staff in front of the apprentice who lost him. Gifting and asking go through
the NPC's own judgement; buying and selling use coins at fixed catalog prices.

Companions **speak up on their own** when something in front of them actually touches
them — an object they knew, a place they have history with — and otherwise walk
quietly, which is most of the time. In conversation they'll cut in when what someone
said lands on them, because the person speaking is the one who notices that it did.

Anything you tell **Moss** is round the whole town by morning, and turns up in other
people's mouths as rumour.

Characters remember what they were **there** for. Take a companion into the tavern
cellar and they remember the room; pick something up in front of them and they saw
you do it; hand them a book and they read it and know what it said. What they only
heard about second-hand reaches them as rumor instead — so telling someone what
happened is a real thing to do, and finding Ansel's old staff in front of his
apprentice is not the same as mentioning it later.

Everyone is in the middle of something when you meet them, and they lead with it —
Bram has been watching the road for eight days for a cart that hasn't come, Tilda
lost her mother's locket on the market run, Perrin wants you to tell the apprentice
to stop knocking. A corner **minimap** (full map on **M**) marks the room every open
objective is pointing at, so an errand never turns into a search.

Everyone has something they are working on, and it goes somewhere. Bram keeps the
last warm room in town, and behind his bar is a coat that has been ready by the door
for years. Perrin was lamplighter on the one night the Hearthlight went fully out and
has kept his own hearth cold ever since. Sella has had her cart loaded for a month and
has not left. Follow any of them far enough and they arrive at the ridge, which is
where all of it has been pointing.

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
| `engine/` | `WorldState` (the single source of truth), the bounded+checkable quest system, the Emberhold map, one `Interactable` system for lamps/fixtures/puzzles, and `witness.py` (events become the first-person memories of whoever was present). |
| `npc/` | Character files (`characters/*.json`), per-NPC memory, `agenda.py` (what each character is pursuing), `bonds.py` (what they care about), the action vocabulary + validation, and the LangGraph brain (`agent.py`: perceive → reason → act). |
| `llm/` | Loads `settings.json` and talks to the OpenAI-compatible endpoint, parsing JSON defensively (no reliance on the function-calling API). |
| `ui/` | Pygame rendering + the dialogue overlay (free-text input, threaded turn, typewriter). |

Quests **continue**: finishing one doesn't end the thread. Most quests leave their next
step "to be decided", so when you return to whoever gave it, they react to what you did and
set you on the next stage — an arc that grows out of the conversation rather than a fixed
script (and can be brought to a close when your story with them is done).

The LLM never mutates the game directly: it returns `dialogue + actions`, and
`npc/actions.py` validates every action against real world entities before
applying it. Ungrounded actions are dropped, so emergent quests stay completable.

Reach the Gloam and answer it — with a blade or without one — and the dusk lifts, and
the game tells you what became of everyone based on how far you actually got with them.
Then it hands Emberhold back, lit, and you can keep walking it.

**Milestones:** M1 (this) is the social/quest framework. M2 adds turn-based
combat with an ACT/mercy route (talk enemies — and the Gloam — down). See
`CLAUDE.md` for the full design.
