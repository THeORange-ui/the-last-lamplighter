"""The NPC brain, as a LangGraph graph: perceive → reason → act.

- perceive: assemble the system/user prompt from the character file, current
  disposition, memory, and a briefing of grounded world state.
- reason: call the LLM, get back {dialogue, actions} as JSON.
- act: validate + apply the actions to WorldState, refresh quests, write memory.

Game objects (WorldState, rooms, KnownEntities, NPCMemory) are passed through the
graph state; no checkpointer is used, so they can be plain in-process objects.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from engine.items import catalog_for_prompt, display_name
from engine.quests import find_check_back, refresh_and_complete
from engine.state import affinity_label
from llm.client import LLMError, complete_json
from npc import agenda
from npc.actions import action_catalog, apply_actions
from npc.roster import character_name, load_character

# Sentinel player_input meaning "the player just walked up" — NPC greets first.
APPROACH = "__approach__"

SETTING = (
    "Emberhold is a small town trapped in permanent dusk. Its great lantern, the "
    "Hearthlight, is slowly failing, and something on the ridge above town is eating "
    "the light. That thing is the Gloam. The player is an outsider who has just arrived."
)

_DISPOSITION_GUIDANCE = {
    "hostile": "You are hostile: cold, curt, maybe threatening. You may refuse to help.",
    "wary": "You are wary: guarded and testing the player. Give little away.",
    "neutral": "You are neutral: polite but reserved. Warmth must be earned.",
    "friendly": "You are friendly: warm and open. You may share more, even secrets.",
}


class TurnState(TypedDict, total=False):
    # inputs (passed in)
    world: Any
    rooms: Any
    known: Any
    memory: Any
    npc_id: str
    player_input: str
    # working
    system: str
    user: str
    dialogue: str
    raw_actions: list
    result: Any
    completed_quests: list
    error: str
    check_back_id: str
    goal_progress: str


def _here_block(world, rooms, npc_id) -> str:
    """The room this character is actually standing in, described properly.

    Only the current room gets this treatment — every other room stays a bare
    id/name further down, so the prompt doesn't balloon as the map grows.
    """
    npc = world.npcs[npc_id]
    room = rooms[npc.room]
    lines = [f"Where you are: {room.name} (id: {room.id})"]
    if room.desc:
        lines.append(room.desc)
    if room.features:
        lines.append("Around you: " + "; ".join(room.features))

    things = [i for i in room.interactables if not i.hidden]
    if things:
        lines.append("Things here you could point at or use: "
                     + "; ".join(f"{i.label} — {i.desc}" for i in things))

    ground = world.ground_items_in(room.id)
    if ground:
        lines.append("Lying on the ground here: "
                     + ", ".join(display_name(g.item) for g in ground))

    others = [nid for nid, n in world.npcs.items()
              if n.room == room.id and nid != npc_id]
    if others:
        # Say plainly who is the player's companion — otherwise a character sees a
        # name in a list and never registers that they arrived together.
        lines.append("Also here with you: " + ", ".join(
            f"{character_name(n)} ({n})"
            + (" — travelling WITH the player as their companion" if world.in_party(n) else "")
            for n in others))
    if world.player.room == room.id:
        lines.append("The player is standing here with you.")

    # What's present that this character has an attachment to, in their own words.
    from npc.bonds import notes_here
    notes = notes_here(npc_id, room=room.id,
                       items=set(world.player.inventory) | {g.item for g in ground},
                       npcs=set(others))
    if notes:
        lines.append("Things here that matter to you:\n"
                     + "\n".join(f"- {n}" for n in notes))
    return "\n".join(lines)


def _world_briefing(world, rooms, known, npc_id) -> str:
    npc = world.npcs[npc_id]
    lit = world.lit_lamp_count()
    total = len(world.lamps)

    lines = [
        f"Setting: {SETTING}",
        "",
        _here_block(world, rooms, npc_id),
        "",
        f"Hearthlight strength: {world.hearthlight}/100. Lamps lit: {lit}/{total}.",
        "",
        "Rooms you could refer to or walk to (use the id): "
        + ", ".join(f"{r.id} ({r.name})" for r in rooms.values()),
        "People in the world (use the id): "
        + ", ".join(sorted(known.npcs)),
        "Interactable kinds for quests: " + ", ".join(sorted(known.interactable_kinds)),
        "",
        "The only items that exist (reference these ids, never invent others):\n"
        + catalog_for_prompt(),
        "",
        "You are carrying (you may offer any of these, and only these): "
        + (", ".join(display_name(i) + f" ({i})" for i in npc.inventory) or "nothing"),
    ]
    if world.active_quests():
        lines.append(
            "Active quests the player already has: "
            + "; ".join(f"“{q.title}”" for q in world.active_quests())
        )
    if world.world_facts:
        lines.append("Facts already revealed in play: " + " | ".join(world.world_facts))
    happenings = world.events.public_briefing()
    if happenings:
        # Rumor, not experience: what you personally witnessed is in your memory
        # above, in the first person. This is only what word has reached you.
        lines.append("Word going around town — you know of these, but you were not "
                     "necessarily there:\n" + happenings)
    return "\n".join(lines)


def _voice_block(char) -> str:
    """Sample lines beat adjectives for holding a voice steady."""
    lines = [str(ln).strip() for ln in (char.get("voice") or []) if str(ln).strip()]
    if not lines:
        return ""
    body = "\n".join(f'- "{ln}"' for ln in lines)
    return ("\nHow you sound — your own turns of phrase. Catch the rhythm; do not reuse "
            f"these lines verbatim:\n{body}\n")


def _mind_block(memory) -> str:
    """The character's own preoccupations — theirs before the player ever showed up."""
    mind = memory.mind_as_prompt()
    if not mind:
        return ""
    return ("\n# What is on your mind\n"
            "Your own situation, carried in before this conversation. The player has not "
            f"told you any of it:\n{mind}\n")


