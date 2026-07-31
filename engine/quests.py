"""Bounded, checkable quest schema.

Emergent quests come from NPCs, but they must fit a fixed schema so the engine
can detect completion. Free-form title/description; structured objective + reward.

Objective types (target must resolve to a known entity):
    reach     target=room_id                 — player enters the room
    interact  target=interactable_kind        — interact with N of a kind (e.g. lamp)
    fetch     target=item_id                   — hold N of an item
    deliver   target=item_id  npc=npc_id       — give an item to an NPC
    talk_to   target=npc_id                     — speak with an NPC
    judged    target=<free-text criterion>      — no mechanical test exists; the giver
              decides. Some things worth asking for ("set my mind at rest about Ansel")
              are satisfied by what was said and felt, not by a counter. Progress only
              moves when the giver uses the `complete_quest` action, so this type can
              never auto-complete — and the giver is the only one who may close it.

Reward types:
    item      value=item_id
    affinity  value=int (added to giver's affinity)
    info      value=fact string (added to world_facts)
"""
from __future__ import annotations

from dataclasses import dataclass, field

OBJECTIVE_TYPES = {"reach", "interact", "fetch", "deliver", "talk_to", "judged"}
REWARD_TYPES = {"item", "affinity", "info"}

# Objective type for the breadcrumb that sends the player back to a quest-giver so
# they can decide (and hand over) the next step. Not part of OBJECTIVE_TYPES: it is
# created by the engine, never proposed by the LLM, and completed when the player
# checks back in (handled in npc/agent.py), not by progress evaluation.
CHECK_BACK = "check_back"


@dataclass
class Objective:
    type: str
    target: str
    count: int = 1
    npc: str | None = None       # for deliver


@dataclass
class Reward:
    type: str
    value: str                    # item_id / int-as-str / fact


@dataclass
class Quest:
    id: str
    title: str
    description: str
    giver: str
    objective: Objective
    reward: Reward
    status: str = "active"        # active | complete
    progress: int = 0
    parent: str | None = None     # the quest this one continues (a chain node)
    # What happens when this quest completes. Each entry is either
    #   {"kind": "decide_later"}  — the giver invents the next step when next talked to
    #   {"kind": "quest", "quest": {<quest dict>}}  — a concrete next node, activated now
    # A "decide_later" node is a leaf: it has no children (they're decided at that point).
    followups: list = field(default_factory=list)
    # The conversation counter when this was handed over. "Go and speak to Corvin" has
    # to mean a conversation *after* the asking, so `talk_to` measures against this
    # rather than against the permanent "have you two ever met" flag. See `add_quest`.
    opened_seq: int = 0

    def summary(self) -> str:
        return f"{self.title} — {self.description}"


class QuestValidationError(ValueError):
    pass


def _parse_followups(raw) -> list:
    """Normalize a quest's follow-ups. Defaults to a single 'decide_later' node so
    arcs continue by default; a 'decide_later' node is a leaf (its own children are
    only decided when it activates), so any nested children are dropped."""
    if not isinstance(raw, list) or not raw:
        return [{"kind": "decide_later"}]
    out: list = []
    for fu in raw:
        if not isinstance(fu, dict):
            continue
        kind = str(fu.get("kind", "")).strip()
        if kind == "decide_later":
            out.append({"kind": "decide_later"})
        elif kind == "quest" and isinstance(fu.get("quest"), dict):
            out.append({"kind": "quest", "quest": fu["quest"]})
    return out or [{"kind": "decide_later"}]


