"""One character, one night, one decision.

This is a **separate, small prompt**, and that is the point. `npc/agent.py:_build_prompt`
runs to some three and a half thousand tokens, and every block added there makes every
other block quieter — so initiative gets its own call rather than another section in the
one that already exists. The pattern is `npc/interject.py` and `npc/combat_agent.py`:
narrow specialists, which are the best-behaved parts of this system precisely because
each one asks for a single thing.

The prompt is built from exactly what the engine will accept — the legal destinations
come from `initiative.legal_rooms()`, the items from what is actually lying there — so
the model chooses among valid options instead of being corrected afterwards.

**Doing nothing is a first-class answer.** An LLM handed a menu will pick from it, and a
world where somebody does something every single night is noise. A night with nothing
concrete to be about should be silent; quiet nights are what make the loud ones land.
"""
from __future__ import annotations

from typing import NamedTuple

from engine.items import display_name
from engine.witness import BEAT, record_experience
from engine.initiative import legal_rooms, note_acted
from llm.client import LLMError, complete_json
from npc.actions import action_catalog, apply_actions
from npc.agenda import open_goal
from npc.bonds import notes_here
from npc.memory import NPCMemory
from npc.roster import character_name, load_character

MAX_ACTIONS = 2          # one move plus one act; never a journey
_MEMORY_LINES = 4


def _item_ids() -> str:
    """Just the ids and names — the nightly prompt is meant to stay small, and the full
    catalogue with descriptions is a dialogue-prompt luxury."""
    from engine.items import ITEMS
    return ", ".join(f"{iid} ({e['name']})" for iid, e in ITEMS.items())


def _system(char: dict, goal: dict | None) -> str:
    aim = goal["want"] if goal else "nothing in particular"
    why = f" Why it matters to you: {goal['why']}" if goal and goal.get("why") else ""
    return (
        f"You are {char['name']} — {char.get('role', '')}. {char.get('personality', '')}\n"
        f"What drives you: {'; '.join(char.get('drives', []))}\n\n"
        f"# Tonight\n"
        f"It is night in Emberhold. The outsider is asleep at the waystation and nobody "
        f"is watching you. You are deciding what to do about your own business, on your "
        f"own initiative.\n"
        f"What you have been trying to do: {aim}.{why}\n\n"
        "# What a night can hold\n"
        "One night. You can walk somewhere and stay there, pick up or put down one "
        "thing, send word to someone, or ask for help with something. That is all.\n"
        "You cannot fight, search, travel far, or finish anything tonight. Nothing gets "
        "settled in the dark — whatever you start is still standing there in the "
        "morning for someone to walk into.\n"
        "At most two actions, and usually fewer.\n\n"
        "**Most nights nobody does anything.** People sleep. Act only if you genuinely "
        "cannot leave this alone any longer — if you would honestly just go to bed, "
        "return an empty list. That is the normal answer.\n"
        "And if all you would do is send someone a message, that is usually a sign the "
        "honest answer was to do nothing. Sending word costs you nothing and changes "
        "nothing; save it for when there is something someone truly needs to know.\n"
        "But **if you do act, leave something behind that the outsider could pick up.** "
        "Walking somewhere and saying nothing about why is the same as not acting at "
        "all, from anyone else's side of it. If your night gives you a reason to want "
        "their hands for something, ask for it — that is usually the point of having "
        "moved at all.\n\n"
        + action_catalog("offscreen")
        + '\nReply with ONE JSON object: {"actions": [ ... ]}'
    )