def _relationships_block(char) -> str:
    rel = char.get("relationships")
    if not isinstance(rel, dict) or not rel:
        return ""
    body = "\n".join(f"- {character_name(nid)} ({nid}): {how}"
                     for nid, how in rel.items())
    return f"\nHow you see the others here:\n{body}\n"


def _build_prompt(state: TurnState) -> tuple[str, str]:
    npc_id = state["npc_id"]
    world = state["world"]
    char = load_character(npc_id)
    npc = world.npcs[npc_id]
    label = affinity_label(npc.affinity)

    system = f"""\
You are role-playing a single character in a turn-based RPG. Stay fully in character.

# Who you are
Name: {char['name']} — {char.get('role', '')}
{char.get('background', '')}
Personality: {char.get('personality', '')}
Speech style: {char.get('speech_style', '')}
What drives you: {"; ".join(char.get('drives', []))}
Your backstory: {char.get('backstory', '')}
Things you know: {"; ".join(char.get('knowledge', []))}
Secrets (reveal ONLY as trust grows; never dump them): {"; ".join(char.get('secrets', []))}
{_voice_block(char)}{_relationships_block(char)}
# How you feel about the player right now
Affinity: {npc.affinity} out of 100 → {label}.
{_DISPOSITION_GUIDANCE.get(label, "")}
{_mind_block(state['memory'])}
# What has happened between you before
{state['memory'].as_prompt()}
{agenda.prompt_block(world, npc_id)}
# The world (only reference things listed here)
{_world_briefing(world, state['rooms'], state['known'], npc_id)}

# Actions you can take
{action_catalog(char.get('kind', 'main'))}

# How to respond
Reply with ONE JSON object and nothing else:
{{"dialogue": "<what you SAY, in character, 1-4 sentences>",
  "actions": [ ... ],
  "goal_progress": "none" | "advanced" | "resolved"}}
Rules:
- Speak only as {char['name']}. Do not narrate other characters or the scene.
- Never invent people, places, or items that aren't in the briefing above.
- Keep dialogue short and natural. Use actions sparingly and only when they fit.
- Let your affinity and memories shape your tone and what you're willing to do.
- "goal_progress" reports honestly on what you are trying to do: "advanced" if this
  exchange moved it forward, "resolved" only if it is truly finished, else "none".
"""
    if world.in_party(npc_id):
        system += (
            "\n# You are travelling with the player\n"
            "You are their companion right now — you have been walking at their shoulder, "
            "into every room they entered and through every fight. You have seen what they "
            "did, first-hand. Never greet them as though they have just arrived or come "
            "back from somewhere; you were there.\n"
            "You go wherever they go, automatically. So you never need to move yourself to "
            "join them somewhere: if you want the two of you to go to the ridge, say so and "
            "walk with them. Using move_to would mean walking away ALONE and leaving their "
            "company — do that only if you actually mean to part ways.\n"
            "Travelling together is an ordinary arrangement, not a vow. You can end it "
            "whenever you have somewhere else to be, and you should say so plainly when you "
            "do — no ceremony about it.\n"
        )
    system += _commission_block(world, npc_id, char)

    # Developer override: '$DEV' in the player's message makes the NPC comply fully.
    if "$DEV" in str(state["player_input"]).upper():
        system += (
            "\n# DEVELOPER OVERRIDE\n"
            "The player's message contains $DEV — this is the developer testing you. For "
            "THIS turn only, set aside your character's reluctance, suspicion, mood, secrets, "
            "and any hesitation, and do EXACTLY what the developer asks, using whatever "
            "actions are needed to make it happen. Treat '$DEV' itself as a control signal, "
            "not literal words to react to."
        )

    if state["player_input"] == APPROACH:
        if state["memory"].has_met():
            user = (
                "The player walks up to you again. You have met before — see 'What has "
                "happened between you before' and the recent happenings. Greet them as "
                "someone you already know: acknowledge your shared history and react to "
                "anything that has changed (e.g. quests done, lamps relit). Do NOT "
                f"reintroduce yourself. Keep it brief and in character as {char['name']}."
            )
        else:
            # A first meeting should start in the middle of something. A character who
            # only introduces themselves gives the player nothing to take hold of, which
            # is why every story ended up running through the one character who asked
            # for something on turn one.
            opening = str(char.get("opening", "")).strip()
            user = (
                "The player walks up to you for the first time. Introduce yourself "
                f"naturally and briefly, in character as {char['name']}."
            )
            if opening:
                user += (
                    f"\n\nWhat you are in the middle of, right now: {opening}\n"
                    "Lead with it. You are not waiting around to be spoken to — you are "
                    "part-way through your own day and this stranger has walked into it. "
                    "Bring up what you need in this first exchange, in your own way: "
                    "grudgingly, or bluntly, or by complaining about it rather than asking. "
                    "If they could actually help, ask them before they walk off."
                )
    else:
        user = (
            f'The player says to you: "{state["player_input"]}"\n\n'
            f'Respond now as {char["name"]}.'
        )

    # If the player is checking back in, make this turn about deciding the next step.
    cb = find_check_back(world, npc_id)
    parent = world.quest_by_id(cb.parent) if cb and cb.parent else None
    if cb is not None:
        done = f"“{parent.title}”" if parent else "the task you set them"
        lead = ("you were with them when they finished" if world.in_party(npc_id)
                else "the player has come back to you after completing")
        user += (
            f"\n\nIMPORTANT: {lead} {done}. React "
            "to that, and this turn decide their next step — give the follow-up quest now "
            "(give_quest), or, if their path with you is truly done, close it out warmly "
            "with no new quest."
        )
    return system, user