def build_quest(data: dict, giver: str, known: "KnownEntities",
                parent: str | None = None) -> Quest:
    """Validate an LLM-proposed quest dict against known entities.

    Raises QuestValidationError if the objective/reward can't be grounded, so
    ungrounded quests are dropped rather than becoming uncompletable.
    """
    try:
        title = str(data["title"]).strip()
        description = str(data["description"]).strip()
        obj = data["objective"]
        otype = str(obj["type"]).strip()
        target = str(obj["target"]).strip()
    except (KeyError, TypeError) as e:
        raise QuestValidationError(f"malformed quest: {e}") from e

    if not title or not description:
        raise QuestValidationError("empty title/description")
    if otype not in OBJECTIVE_TYPES:
        raise QuestValidationError(f"unknown objective type {otype!r}")

    count = int(obj.get("count", 1) or 1)
    npc = obj.get("npc")

    # Ground the target.
    if otype == "reach" and target not in known.rooms:
        raise QuestValidationError(f"reach target {target!r} is not a room")
    if otype == "interact" and target not in known.interactable_kinds:
        raise QuestValidationError(f"interact target {target!r} is not interactable")
    if otype in ("fetch", "deliver") and target not in known.items:
        raise QuestValidationError(f"item {target!r} does not exist")
    if otype == "talk_to" and target not in known.npcs:
        raise QuestValidationError(f"talk_to target {target!r} is not an NPC")
    if otype == "deliver":
        if not npc or npc not in known.npcs:
            raise QuestValidationError(f"deliver npc {npc!r} is not an NPC")
    if otype == "judged":
        # Nothing to ground against the world: the criterion is prose and the giver is
        # the judge. It still has to SAY something, or nobody can tell what was asked.
        if not target:
            raise QuestValidationError("judged quest needs a criterion as its target")
        # Count is forced to 1 — "satisfy me twice" isn't a thing.
        count = 1

    # Reward (optional; default a small affinity bump).
    rdata = data.get("reward") or {"type": "affinity", "value": "10"}
    rtype = str(rdata.get("type", "affinity"))
    rvalue = str(rdata.get("value", "10"))
    if rtype not in REWARD_TYPES:
        raise QuestValidationError(f"unknown reward type {rtype!r}")
    if rtype == "item" and rvalue not in known.items:
        raise QuestValidationError(f"reward item {rvalue!r} does not exist")

    if otype == "judged":
        # Nothing in the world closes this one, so the player has to be told that
        # coming back and saying so IS the completion — otherwise they do the thing,
        # nothing happens, and the note sits there looking broken.
        from npc.roster import character_name
        back = f"Tell {character_name(giver)} about it when you're done."
        if back not in description:
            description = f"{description} {back}".strip()

    quest_id = _slug(title)
    return Quest(
        id=quest_id,
        title=title,
        description=description,
        giver=giver,
        objective=Objective(type=otype, target=target, count=count, npc=npc),
        reward=Reward(type=rtype, value=rvalue),
        parent=parent,
        followups=_parse_followups(data.get("followups")),
    )


# What a `minor` character is allowed to ask for. No `reach` or `interact`: those are
# progression-shaped, and a throwaway NPC should never be steering the main line.
# What a `minor` may ask for, and what a night's ask may be. `judged` is in both: what
# somebody wants is often something only they can call settled — "find out whether
# Corvin's story is worth anything" has no counter that closes it, and forcing it into
# `talk_to` turns a real question into a box-tick. It is safe here because the rails
# that make an ask small are count=1 and no follow-ups, neither of which judged touches.
# It is only safe as long as the giver's kind can also use `complete_quest` — a judged
# quest whose giver cannot close it is open forever. See ACTION_SETS.
MINOR_OBJECTIVES = {"fetch", "deliver", "talk_to", "judged"}

# What an *offscreen* ask may be. Same rails, plus `judged`: a night's ask is often
# something only the asker can call settled — "find out what Corvin's pass story is
# actually worth" has no counter that closes it, and forcing it into `talk_to` made a
# real question into a box-tick. The giver closes it with `complete_quest`, whose ids
# are in their briefing.
# The night's asks are the same set. They were briefly wider (judged was allowed here
# and nowhere else); kept as its own name because the two have no reason to stay equal.
OFFSCREEN_OBJECTIVES = set(MINOR_OBJECTIVES)


