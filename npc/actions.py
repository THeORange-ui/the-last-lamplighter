"""The bounded action vocabulary an NPC may use, plus validated application.

The LLM returns a list of action objects. NONE of them touch game state until
this module validates them against KnownEntities / the rooms and applies them.
Anything malformed or ungrounded is dropped (recorded as a debug effect), never
executed. This is the guardrail that keeps emergent behavior from corrupting the
world.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.items import display_name
from engine.quests import (QuestValidationError, build_quest,
                           pending_continuation_key)
from engine.world import GRID_H, GRID_W, Room
from npc.roster import character_name

# --- the action vocabulary, one doc block per action type --------------------
# These are injected into the NPC system prompt, filtered by the character's kind
# (see ACTION_SETS / action_catalog). The engine only ever applies actions the
# character's kind is allowed to use.
ACTIONS: dict[str, str] = {
    "adjust_affinity": (
        '- {"type": "adjust_affinity", "delta": <int -25..25>, "reason": "<why>"}\n'
        "    Nudge how you feel about the player. Positive = warmer, negative = colder."
    ),
    "give_quest": (
        '- {"type": "give_quest", "quest": {\n'
        '       "title": "<short>", "description": "<one sentence>",\n'
        '       "objective": {"type": "reach|interact|fetch|deliver|talk_to",\n'
        '                     "target": "<entity>", "count": <int>, "npc": "<id, deliver only>"},\n'
        '       "reward": {"type": "item|affinity|info", "value": "<item id / int / fact>"}}}\n'
        "    Offer a task. The target MUST be a real entity listed in the world briefing."
    ),
    "offer_item": (
        '- {"type": "offer_item", "item": "<item id you are CARRYING>"}\n'
        "    Hand the player one item from your own inventory (listed in the briefing).\n"
        "    You cannot give what you do not carry."
    ),
    "reveal_fact": (
        '- {"type": "reveal_fact", "fact": "<a concrete fact you disclose>"}\n'
        "    Share something true about the world/your past. Recorded to memory."
    ),
    "move_to": (
        '- {"type": "move_to", "room": "<room id>"}\n'
        "    Leave to go somewhere. This ends the conversation."
    ),
    "join_party": (
        '- {"type": "join_party"}\n'
        "    Join the player and travel with them — you become a companion who follows\n"
        "    them through the world and fights at their side. Only if your character\n"
        "    truly would throw in their lot with them."
    ),
    "leave_party": (
        '- {"type": "leave_party"}\n'
        "    Leave the player's company and go your own way. Use this ONLY when the player\n"
        "    has asked you to part ways (or your character genuinely wants to). Say your\n"
        "    goodbye in your dialogue, and you may add a move_to to walk off somewhere."
    ),
    "attack": (
        '- {"type": "attack"}\n'
        "    Turn hostile and attack the player RIGHT NOW. Only if your character truly\n"
        "    would — this starts a fight. Use rarely and in-character."
    ),
    "end_dialogue": (
        '- {"type": "end_dialogue"}\n'
        "    End the conversation naturally."
    ),
}

# Which actions each kind of character may use. A `main` character is a full agent
# (quests, companionship, the works); a `vendor` mostly trades; a `minor` throwaway
# NPC can only colour a scene and share a fact.
ACTION_SETS: dict[str, list[str]] = {
    "main": ["adjust_affinity", "give_quest", "offer_item", "reveal_fact",
             "move_to", "join_party", "leave_party", "attack", "end_dialogue"],
    "vendor": ["adjust_affinity", "offer_item", "reveal_fact", "end_dialogue"],
    "minor": ["adjust_affinity", "reveal_fact", "end_dialogue"],
}

# Legacy action aliases → their current name (kept so older prompts/saves still work).
ACTION_ALIASES = {"join_combat": "join_party"}

_CATALOG_HEADER = (
    "You may include zero or more of these actions. Only use them when they fit what\n"
    'your character would actually do this turn. Each is a JSON object in "actions":\n'
)


def allowed_actions(kind: str) -> set[str]:
    """The set of action types a character of this kind may use (aliases resolved)."""
    return set(ACTION_SETS.get(kind, ACTION_SETS["main"]))


def action_catalog(kind: str = "main") -> str:
    """Render the prompt catalog with only the actions this kind is allowed to use."""
    allowed = ACTION_SETS.get(kind, ACTION_SETS["main"])
    blocks = [ACTIONS[a] for a in allowed if a in ACTIONS]
    return _CATALOG_HEADER + "\n" + "\n".join(blocks) + "\n"


_AFFINITY_CLAMP = 25
_MAX_ACTIONS = 6


@dataclass
class ActionResult:
    effects: list[str] = field(default_factory=list)   # player-visible lines
    debug: list[str] = field(default_factory=list)     # dropped/invalid notes
    end_dialogue: bool = False
    wants_combat: bool = False     # NPC pledged to fight as an ally
    starts_combat: bool = False    # NPC turned hostile and attacks now
    joined_party: bool = False     # NPC just joined the travelling party
    left_party: bool = False       # NPC just left the travelling party


def _free_interior_tile(room: Room, blocked: set[tuple[int, int]]) -> tuple[int, int]:
    for y in range(1, GRID_H - 1):
        for x in range(1, GRID_W - 1):
            if (x, y) not in blocked:
                return (x, y)
    return (1, 1)


def apply_actions(state, npc_id, actions, known, rooms) -> ActionResult:
    result = ActionResult()
    name = character_name(npc_id)
    if not isinstance(actions, list):
        return result

    # Gate by character kind: an action this kind may not use is dropped, never applied.
    from npc.roster import load_character
    try:
        kind = load_character(npc_id).get("kind", "main")
    except KeyError:
        kind = "main"
    allowed = allowed_actions(kind)

    for raw in actions[:_MAX_ACTIONS]:
        if not isinstance(raw, dict):
            continue
        atype = str(raw.get("type", "")).strip()
        atype = ACTION_ALIASES.get(atype, atype)   # normalize legacy names
        if atype and atype not in allowed:
            result.debug.append(f"dropped {atype!r}: not allowed for a {kind} character")
            continue

        if atype == "adjust_affinity":
            try:
                delta = int(raw.get("delta", 0))
            except (TypeError, ValueError):
                continue
            delta = max(-_AFFINITY_CLAMP, min(_AFFINITY_CLAMP, delta))
            if delta:
                state.adjust_affinity(npc_id, delta)

        elif atype == "give_quest":
            if not isinstance(raw.get("quest"), dict):
                result.debug.append("give_quest without quest body")
                continue
            # If this NPC owes a continuation, link the new quest to the one it follows.
            parent = state.flags.get(pending_continuation_key(npc_id))
            try:
                quest = build_quest(raw["quest"], giver=npc_id, known=known, parent=parent)
            except QuestValidationError as e:
                result.debug.append(f"dropped quest: {e}")
                continue
            if state.has_quest(quest.id):
                continue
            state.quests.append(quest)
            state.events.record("quest_start", f"{name} gave you the quest “{quest.title}”.")
            result.effects.append(f"{name} gives you a quest: “{quest.title}”.")

        elif atype == "offer_item":
            item = str(raw.get("item", "")).strip()
            npc_inv = state.npcs[npc_id].inventory
            if item not in known.items:
                result.debug.append(f"dropped offer of unknown item {item!r}")
                continue
            if item not in npc_inv:
                # The NPC can only give what it actually holds — no inventing items.
                result.debug.append(f"{npc_id} tried to offer {item!r} it doesn't have")
                continue
            npc_inv.remove(item)
            state.player.inventory.append(item)
            label = display_name(item)
            state.events.record("item_get", f"{name} gave you the {label}.", public=False)
            result.effects.append(f"{name} gives you: {label}.")

        elif atype == "reveal_fact":
            fact = str(raw.get("fact", "")).strip()
            if fact:
                state.add_fact(fact)

        elif atype == "move_to":
            room_id = str(raw.get("room", "")).strip()
            room = rooms.get(room_id)
            if room is None:
                result.debug.append(f"dropped move to unknown room {room_id!r}")
                continue
            npc = state.npcs[npc_id]
            npc.room = room_id
            npc.x, npc.y = _free_interior_tile(room, room.blocked())
            result.effects.append(f"{name} leaves for {room.name}.")
            result.end_dialogue = True

        elif atype == "join_party":
            if state.add_to_party(npc_id):
                state.npcs[npc_id].flags["ally_pledged"] = True   # legacy mirror
                result.joined_party = True
                result.wants_combat = True
                state.events.record("party", f"{name} joined you.", public=True)
                result.effects.append(f"{name} takes up beside you — a companion now.")

        elif atype == "leave_party":
            if state.remove_from_party(npc_id):
                state.npcs[npc_id].flags.pop("ally_pledged", None)
                result.left_party = True
                result.end_dialogue = True
                state.events.record("party", f"{name} left your company.", public=True)
                result.effects.append(f"{name} parts ways with you.")

        elif atype == "attack":
            result.starts_combat = True
            result.end_dialogue = True
            state.npcs[npc_id].flags["hostile"] = True
            result.effects.append(f"{name} turns on you!")

        elif atype == "end_dialogue":
            result.end_dialogue = True

        else:
            result.debug.append(f"unknown action type {atype!r}")

    return result
