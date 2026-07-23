"""Bounded, checkable quest schema.

Emergent quests come from NPCs, but they must fit a fixed schema so the engine
can detect completion. Free-form title/description; structured objective + reward.

Objective types (target must resolve to a known entity):
    reach     target=room_id                 — player enters the room
    interact  target=interactable_kind        — interact with N of a kind (e.g. lamp)
    fetch     target=item_id                   — hold N of an item
    deliver   target=item_id  npc=npc_id       — give an item to an NPC
    talk_to   target=npc_id                     — speak with an NPC

Reward types:
    item      value=item_id
    affinity  value=int (added to giver's affinity)
    info      value=fact string (added to world_facts)
"""
from __future__ import annotations

from dataclasses import dataclass, field

OBJECTIVE_TYPES = {"reach", "interact", "fetch", "deliver", "talk_to"}
REWARD_TYPES = {"item", "affinity", "info"}


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

    def summary(self) -> str:
        return f"{self.title} — {self.description}"


class QuestValidationError(ValueError):
    pass


def build_quest(data: dict, giver: str, known: "KnownEntities") -> Quest:
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

    # Reward (optional; default a small affinity bump).
    rdata = data.get("reward") or {"type": "affinity", "value": "10"}
    rtype = str(rdata.get("type", "affinity"))
    rvalue = str(rdata.get("value", "10"))
    if rtype not in REWARD_TYPES:
        raise QuestValidationError(f"unknown reward type {rtype!r}")
    if rtype == "item" and rvalue not in known.items:
        raise QuestValidationError(f"reward item {rvalue!r} does not exist")

    quest_id = _slug(title)
    return Quest(
        id=quest_id,
        title=title,
        description=description,
        giver=giver,
        objective=Objective(type=otype, target=target, count=count, npc=npc),
        reward=Reward(type=rtype, value=rvalue),
    )


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


def evaluate_progress(quest: Quest, state) -> int:
    """Compute current progress toward a quest's objective from world state."""
    o = quest.objective
    if o.type == "reach":
        return o.count if state.player.room == o.target else 0
    if o.type == "interact":
        # M1 only has lamps as an interactable kind.
        if o.target == "lamp":
            return min(state.lit_lamp_count(), o.count)
        return quest.progress  # tracked incrementally by the engine
    if o.type in ("fetch", "deliver"):
        return min(state.player.inventory.count(o.target), o.count)
    if o.type == "talk_to":
        npc = state.npcs.get(o.target)
        return o.count if npc and npc.talked_to else 0
    return 0


def refresh_and_complete(state, on_complete=None) -> list[Quest]:
    """Update progress on active quests, mark completed ones, grant rewards.

    Returns the list of quests that just completed. `on_complete(quest)` is
    called for each (used by the UI to announce completion).
    """
    just_done: list[Quest] = []
    for q in state.active_quests():
        q.progress = evaluate_progress(q, state)
        if q.progress >= q.objective.count:
            q.status = "complete"
            _grant_reward(q, state)
            state.events.record("quest_complete", f"Completed the quest “{q.title}”.")
            just_done.append(q)
            if on_complete:
                on_complete(q)
    return just_done


def _grant_reward(quest: Quest, state) -> None:
    r = quest.reward
    if r.type == "item":
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