def build_simple_quest(data: dict, giver: str, known: "KnownEntities",
                       inventory=(), allowed=None) -> Quest:
    """A small ask, railed hard.

    Same grounding as build_quest, then: one of a few shapes, count forced to 1, no
    follow-ups (a favour is not an arc), and a reward they can actually pay — an item
    reward has to be something in their own pocket, or it falls back to warmth.
    """
    allowed = allowed or MINOR_OBJECTIVES
    obj = dict(data.get("objective") or {})
    otype = str(obj.get("type", "")).strip()
    if otype not in allowed:
        raise QuestValidationError(
            f"{otype!r} is not something this character may ask for here")
    obj["count"] = 1

    reward = dict(data.get("reward") or {})
    if reward.get("type") == "item" and str(reward.get("value", "")) not in inventory:
        reward = {"type": "affinity", "value": "8"}

    payload = dict(data)
    payload["objective"] = obj
    payload["reward"] = reward or {"type": "affinity", "value": "8"}
    payload["followups"] = []
    quest = build_quest(payload, giver=giver, known=known)
    quest.followups = []
    return quest


def open_request_from(state, giver: str) -> "Quest | None":
    """The one open ask a minor character is allowed to have running."""
    return next((q for q in state.active_quests() if q.giver == giver), None)


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug[:40] or "quest"


@dataclass
class KnownEntities:
    """Everything a quest target is allowed to reference."""

    rooms: set[str] = field(default_factory=set)
    npcs: set[str] = field(default_factory=set)
    items: set[str] = field(default_factory=set)
    interactable_kinds: set[str] = field(default_factory=set)


def add_quest(state, quest: Quest) -> Quest | None:
    """Put a quest on the player's plate, stamped with when it was handed over.

    Refuses one that is *already* satisfied. Sella asking the player to go and price
    Corvin's tale produced a real quest that completed in the same frame it was created,
    because the player had met Corvin an hour earlier — so all the player ever saw was a
    log line, and two nights of substantive asks looked like nothing had happened.
    """
    if state.has_quest(quest.id):
        return None
    # Stamped from the conversation counter (engine/pacing.note_talked), read straight
    # off flags so quests.py doesn't have to import pacing, which imports quests.
    quest.opened_seq = int(state.flags.get("talk_seq", 0))
    if evaluate_progress(quest, state) >= quest.objective.count:
        return None          # nothing to do the moment it arrives is not a task
    state.quests.append(quest)
    return quest


def evaluate_progress(quest: Quest, state) -> int:
    """Compute current progress toward a quest's objective from world state."""
    o = quest.objective
    if o.type == "reach":
        return o.count if state.player.room == o.target else 0
    if o.type == "interact":
        from engine.interact import used_count   # local: engine.interact imports items
        return min(used_count(state, o.target), o.count)
    if o.type == "fetch":
        return min(state.player.inventory.count(o.target), o.count)
    if o.type == "deliver":
        # In whose hands? Theirs. This used to count the *player's* inventory, which
        # made deliver a byte-for-byte copy of fetch: Tilda asked for her tin locket
        # back and the quest closed the instant the player picked it up off the floor,
        # having never gone anywhere near her. The whole difference between the two
        # types is the handing over, so that is what gets counted.
        target = state.npcs.get(o.npc)
        return min(target.inventory.count(o.target), o.count) if target else 0
    if o.type == "talk_to":
        # Being asked to go and speak to someone means speaking to them *now*, not
        # having met them once before. Measured against when the quest was handed over.
        npc = state.npcs.get(o.target)
        if npc is None:
            return 0
        spoke = int(npc.flags.get("last_talk_seq", 0))
        return o.count if spoke > quest.opened_seq else 0
    if o.type == "judged":
        # Never satisfied by world state — only the giver's own `complete_quest` moves it.
        return quest.progress
    return 0


def refresh_and_complete(state, known=None, on_complete=None) -> list[Quest]:
    """Update progress on active quests, mark completed ones, grant rewards, and
    open any follow-ups.

    Returns the list of quests that just completed. `on_complete(quest)` is
    called for each (used by the UI to announce completion). `known` is needed to
    ground concrete follow-up quests; without it, only 'decide_later' nodes fire.
    """
    just_done: list[Quest] = []
    for q in state.active_quests():
        q.progress = evaluate_progress(q, state)
        if q.progress >= q.objective.count:
            q.status = "complete"
            _grant_reward(q, state)
            from engine.witness import BEAT, record_experience
            record_experience(
                state, "quest_complete", f"Completed the quest “{q.title}”.",
                room=state.player.room, salience=BEAT,
                first_person=f'You were there when the player finished "{q.title}".',
                # The giver gets their own, better-put line (npc/agent.py act, and
                # main.on_quests_completed) — don't hand them a second copy. Nor the
                # person the quest was *about*: "you were there when the player finished
                # finding the lamplighter's apprentice" is a strange thing for the
                # apprentice to remember, and compaction turned it into Wren believing
                # she had gone and found herself.
                exclude=(q.giver, q.objective.target, q.objective.npc or ""))
            _activate_followups(q, state, known)
            just_done.append(q)
            if on_complete:
                on_complete(q)
    return just_done


