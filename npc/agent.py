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
from engine.quests import refresh_and_complete
from engine.state import affinity_label
from llm.client import LLMError, complete_json
from npc.actions import action_catalog, apply_actions
from npc.roster import load_character

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


def _world_briefing(world, rooms, known, npc_id) -> str:
    npc = world.npcs[npc_id]
    room = rooms[npc.room]
    lit = world.lit_lamp_count()
    total = len(world.lamps)

    lines = [
        f"Setting: {SETTING}",
        f"You are currently in: {room.name} (id: {room.id}).",
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
        lines.append("Recent happenings around town (you are aware of these):\n" + happenings)
    return "\n".join(lines)


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
Personality: {char.get('personality', '')}
Speech style: {char.get('speech_style', '')}
What drives you: {"; ".join(char.get('drives', []))}
Your backstory: {char.get('backstory', '')}
Things you know: {"; ".join(char.get('knowledge', []))}
Secrets (reveal ONLY as trust grows; never dump them): {"; ".join(char.get('secrets', []))}

# How you feel about the player right now
Affinity: {npc.affinity} out of 100 → {label}.
{_DISPOSITION_GUIDANCE.get(label, "")}

# What has happened between you before
{state['memory'].as_prompt()}

# The world (only reference things listed here)
{_world_briefing(world, state['rooms'], state['known'], npc_id)}

# Actions you can take
{action_catalog(char.get('kind', 'main'))}

# How to respond
Reply with ONE JSON object and nothing else:
{{"dialogue": "<what you SAY, in character, 1-4 sentences>", "actions": [ ... ]}}
Rules:
- Speak only as {char['name']}. Do not narrate other characters or the scene.
- Never invent people, places, or items that aren't in the briefing above.
- Keep dialogue short and natural. Use actions sparingly and only when they fit.
- Let your affinity and memories shape your tone and what you're willing to do.
"""
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
            user = (
                "The player walks up to you for the first time. Introduce yourself "
                f"naturally and briefly, in character as {char['name']}."
            )
    else:
        user = (
            f'The player says to you: "{state["player_input"]}"\n\n'
            f'Respond now as {char["name"]}.'
        )
    return system, user


# --- graph nodes -------------------------------------------------------------
def perceive(state: TurnState) -> TurnState:
    system, user = _build_prompt(state)
    return {"system": system, "user": user}


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
    return {"dialogue": dialogue, "raw_actions": actions}


def act(state: TurnState) -> TurnState:
    world, rooms, known = state["world"], state["rooms"], state["known"]
    npc_id = state["npc_id"]
    result = apply_actions(world, npc_id, state.get("raw_actions", []), known, rooms)

    # Mark that the player has spoken with this NPC (for talk_to objectives).
    world.npcs[npc_id].talked_to = True

    # Progress/complete any quests affected by these actions.
    completed = refresh_and_complete(world)

    # Write a compact memory line.
    mem = state["memory"]
    if state["player_input"] == APPROACH:
        line = f'The player approached; you said: "{state["dialogue"]}"'
    else:
        line = f'Player said: "{state["player_input"]}" | You replied: "{state["dialogue"]}"'
    if result.effects:
        line += " | " + "; ".join(result.effects)
    mem.remember(line)

    # If a quest THIS npc gave just completed, they personally remember it.
    for q in completed:
        if q.giver == npc_id:
            mem.remember(f'The player completed the quest you gave them: "{q.title}".')

    # Compact the log if it's grown long (runs here in the dialogue worker thread).
    mem.maybe_compact(lambda prior, old: summarize_memory(npc_id, prior, old))

    return {"result": result, "completed_quests": completed}


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
