"""The bounded action vocabulary an NPC may use, plus validated application.

The LLM returns a list of action objects. NONE of them touch game state until
this module validates them against KnownEntities / the rooms and applies them.
Anything malformed or ungrounded is dropped (recorded as a debug effect), never
executed. This is the guardrail that keeps emergent behavior from corrupting the
world.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.quests import QuestValidationError, build_quest
from engine.world import GRID_H, GRID_W, Room
from npc.roster import character_name

# Human-facing catalog injected into the NPC system prompt.
ACTION_CATALOG = """\
You may include zero or more of these actions. Only use them when they fit what
your character would actually do this turn. Each is a JSON object in "actions":

- {"type": "adjust_affinity", "delta": <int -25..25>, "reason": "<why>"}
    Nudge how you feel about the player. Positive = warmer, negative = colder.
- {"type": "give_quest", "quest": {
       "title": "<short>", "description": "<one sentence>",
       "objective": {"type": "reach|interact|fetch|deliver|talk_to",
                     "target": "<entity>", "count": <int>, "npc": "<id, deliver only>"},
       "reward": {"type": "item|affinity|info", "value": "<item id / int / fact>"}}}
    Offer a task. The target MUST be a real entity listed in the world briefing.
- {"type": "offer_item", "item": "<item id from the world briefing>"}
    Hand the player an item you plausibly have.
- {"type": "reveal_fact", "fact": "<a concrete fact you disclose>"}
    Share something true about the world/your past. Recorded to memory.
- {"type": "move_to", "room": "<room id>"}
    Leave to go somewhere. This ends the conversation.
- {"type": "join_combat"}
    Declare you'll fight beside the player. (Combat arrives in a later build.)
- {"type": "end_dialogue"}
    End the conversation naturally.
"""

_AFFINITY_CLAMP = 25
_MAX_ACTIONS = 6


@dataclass
class ActionResult:
    effects: list[str] = field(default_factory=list)   # player-visible lines
    debug: list[str] = field(default_factory=list)     # dropped/invalid notes
    end_dialogue: bool = False
    wants_combat: bool = False


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

    for raw in actions[:_MAX_ACTIONS]:
        if not isinstance(raw, dict):
            continue
        atype = str(raw.get("type", "")).strip()

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
            try:
                quest = build_quest(raw["quest"], giver=npc_id, known=known)
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
            if item not in known.items:
                result.debug.append(f"dropped offer of unknown item {item!r}")
                continue
            state.player.inventory.append(item)
            state.events.record(
                "item_get", f"{name} gave you {item.replace('_', ' ')}.", public=False
            )
            result.effects.append(f"{name} gives you: {item.replace('_', ' ')}.")

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

        elif atype == "join_combat":
            state.npcs[npc_id].flags["ally_pledged"] = True
            result.wants_combat = True
            result.effects.append(f"{name} vows to stand with you when it comes to a fight.")

        elif atype == "end_dialogue":
            result.end_dialogue = True

        else:
            result.debug.append(f"unknown action type {atype!r}")

    return result