FAIL_AFFINITY = -8       # what calling a task off costs the player, with its giver


def fail_quest(state, quest: Quest) -> None:
    """Close a quest unhappily, because its giver has decided it is over.

    The mirror of `complete_quest`, and deliberately the *only* way a quest can fail:
    nothing in the engine times anything out. A deadline would resolve a thread while
    the player is somewhere else, which is the one thing Part 4 says not to do — content
    resolved offscreen is content nobody gets to play — and a clock that both creates
    pressure and reads world state to decide what to do about it is the shape
    `engine/pacing.py` has oscillated over twice.

    No follow-ups fire, and that needs no special case: `refresh_and_complete` only ever
    looks at active quests, and this one no longer is. A failure ends its branch of the
    tree rather than continuing it, which is the point.

    The cost is relational, because that is the kind of failure this game can express.
    A character who feels more strongly than this still has `adjust_affinity`.
    """
    quest.status = "failed"
    state.adjust_affinity(quest.giver, FAIL_AFFINITY)
    state.events.record("quest_failed", f"“{quest.title}” came to nothing.")


def make_check_back_quest(giver: str, parent_id: str) -> Quest:
    """A visible breadcrumb: 'Check back with <giver>'. Completing the parent opens
    it; talking to the giver completes it and prompts them for the next step."""
    from npc.roster import character_name  # local import: avoid engine→npc coupling
    name = character_name(giver)
    return Quest(
        id=f"check_back__{parent_id}",
        title=f"Check back with {name}",
        description=f"Return to {name} to see what comes next.",
        giver=giver,
        objective=Objective(type=CHECK_BACK, target=giver, count=1),
        reward=Reward(type="affinity", value="0"),
        parent=parent_id,
        followups=[],
    )


def find_check_back(state, giver: str):
    """The active 'check back with <giver>' breadcrumb, if any."""
    return next((q for q in state.active_quests()
                 if q.objective.type == CHECK_BACK and q.giver == giver), None)


def _activate_followups(quest: Quest, state, known) -> None:
    """When a quest completes, open its follow-ups: activate concrete child quests
    now, and for 'decide_later' nodes drop a visible 'check back with <giver>'
    breadcrumb that guides the player back so the giver can decide the next step."""
    for fu in quest.followups or []:
        kind = fu.get("kind")
        if kind == "decide_later":
            cb = make_check_back_quest(quest.giver, quest.id)
            if add_quest(state, cb) is not None:
                state.events.record("quest_start", f"New note: {cb.title}.")
        elif kind == "quest" and known is not None:
            try:
                child = build_quest(fu["quest"], giver=quest.giver, known=known,
                                    parent=quest.id)
            except QuestValidationError:
                continue
            if not state.has_quest(child.id):
                state.quests.append(child)
                state.events.record("quest_start",
                                    f"A new task opens up: “{child.title}”.")


def _grant_reward(quest: Quest, state) -> None:
    r = quest.reward
    if r.type == "item":
        # Pay out of their own pocket where they have it, so a reward is a real
        # transfer rather than an item conjured into being.
        giver = state.npcs.get(quest.giver)
        if giver is not None and r.value in giver.inventory:
            giver.inventory.remove(r.value)
        state.player.inventory.append(r.value)
    elif r.type == "affinity":
        try:
            delta = int(r.value)
        except ValueError:
            delta = 10
        if quest.giver in state.npcs:
            state.adjust_affinity(quest.giver, delta)
    elif r.type == "info":
        state.add_fact(r.value)
