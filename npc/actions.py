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
from engine.quests import QuestValidationError, build_quest, find_check_back
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
        '       "objective": {"type": "reach|interact|fetch|deliver|talk_to|judged",\n'
        '                     "target": "<entity>", "count": <int>, "npc": "<id, deliver only>"},\n'
        '       "reward": {"type": "item|affinity|info", "value": "<item id / int / fact>"}}}\n'
        "    Offer a task. The target MUST be a real entity listed in the world briefing —\n"
        "    EXCEPT for \"judged\", where the target is instead a short plain-English\n"
        "    description of what would satisfy you. Use \"judged\" when what you want can't\n"
        "    be counted or stood on (\"put my mind at rest about Ansel\"): nothing in the\n"
        "    world can decide it, so YOU decide, later, with complete_quest."
    ),
    "complete_quest": (
        '- {"type": "complete_quest", "quest_id": "<id of a quest YOU gave>", "because": "<why>"}\n'
        "    Declare a quest you gave the player finished, because you judge it so. This is\n"
        "    how a \"judged\" quest ends — when what you actually needed has happened, say so\n"
        "    and close it. You can only close your own quests, and only once."
    ),
    "offer_item": (
        '- {"type": "offer_item", "item": "<item id you are CARRYING>", "count": <int, default 1>}\n'
        "    Hand the player items from your own inventory (listed in the briefing).\n"
        "    Give the whole number they need in ONE action — if they need three flasks and\n"
        "    you have three, set count to 3. Don't make them ask three times.\n"
        "    You cannot give what you do not carry."
    ),
    "reveal_fact": (
        '- {"type": "reveal_fact", "fact": "<a concrete fact you disclose>"}\n'
        "    Share something true about the world/your past. Recorded to memory."
    ),
    "use_item": (
        '- {"type": "use_item", "item": "<item id you are CARRYING>"}\n'
        "    Use something you hold: read a map or a book, eat or drink, try a key.\n"
        "    Reading tells you what it says (you will remember it), and food is eaten up.\n"
        "    Use this when the player hands you something you'd want to look at."
    ),
    "set_goal": (
        '- {"type": "set_goal", "want": "<what you now mean to do>", "why": "<why it matters>"}\n'
        "    Change what you are working toward. Use it when your situation has shifted and\n"
        "    the thing you were pursuing is no longer the thing you care about — or when the\n"
        "    old one is done and you know what comes next. One goal at a time."
    ),
    "resolve_goal": (
        '- {"type": "resolve_goal"}\n'
        "    Declare what you were trying to do FINISHED. Only when it genuinely is."
    ),
    "tell": (
        '- {"type": "tell", "targets": ["<npc id>", ...], "info": "<what you pass on>"}\n'
        "    Pass word to other people you know — they will remember what you told them,\n"
        "    and can act on it next time the player meets them. Use it when something has\n"
        "    happened that someone else would care about, or that concerns them."
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
    "main": ["adjust_affinity", "give_quest", "complete_quest", "offer_item", "reveal_fact",
             "use_item", "set_goal", "resolve_goal", "tell", "move_to", "join_party",
             "leave_party", "attack", "end_dialogue"],
    "vendor": ["adjust_affinity", "offer_item", "reveal_fact", "use_item", "end_dialogue"],
    "minor": ["adjust_affinity", "reveal_fact", "use_item", "end_dialogue"],
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
_MAX_OFFER = 10          # most of one item an NPC can hand over in a single action


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
            # If the player is checking back, link the new quest to the one it follows.
            cb = find_check_back(state, npc_id)
            parent = cb.parent if cb else None
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

        elif atype == "complete_quest":
            qid = str(raw.get("quest_id", "")).strip()
            quest = state.quest_by_id(qid)
            if quest is None or quest.status != "active":
                result.debug.append(f"complete_quest for unknown/inactive quest {qid!r}")
                continue
            if quest.giver != npc_id:
                # Only the person who asked gets to say it's been done.
                result.debug.append(
                    f"{npc_id} tried to complete {qid!r}, which they did not give")
                continue
            # Mark it satisfied; refresh_and_complete (right after this) grants the
            # reward and opens any follow-ups, exactly as for a mechanical quest.
            quest.progress = quest.objective.count
            result.effects.append(f"{name} considers “{quest.title}” settled.")

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
            try:
                count = int(raw.get("count", 1) or 1)
            except (TypeError, ValueError):
                count = 1
            # Hand over as many as asked, capped by what they actually hold.
            count = max(1, min(count, _MAX_OFFER, npc_inv.count(item)))
            for _ in range(count):
                npc_inv.remove(item)
                state.player.inventory.append(item)
            label = display_name(item)
            if count > 1:
                label = f"{label} x{count}"
            # Anyone else in the room sees the handover — and if the object matters to
            # them, they will not forget it (engine/witness.py pins bonded items).
            from engine.witness import BEAT, record_experience
            record_experience(
                state, "item_get", f"{name} gave you the {label}.",
                room=state.npcs[npc_id].room, public=False, salience=BEAT,
                first_person=f"You saw {name} hand the player the {label}.",
                exclude=(npc_id,), bond_items=(item,))
            result.effects.append(f"{name} gives you: {label}.")

        elif atype == "reveal_fact":
            fact = str(raw.get("fact", "")).strip()
            if fact:
                state.add_fact(fact)

        elif atype == "use_item":
            from engine.items import ITEMS
            from npc.memory import NPCMemory
            item = str(raw.get("item", "")).strip()
            npc_inv = state.npcs[npc_id].inventory
            if item not in known.items or item not in npc_inv:
                result.debug.append(f"{npc_id} tried to use {item!r} it doesn't have")
                continue
            spec = ITEMS.get(item, {})
            label = display_name(item)
            use = spec.get("use")
            # NPC use is deliberately NOT engine.use_item(): that applies *player*
            # effects (healing the player, setting map_read). An NPC reading learns
            # the contents itself; food it eats is simply gone.
            if use == "read":
                text = spec.get("read_text", "")
                NPCMemory.remember_for(npc_id, f"You read the {label}. It said: {text}")
                state.events.record("npc_use", f"{name} reads the {label}.", public=False)
                result.effects.append(f"{name} reads the {label}.")
            elif use in ("eat", "drink"):
                npc_inv.remove(item)
                verb = "eats" if use == "eat" else "drinks"
                result.effects.append(f"{name} {verb} the {label}.")
            else:
                result.effects.append(f"{name} turns the {label} over in their hands.")

        elif atype == "set_goal":
            from npc import agenda
            item = agenda.set_goal(state, npc_id, raw.get("want", ""), raw.get("why", ""))
            if item is None:
                result.debug.append("set_goal with no usable 'want'")
                continue
            result.debug.append(f"{npc_id} now wants: {item['want']}")

        elif atype == "resolve_goal":
            from npc import agenda
            if not agenda.can_resolve(state.npcs[npc_id]):
                # Too soon (or nothing open) — an arc shouldn't collapse in one turn.
                result.debug.append(f"{npc_id} tried to resolve a goal too early")
                continue
            nxt = agenda.advance_agenda(state, npc_id)
            result.debug.append(
                f"{npc_id} resolved a goal; next: {nxt['want']}" if nxt
                else f"{npc_id} resolved their last authored goal")

        elif atype == "tell":
            from npc.memory import NPCMemory
            info = str(raw.get("info", "")).strip()
            targets = raw.get("targets")
            if not isinstance(targets, list):
                targets = [raw["npc"]] if raw.get("npc") else []
            told = []
            for tid in targets:
                tid = str(tid).strip()
                if tid == npc_id or tid not in known.npcs or tid not in state.npcs:
                    result.debug.append(f"tell to invalid npc {tid!r}")
                    continue
                if info:
                    NPCMemory.remember_for(tid, f"{name} told you: {info}")
                    told.append(character_name(tid))
            if told:
                result.effects.append(f"{name} passes word to {', '.join(told)}.")

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