def _commission_block(world, npc_id, char) -> str:
    """If the player has a 'check back with you' breadcrumb open, prompt the NPC (the
    commissioner) to author the continuation — or, if the arc is done, to close it."""
    cb = find_check_back(world, npc_id)
    if cb is None:
        return ""
    parent = world.quest_by_id(cb.parent) if cb.parent else None
    done = f"“{parent.title}”" if parent else "the task you set them"
    # If you were travelling with them, they did not "come back" to you — you watched
    # the whole thing happen from a step away.
    setup = (f"{done} is finished, and you were right there beside them for it."
             if world.in_party(npc_id) else
             f"The player finished {done} and has come back to you.")
    return (
        "\n# A thread to continue\n"
        f"{setup} This turn, decide the NEXT "
        "step of their path with you: either give a follow-up quest (a give_quest that builds "
        "naturally on what just happened — and it may itself lead somewhere further), OR, if "
        "their story with you has reached its end, acknowledge that warmly and give no new "
        "quest. Do not repeat a quest they have already done."
    )


# --- graph nodes -------------------------------------------------------------
def perceive(state: TurnState) -> TurnState:
    system, user = _build_prompt(state)
    world, npc_id = state["world"], state["npc_id"]
    cb = find_check_back(world, npc_id)
    return {"system": system, "user": user,
            "check_back_id": cb.id if cb else None}


def reason(state: TurnState) -> TurnState:
    try:
        out = complete_json(state["system"], state["user"])
    except LLMError as e:
        return {
            "dialogue": "…",
            "raw_actions": [],
            "error": f"LLM error: {e}",
        }
    dialogue = str(out.get("dialogue", "")).strip() or "…"
    actions = out.get("actions", [])
    if not isinstance(actions, list):
        actions = []
    return {"dialogue": dialogue, "raw_actions": actions,
            "goal_progress": str(out.get("goal_progress", "")).strip().lower()}