def _user(world, rooms, npc_id: str, goal) -> str:
    npc = world.npcs[npc_id]
    room = rooms[npc.room]
    ground = world.ground_items_in(npc.room)
    here = [nid for nid, n in world.npcs.items() if n.room == npc.room and nid != npc_id]
    legal = legal_rooms(world, rooms, npc_id)
    mem = NPCMemory(npc_id).as_prompt(_MEMORY_LINES)

    lines = [
        f"Where you are: {room.name} (id: {room.id}). {room.desc}",
        "You are carrying: "
        + (", ".join(f"{display_name(i)} ({i})" for i in npc.inventory) or "nothing"),
        "Lying on the ground here: "
        + (", ".join(f"{display_name(g.item)} ({g.item})" for g in ground) or "nothing"),
        "Where you could walk tonight (use the id, and only these): "
        + (", ".join(f"{rooms[r].name} ({r})" for r in legal) or "nowhere worth going"),
        "People you could send word to, or ask about, or send someone to (use the id): "
        + (", ".join(f"{character_name(n)} ({n})" for n in world.npcs if n != npc_id
                     and n != "gloam") or "nobody"),
        # request_help grounds its target against these, so an ask built from anything
        # else is dropped. Cheaper to show the list than to reject the answer.
        "The only things that exist, if you need to name one: " + _item_ids(),
    ]
    if here:
        lines.append("Also here tonight: "
                     + ", ".join(f"{character_name(n)} ({n})" for n in here))
    notes = notes_here(npc_id, room=npc.room,
                       items={g.item for g in ground} | set(npc.inventory),
                       npcs=set(here))
    if notes:
        lines.append("What is here that matters to you:\n"
                     + "\n".join(f"- {n}" for n in notes))
    if mem:
        lines.append(f"What you have been turning over lately:\n{mem}")
    lines.append("\nWhat do you do tonight, if anything?")
    return "\n".join(lines)


# Acts that change the board and so plausibly leave the player something to take up.
# Passing word is not one of them: nobody needs a quest because somebody spoke to
# somebody else, and forcing a note for it made the world feel like it was feeding you.
SUBSTANTIVE = {"go", "take", "leave"}


class NightResult(NamedTuple):
    lines: list          # third-person, the engine's own words
    substantive: bool    # moved something the player could walk into
    asked: bool          # left an actual quest of their own


_NOTHING = NightResult((), False, False)    # tuple: a shared default must not be mutable


def decide_night(world, rooms, known, npc_id: str) -> NightResult:
    """Ask one character what they do tonight; validate and apply it.

    The lines are third-person descriptions of what actually changed — the engine's own
    words, never the model's, so a report can't describe something that didn't happen.
    `substantive` and `asked` are per-character, not per-night: whether *this* person
    needs a thread left behind them is nothing to do with what anybody else did.
    """
    try:
        char = load_character(npc_id)
    except KeyError:
        return _NOTHING
    npc = world.npcs[npc_id]
    goal = open_goal(npc)
    was_room = npc.room

    try:
        out = complete_json(_system(char, goal), _user(world, rooms, npc_id, goal),
                            temperature=0.85, max_tokens=320,
                            log_group=f"night-act:{npc_id}")
    except LLMError:
        return _NOTHING    # a night the endpoint slept through is simply a quiet one

    actions = out.get("actions")
    if not isinstance(actions, list) or not actions:
        return _NOTHING
    chosen = actions[:MAX_ACTIONS]
    quests_before = len(world.quests)
    result = apply_actions(world, npc_id, chosen, known, rooms, as_kind="offscreen")
    if not result.effects:
        return _NOTHING    # everything they proposed was dropped; the night stays quiet
    types = {str(a.get("type", "")) for a in chosen if isinstance(a, dict)}
    substantive = bool(types & SUBSTANTIVE)
    asked = len(world.quests) > quests_before

    note_acted(world, npc_id)
    # What they did is their own experience — first-hand, and *only* theirs. Handing the
    # first-person line to everyone present is the reversed-perspective bug in a new
    # place: it had Wren remembering passing word to herself. The actor gets "You…",
    # bystanders get "Wren…", and the town gets the public event.
    onlookers = [nid for nid, n in world.npcs.items()
                 if n.room == npc.room and nid != npc_id]
    for i, mine in enumerate(result.self_effects):
        record_experience(
            world, "night", result.observed[i], room=was_room, salience=BEAT,
            first_person=mine, public=True, targets=[npc_id],
            bond_items=tuple(npc.inventory),
        )
        for other in onlookers:
            NPCMemory.remember_for(other, result.observed[i])
    # `effects` is the player-facing phrasing and already names who did it — prefixing
    # the name again produced "Old Perrin: Old Perrin passes word to Wren."
    return NightResult(list(result.effects), substantive, asked)
