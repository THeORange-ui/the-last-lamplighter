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
from engine.quests import (OFFSCREEN_OBJECTIVES, QuestValidationError, add_quest,
                           build_quest, build_simple_quest, find_check_back,
                           open_request_from)
from engine.state import GroundItem
from engine.world import GRID_H, GRID_W, RIDGE_ROOMS, Room
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
        "    Offer a task. The target MUST be a real entity listed in the world briefing.\n"
        "    PICK THE TYPE BY PICTURING HOW IT ENDS — the moment they have done it, what\n"
        "    tells anyone it is done?\n"
        "      • The doing is the whole of it — go and see Tilda, reach the ridge, bring me\n"
        "        the staff — takes a concrete type (talk_to / reach / fetch / deliver /\n"
        "        interact). Those complete themselves the moment it happens.\n"
        "      • What you want is to be told something, to stop worrying, or to make up\n"
        "        your mind — put my mind at rest about Ansel, find out what his story is\n"
        "        worth, help me decide whether to stay — takes \"judged\", whose target is\n"
        "        the state of affairs YOU will weigh up, in plain words. Nothing in the\n"
        "        world can settle that but you, so YOU close it later with complete_quest\n"
        "        (your open quests' ids are in your briefing).\n"
        "    The mistake to avoid: wanting to KNOW something and asking for talk_to with\n"
        "    whoever knows it. That closes the moment they greet him — you never hear a\n"
        "    word of it, and nothing you wanted has actually happened.\n"
        "    Use judged where it honestly fits — worries and reconciliations are real asks —\n"
        "    just don't reach for it when a plain errand would do."
    ),
    "request_help": (
        '- {"type": "request_help", "quest": {\n'
        '       "title": "<short>", "description": "<one sentence>",\n'
        '       "objective": {"type": "fetch|deliver|talk_to", "target": "<item or npc id>",\n'
        '                     "npc": "<id, deliver only>"},\n'
        '       "reward": {"type": "item|affinity|info", "value": "<item you CARRY / int / fact>"}}}\n'
        "    Ask the player for one small favour — something you have lost, something that\n"
        "    needs taking to someone, someone you need spoken to. Keep it small and real to\n"
        "    your own life; you are not sending anyone up the mountain. You may only have\n"
        "    ONE favour outstanding, and if you pay in goods it comes out of your own pocket."
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
        "    Walk off to somewhere else. This ends the conversation.\n"
        "    If you are travelling WITH the player, this also means you LEAVE their\n"
        "    company — you cannot walk somewhere else and still be at their side. Only\n"
        "    use it if you truly mean to part ways. To go somewhere together, just say so\n"
        "    and let them lead; you follow them everywhere already.\n"
        "    Nobody walks up onto the ridge alone."
    ),
    "join_party": (
        '- {"type": "join_party"}\n'
        "    Go along with the player for a while — you walk together and watch out for each\n"
        "    other, and you'll be in the fight if one starts. This is an ordinary thing that\n"
        "    people do, not a vow: it means 'I'll come with you', nothing heavier. Agree to\n"
        "    it if you trust them enough for a trip and you have reason to go, or if what\n"
        "    they're doing matters to you. You don't need to like them much."
    ),
    "leave_party": (
        '- {"type": "leave_party"}\n'
        "    Stop travelling together and go your own way. No more of a big deal than\n"
        "    agreeing to it was — people part company all the time. Use it when the player\n"
        "    asks, or when you have somewhere of your own to be. Say your goodbye, and you\n"
        "    may add a move_to to walk off somewhere."
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
    "request_help_offscreen": (
        '- {"type": "request_help", "quest": {\n'
        '       "title": "<short>", "description": "<one sentence>",\n'
        '       "objective": {"type": "fetch|deliver|talk_to|judged",\n'
        '                     "target": "<item id / npc id / a plain-English criterion>",\n'
        '                     "npc": "<id, deliver only>"},\n'
        '       "reward": {"type": "item|affinity|info", "value": "<item you CARRY / int / fact>"}}}\n'
        "    Send word asking the outsider for one thing.\n"
        "    CHOOSE THE TYPE BY PICTURING HOW IT ENDS. Ask yourself: the moment they have\n"
        "    done it, what tells anyone it is done?\n"
        "      • If the doing IS the whole of it — hand me that flask, carry this to her,\n"
        "        go and look in on her — use fetch / deliver / talk_to. These close by\n"
        "        themselves, the instant the thing happens.\n"
        "      • If what you actually want is to be TOLD something, or to stop worrying,\n"
        "        or to make up your own mind — use \"judged\", and write the target as the\n"
        "        state of affairs YOU will weigh up, in plain words: \"I know what Corvin's\n"
        "        pass story is really worth\". You close it yourself later with\n"
        "        complete_quest, once they have come back and told you.\n"
        "    A common mistake: wanting to know something and asking for talk_to with the\n"
        "    person who knows it. That closes the moment they say hello to him — you never\n"
        "    hear a word of it, and nothing you wanted has happened. If you want the\n"
        "    answer, the ask is judged and the answer comes back to YOU.\n"
        "    One outstanding ask at a time, and goods come out of your own pocket."
    ),
    # --- offscreen only: the world's turn, while the player sleeps -------------
    # These never appear in a conversation. They are the whole vocabulary a character
    # has for acting on their own (engine/initiative.py, npc/nightly.py).
    "go": (
        '- {"type": "go", "room": "<room id>", "why": "<short, in your own words>"}\n'
        "    Walk somewhere overnight and stay there. Only rooms from the list you were\n"
        "    given — anywhere else is somewhere the outsider could never follow you to.\n"
        "    This is a real move: you are gone from where you were, and whoever comes\n"
        "    looking will find you where you went."
    ),
    "take": (
        '- {"type": "take", "item": "<item id>", "why": "<short>"}\n'
        "    Pick up something lying where you are and keep it. Only things actually on\n"
        "    the ground in your room. It is yours afterwards — if someone wants it they\n"
        "    will have to ask you for it."
    ),
    "leave": (
        '- {"type": "leave", "item": "<item id>", "why": "<short>"}\n'
        "    Set something down here and walk away from it, out of your own pack. It stays\n"
        "    where you put it for anyone to find."
    ),
}