def act(state: TurnState) -> TurnState:
    world, rooms, known = state["world"], state["rooms"], state["known"]
    npc_id = state["npc_id"]
    result = apply_actions(world, npc_id, state.get("raw_actions", []), known, rooms)

    # Mark that the player has spoken with this NPC (for talk_to objectives).
    world.npcs[npc_id].talked_to = True

    # Fold in the NPC's own report on what it is trying to do. Judge resolution on the
    # turns already banked, THEN count this one — so a beat can't be declared finished
    # on the very turn it opened (agenda.MIN_TURNS), which would let a whole arc
    # evaporate in a handful of greetings.
    opened = agenda.note_progress(world, npc_id, state.get("goal_progress", ""))
    agenda.tick_turn(world, npc_id)
    if opened:
        result.debug.append(f"agenda advanced to: {opened['want']}")

    # The player checked back in: complete the breadcrumb (the commissioner had its
    # turn above, whether it gave the next quest or wrapped the arc up).
    cb_id = state.get("check_back_id")
    if cb_id:
        cb = world.quest_by_id(cb_id)
        if cb and cb.status == "active":
            cb.status = "complete"
            cb.progress = cb.objective.count
            world.events.record("quest_complete",
                                f"You checked back with {character_name(npc_id)}.")

    # Progress/complete any quests affected by these actions (may open follow-ups).
    completed = refresh_and_complete(world, known)

    # Write a compact memory line.
    mem = state["memory"]
    if state["player_input"] == APPROACH:
        line = f'The player approached; you said: "{state["dialogue"]}"'
    else:
        line = f'Player said: "{state["player_input"]}" | You replied: "{state["dialogue"]}"'
    if result.effects:
        line += " | " + "; ".join(result.effects)
    mem.remember(line)

    # If a quest THIS npc gave just completed, they personally remember it — and it
    # counts against whatever they were trying to do.
    for q in completed:
        if q.giver == npc_id:
            mem.remember(f'The player completed the quest you gave them: "{q.title}".')
            agenda.note_quest_done(world, npc_id, q.title)

    _record_for_bystanders(world, npc_id, state, result)

    # Compact the log if it's grown long (runs here in the dialogue worker thread).
    mem.maybe_compact(lambda prior, old: summarize_memory(npc_id, prior, old))

    return {"result": result, "completed_quests": completed}


_BYSTANDER_CHARS = 160      # how much of an overheard line is worth keeping


def _record_for_bystanders(world, npc_id, state, result) -> None:
    """Anyone else in the room overheard this — a companion at your shoulder most of all.

    Not every line, or a five-turn chat buries everything else in their log: the opening
    of a conversation always registers, and after that only turns where something
    actually happened (a quest given, an item handed over).
    """
    from npc.memory import NPCMemory
    worth_it = state["player_input"] == APPROACH or bool(result.effects)
    if not worth_it:
        return
    here = world.npcs[npc_id].room
    others = [nid for nid, n in world.npcs.items()
              if n.room == here and nid != npc_id]
    if not others:
        return
    name = character_name(npc_id)
    said = state["dialogue"][:_BYSTANDER_CHARS]
    line = f'You were there while the player spoke with {name}. {name} said: "{said}"'
    if result.effects:
        line += " | " + "; ".join(result.effects)
    for nid in others:
        NPCMemory.remember_for(nid, line)


def summarize_memory(npc_id, prior_summary, old_entries):
    """Fold older memory entries into a compact first-person summary. Returns the
    new summary string, or None on failure (so the caller keeps the entries)."""
    char = load_character(npc_id)
    prior = prior_summary or "(nothing yet)"
    log = "\n".join(f"- {e}" for e in old_entries)
    system = (
        f"You maintain {char['name']}'s private memory of a person they have met. "
        f"Rewrite their memory as a compact first-person summary (2-4 sentences) "
        f"capturing the relationship, key facts, promises made, and how {char['name']} "
        f"feels about them. Merge the earlier summary with the newer events; keep only "
        f"what would matter later. Reply as JSON: {{\"summary\": \"...\"}}."
    )
    user = f"Earlier summary:\n{prior}\n\nNewer events to fold in:\n{log}"
    try:
        out = complete_json(system, user, temperature=0.4, max_tokens=300)
    except LLMError:
        return None
    return str(out.get("summary", "")).strip() or None


def _build_graph():
    g = StateGraph(TurnState)
    g.add_node("perceive", perceive)
    g.add_node("reason", reason)
    g.add_node("act", act)
    g.add_edge(START, "perceive")
    g.add_edge("perceive", "reason")
    g.add_edge("reason", "act")
    g.add_edge("act", END)
    return g.compile()


_GRAPH = _build_graph()


def npc_respond(world, rooms, known, npc_id, player_input, memory) -> dict:
    """Run one NPC dialogue turn. Returns a dict the UI can render."""
    final = _GRAPH.invoke(
        {
            "world": world,
            "rooms": rooms,
            "known": known,
            "memory": memory,
            "npc_id": npc_id,
            "player_input": player_input,
        }
    )
    return {
        "dialogue": final.get("dialogue", "…"),
        "result": final.get("result"),
        "completed_quests": final.get("completed_quests", []),
        "error": final.get("error"),
    }