# What a character may do on their own, at night, with nobody watching. Deliberately
# small: enough to change the board, never enough to settle anything. There is no verb
# for fighting, for being hurt, or for finishing a thing — the interesting half is
# supposed to happen later, with the player in the room.
#
# `use` is not here on purpose. Lighting a lamp is the obvious offscreen act for a
# lamplighter, and it is exactly the wrong one: the three lamps gate the ridge, so an
# NPC lighting them hands the player a prerequisite they were meant to earn.
#
# Asking for help is `request_help`, not `give_quest`: railed by `build_simple_quest` to
# fetch/deliver/talk_to at count 1, and limited to one outstanding ask per character by
# `open_request_from`. A note that arrives overnight, with nobody there to discuss it,
# should be a small concrete favour — and the one-at-a-time rule is what stops the world
# posting a fresh errand every time the player sleeps.
#
# Order is deliberate: `tell` renders last because it is the cheapest thing a character
# can do — it commits to nothing and leaves nothing to walk into — and in the first live
# run it crowded out every other verb, six times in five nights.
OFFSCREEN_ACTIONS = ["go", "take", "leave", "request_help", "tell"]

# Which actions each kind of character may use. A `main` character is a full agent
# (quests, companionship, the works); a `vendor` mostly trades; a `minor` throwaway
# NPC can only colour a scene and share a fact.
ACTION_SETS: dict[str, list[str]] = {
    "main": ["adjust_affinity", "give_quest", "complete_quest", "offer_item", "reveal_fact",
             "use_item", "set_goal", "resolve_goal", "tell", "move_to", "join_party",
             "leave_party", "attack", "end_dialogue"],
    # A vendor is still a person with a plan — Sella has an arc — so she gets the
    # agenda actions, but not quest-giving or companionship.
    # `request_help` too, or a vendor can ask for something concrete and have no way
    # to make it a thing the player can actually track — Sella did exactly that.
    "vendor": ["adjust_affinity", "offer_item", "reveal_fact", "use_item",
               "request_help", "set_goal", "resolve_goal", "tell", "end_dialogue"],
    # `offer_item` matters even for a throwaway NPC: without it Tilda agreed to hand over
    # bread and coins, was asked again directly, agreed again, and still could not do it —
    # the action was dropped at this gate every time. A minor promising payment they are
    # incapable of making is worse than a minor who never offers. It is safe, because
    # offer_item can only move things already in their own pocket.
    "minor": ["adjust_affinity", "offer_item", "reveal_fact", "use_item", "request_help",
              "tell", "end_dialogue"],
    # Not a character kind — the vocabulary of the world's turn. Gated through the same
    # `allowed_actions()` check as everything else, so these can never fire in dialogue
    # and a conversational action can never fire at night.
    "offscreen": OFFSCREEN_ACTIONS,
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


# Some actions want different wording in a different setting, without becoming a
# different action. A night's `request_help` may be `judged` and a minor's may not, so
# offscreen gets its own doc block for the same verb.
CATALOG_DOCS: dict[str, dict[str, str]] = {
    "offscreen": {"request_help": "request_help_offscreen"},
}


def action_catalog(kind: str = "main") -> str:
    """Render the prompt catalog with only the actions this kind is allowed to use."""
    allowed = ACTION_SETS.get(kind, ACTION_SETS["main"])
    docs = CATALOG_DOCS.get(kind, {})
    blocks = [ACTIONS[docs.get(a, a)] for a in allowed if docs.get(a, a) in ACTIONS]
    return _CATALOG_HEADER + "\n" + "\n".join(blocks) + "\n"


# Telling the player about one of these is itself the key to a lock — the engine
# records that they now know it. Mirrors engine.items.READ_FLAGS, which does the same
# when the player reads it for themselves.
FACT_FLAGS = {"sigil": "sigil_known"}

_AFFINITY_CLAMP = 25
_MAX_ACTIONS = 6
_MAX_OFFER = 10          # most of one item an NPC can hand over in a single action


@dataclass
class ActionResult:
    """What an action did, said three ways.

    `effects` is written for the player ("Wren gives you: Oil Flask"), and that is the
    only place it belongs. Writing it into the actor's own memory made Wren remember
    *being handed* her own oil, and a bystander remember being handed it too — which
    is why compacted summaries came out with everyone's deeds swapped around. Each
    audience gets its own phrasing.
    """

    effects: list[str] = field(default_factory=list)        # shown to the player
    self_effects: list[str] = field(default_factory=list)   # the actor's own memory
    observed: list[str] = field(default_factory=list)       # what onlookers remember
    debug: list[str] = field(default_factory=list)     # dropped/invalid notes
    end_dialogue: bool = False
    wants_combat: bool = False     # NPC pledged to fight as an ally
    starts_combat: bool = False    # NPC turned hostile and attacks now
    joined_party: bool = False     # NPC just joined the travelling party
    left_party: bool = False       # NPC just left the travelling party


def _note(result: ActionResult, player: str, mine: str, seen: str) -> None:
    """Record one outcome for all three audiences at once."""
    result.effects.append(player)
    result.self_effects.append(mine)
    result.observed.append(seen)


def _free_interior_tile(room: Room, blocked: set[tuple[int, int]]) -> tuple[int, int]:
    for y in range(1, GRID_H - 1):
        for x in range(1, GRID_W - 1):
            if (x, y) not in blocked:
                return (x, y)
    return (1, 1)


def apply_actions(state, npc_id, actions, known, rooms, *, as_kind: str = "") -> ActionResult:
    """Validate and apply one turn's proposed actions.

    `as_kind` overrides the character's own vocabulary. The only caller that passes it
    is the world's turn (`as_kind="offscreen"`), which needs a different set of verbs
    from the same character — the gate is the mechanism either way, so an offscreen verb
    can never fire in dialogue and a conversational one can never fire at night.
    """
    result = ActionResult()
    name = character_name(npc_id)
    if not isinstance(actions, list):
        return result

    # Gate by character kind: an action this kind may not use is dropped, never applied.
    from npc.roster import load_character
    if as_kind:
        kind = as_kind
    else:
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
            if add_quest(state, quest) is None:
                result.debug.append(f"dropped quest {quest.id!r}: already satisfied")
                continue
            state.events.record("quest_start", f"{name} gave you the quest “{quest.title}”.")
            _note(result, f"{name} gives you a quest: “{quest.title}”.",
                  f"You asked them to: “{quest.title}”.",
                  f"{name} asked the player to: “{quest.title}”.")

        elif atype == "request_help":
            if not isinstance(raw.get("quest"), dict):
                result.debug.append("request_help without quest body")
                continue
            if open_request_from(state, npc_id) is not None:
                # One favour at a time — a minor character with three open errands
                # stops being a person and becomes a quest board.
                result.debug.append(f"{npc_id} already has a favour outstanding")
                continue
            try:
                quest = build_simple_quest(
                    raw["quest"], giver=npc_id, known=known,
                    inventory=state.npcs[npc_id].inventory,
                    allowed=OFFSCREEN_OBJECTIVES if kind == "offscreen" else None)
            except QuestValidationError as e:
                result.debug.append(f"dropped request: {e}")
                continue
            if add_quest(state, quest) is None:
                result.debug.append(f"dropped request {quest.id!r}: already satisfied")
                continue
            if kind == "offscreen":
                # Nobody handed this over in person — word of it arrived overnight.
                state.events.record("quest_start",
                                    f"Word from {name}: “{quest.title}”.")
                _note(result, f"Word reached you from {name}, asking for help: "
                              f"“{quest.title}”.",
                      f"You sent word asking for help: “{quest.title}”.",
                      f"{name} sent word asking for help: “{quest.title}”.")
            else:
                state.events.record("quest_start",
                                    f"{name} asked you for help: “{quest.title}”.")
                _note(result, f"{name} asks a favour of you: “{quest.title}”.",
                      f"You asked them a favour: “{quest.title}”.",
                      f"{name} asked the player a favour: “{quest.title}”.")

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
            _note(result, f"{name} considers “{quest.title}” settled.",
                  f"You judged “{quest.title}” done, and told them so.",
                  f"{name} judged “{quest.title}” done.")

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
            _note(result, f"{name} gives you: {label}.",
                  f"You handed them: {label}.",
                  f"{name} handed the player: {label}.")

        elif atype == "reveal_fact":
            fact = str(raw.get("fact", "")).strip()
            if fact:
                state.add_fact(fact)
                # A puzzle's key is knowledge somebody holds: if what they just told you
                # covers a lock, you now know how to work it. Several characters know
                # each of these, so no one refusal can seal a path (see CLAUDE.md on
                # redundancy over scripting).
                low = fact.lower()
                for word, flag in FACT_FLAGS.items():
                    if word in low:
                        state.flags[flag] = True

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
                _note(result, f"{name} reads the {label}.",
                              f"You read the {label}.",
                              f"{name} read the {label}.")
            elif use in ("eat", "drink"):
                npc_inv.remove(item)
                verb = "eats" if use == "eat" else "drinks"
                _note(result, f"{name} {verb} the {label}.",
                              f"You {verb} the {label}.",
                              f"{name} {verb} the {label}.")
            else:
                _note(result, f"{name} turns the {label} over in their hands.",
                              f"You turned the {label} over in your hands.",
                              f"{name} turned the {label} over.")

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
                _note(result, f"{name} passes word to {', '.join(told)}.",
                      f"You passed word to {', '.join(told)}.",
                      f"{name} passed word to {', '.join(told)}.")
                # A gossip's telling doesn't stay between the two of them: it goes into
                # the public feed, which is what every NPC in town reads as rumour. Tell
                # Moss something and by morning everyone has heard a version of it.
                if load_character(npc_id).get("gossip"):
                    state.events.record(
                        "rumor", f"Word going round town: {info}", public=True)
                    _note(result, "Word of it starts going round town.",
                          "You let it out, so it will be all round town by morning.",
                          f"{name} let it out; it will be round town by morning.")

        elif atype == "move_to":
            room_id = str(raw.get("room", "")).strip()
            room = rooms.get(room_id)
            if room is None:
                result.debug.append(f"dropped move to unknown room {room_id!r}")
                continue
            if room_id in RIDGE_ROOMS:
                # Walking up the ridge alone is not a thing anyone survives, and the
                # summit is the Gloam's room. A character gets up there by travelling
                # with the player, not by announcing it and teleporting.
                result.debug.append(f"{npc_id} tried to walk off to {room_id!r} alone")
                continue
            npc = state.npcs[npc_id]
            # Walking off IS leaving: a companion who goes somewhere stops following.
            # Without this the move silently did nothing, because the party gets
            # snapped back to the player's side every frame.
            parted = state.remove_from_party(npc_id)
            if parted:
                npc.flags.pop("ally_pledged", None)
                result.left_party = True
                state.events.record("party", f"{name} left your company.", public=True)
            npc.room = room_id
            npc.x, npc.y = _free_interior_tile(room, room.blocked())
            _note(result,
                  f"{name} parts ways with you and leaves for {room.name}." if parted
                  else f"{name} leaves for {room.name}.",
                  f"You left them and went to {room.name}." if parted
                  else f"You walked off to {room.name}.",
                  f"{name} left for {room.name}.")
            result.end_dialogue = True

        elif atype == "join_party":
            if state.add_to_party(npc_id):
                state.npcs[npc_id].flags["ally_pledged"] = True   # legacy mirror
                result.joined_party = True
                result.wants_combat = True
                state.events.record("party", f"{name} joined you.", public=True)
                _note(result, f"{name} takes up beside you — a companion now.",
                      "You agreed to travel with them for a while.",
                      f"{name} agreed to travel with the player.")

        elif atype == "leave_party":
            if state.remove_from_party(npc_id):
                state.npcs[npc_id].flags.pop("ally_pledged", None)
                result.left_party = True
                result.end_dialogue = True
                state.events.record("party", f"{name} left your company.", public=True)
                _note(result, f"{name} parts ways with you.",
                      "You stopped travelling with them and went your own way.",
                      f"{name} stopped travelling with the player.")

        elif atype == "attack":
            result.starts_combat = True
            result.end_dialogue = True
            state.npcs[npc_id].flags["hostile"] = True
            _note(result, f"{name} turns on you!",
                  "You turned on them.",
                  f"{name} turned on the player!")

        elif atype == "end_dialogue":
            result.end_dialogue = True

        # --- the world's turn (offscreen only; see engine/initiative.py) ---------
        elif atype == "go":
            room_id = str(raw.get("room", "")).strip()
            room = rooms.get(room_id)
            legal = _legal_rooms(state, rooms, npc_id)
            if room is None or room_id not in legal:
                # Not a taste judgement: a room off this list is one the player has no
                # way to walk into, so going there would be vanishing, not moving.
                result.debug.append(f"dropped go to {room_id!r}: not reachable by the player")
                continue
            npc = state.npcs[npc_id]
            was = rooms[npc.room].name if npc.room in rooms else npc.room
            npc.room = room_id
            npc.x, npc.y = _free_interior_tile(room, room.blocked())
            _note(result, f"{name} is no longer at {was} — they went to {room.name}.",
                  f"You left {was} and went to {room.name}{_why(raw)}",
                  f"{name} left {was} for {room.name}.")

        elif atype == "take":
            item = str(raw.get("item", "")).strip()
            npc = state.npcs[npc_id]
            lying = next((g for g in state.ground_items_in(npc.room) if g.item == item), None)
            if lying is None:
                result.debug.append(f"dropped take {item!r}: not lying in {npc.room}")
                continue
            if _is_quest_target(state, item):
                # Pocketing the very thing an open quest asks the player to fetch would
                # leave that quest with no way to finish. They may still get in your way
                # — just not by breaking something the engine promised you could do.
                result.debug.append(f"dropped take {item!r}: an open quest needs it")
                continue
            state.ground_items.remove(lying)
            npc.inventory.append(item)
            label = display_name(item)
            here = rooms[npc.room].name if npc.room in rooms else npc.room
            _note(result, f"{name} has taken the {label} that was at {here}.",
                  f"You picked up the {label} at {here} and kept it{_why(raw)}",
                  f"{name} took the {label} from {here}.")

        elif atype == "leave":
            item = str(raw.get("item", "")).strip()
            npc = state.npcs[npc_id]
            if item not in npc.inventory:
                result.debug.append(f"dropped leave {item!r}: not carrying it")
                continue
            npc.inventory.remove(item)
            state.ground_items.append(GroundItem(room=npc.room, x=npc.x, y=npc.y, item=item))
            label = display_name(item)
            here = rooms[npc.room].name if npc.room in rooms else npc.room
            _note(result, f"{name} has left the {label} at {here}.",
                  f"You set the {label} down at {here} and left it{_why(raw)}",
                  f"{name} left the {label} at {here}.")

        else:
            result.debug.append(f"unknown action type {atype!r}")

    return result


def _why(raw: dict) -> str:
    """The actor's own stated reason, tacked onto their own memory only. It is their
    motive, not a world fact, so it never reaches the journal or anyone else's briefing.
    """
    why = str(raw.get("why", "")).strip().rstrip(".")[:160]
    return f" — {why}." if why else ""


def _is_quest_target(state, item: str) -> bool:
    return any(q.objective.target == item and q.objective.type in ("fetch", "deliver")
               for q in state.active_quests())


def _legal_rooms(state, rooms, npc_id: str) -> set[str]:
    from engine.initiative import legal_rooms
    return set(legal_rooms(state, rooms, npc_id))
