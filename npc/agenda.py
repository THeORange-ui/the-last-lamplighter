"""What a character is trying to do *next* — the thing the prompt was missing.

An NPC's prompt already carried who they are (personality, drives), what has
happened (memory), and how they feel (affinity). None of that says what they *want
this week*, so a model with nothing to pursue defaults to being pleasant and
waiting — which is why quests felt one-off and arcs never advanced. The one place
arcs did reliably continue was the commissioner block in `agent.py`, the one place
we said "decide the next step". An agenda generalizes that to every turn.

Each `main` character's file carries an ordered `agenda`: an arc skeleton of 3-4
beats. Only one is ever open. When it resolves the engine seeds the next authored
beat (`advance_agenda`), so arcs progress and eventually *end* rather than dripping
errands forever — and when the authored beats run out the character names its own
via the `set_goal` action.

Reliability comes from the NPC reporting `goal_progress` each turn:
    "advanced"  clears the stale counter
    "none"      increments it; at MAX_STALE the prompt tells them to press it
    "resolved"  closes the beat and opens the next
So a goal can stall, but never silently. `MIN_TURNS` stops a character from
declaring a beat finished on the same turn it opened, which would let a whole arc
evaporate in four greetings.
"""
from __future__ import annotations

MAX_STALE = 2          # turns of "none" before the prompt escalates
MIN_TURNS = 1          # a goal must be open this many turns before it can resolve
_MAX_WANT = 200        # chars; keeps an LLM-authored goal to one sentence
_MAX_ITEMS = 12        # per-NPC agenda history cap


def _stages(npc_id: str) -> list[dict]:
    from npc.roster import load_character
    try:
        stages = load_character(npc_id).get("agenda") or []
    except KeyError:
        return []
    return [s for s in stages if isinstance(s, dict) and s.get("want")]


def _item(src: dict, stage: int) -> dict:
    return {
        "id": str(src.get("id") or f"stage_{stage}"),
        "want": str(src.get("want", "")).strip()[:_MAX_WANT],
        "why": str(src.get("why", "")).strip(),
        "status": "open",
        "stage": stage,
        "stale": 0,
        "turns": 0,
    }


def open_goal(npc) -> dict | None:
    """The one open agenda item, if any."""
    return next((a for a in getattr(npc, "agenda", []) if a.get("status") == "open"), None)


def seed_agenda(state, npc_id: str) -> dict | None:
    """Give a character its first authored beat, if it has an agenda and no goal yet."""
    npc = state.npcs.get(npc_id)
    if npc is None or npc.agenda:
        return None
    stages = _stages(npc_id)
    if not stages:
        return None
    npc.agenda = [_item(stages[0], stage=0)]
    return npc.agenda[0]


def seed_all(state) -> None:
    for npc_id in state.npcs:
        seed_agenda(state, npc_id)


def advance_agenda(state, npc_id: str) -> dict | None:
    """Close the open beat and open the next authored one. Returns it, or None when
    the arc's authored beats are spent (the character may then name its own)."""
    npc = state.npcs.get(npc_id)
    if npc is None:
        return None
    cur = open_goal(npc)
    if cur is None:
        return None
    cur["status"] = "done"
    stages = _stages(npc_id)
    nxt = int(cur.get("stage", 0)) + 1
    if nxt < len(stages):
        item = _item(stages[nxt], stage=nxt)
        npc.agenda.append(item)
        del npc.agenda[:-_MAX_ITEMS]
        return item
    return None


def can_resolve(npc) -> bool:
    """A beat has to have been open for a turn before it can be declared finished —
    unless the thing they actually asked for has been done, which is proof enough."""
    goal = open_goal(npc)
    if goal is None:
        return False
    return int(goal.get("turns", 0)) >= MIN_TURNS or bool(goal.get("delivered"))


def note_quest_done(state, giver: str, title: str) -> None:
    """A quest this character gave has been completed.

    This is the strongest evidence there is that their current beat has moved, and
    without it they don't reliably notice: in play, Wren sat on "get the lamps lit"
    for eight turns after all three were burning, because nothing ever put the fact
    in front of her at the moment it mattered.
    """
    npc = state.npcs.get(giver)
    goal = open_goal(npc) if npc is not None else None
    if goal is None:
        return
    goal["stale"] = 0
    done = list(goal.get("delivered") or [])
    if title not in done:
        done.append(title)
    goal["delivered"] = done[-3:]


def set_goal(state, npc_id: str, want: str, why: str = "") -> dict | None:
    """A character naming its own next goal (the `set_goal` action). Replaces the
    open beat, keeping its stage so authored staging still lines up afterwards."""
    npc = state.npcs.get(npc_id)
    want = str(want or "").strip()[:_MAX_WANT]
    if npc is None or not want:
        return None
    cur = open_goal(npc)
    stage = int(cur.get("stage", 0)) if cur else 0
    if cur is not None:
        cur["status"] = "done"
    item = _item({"id": f"own_{len(npc.agenda)}", "want": want, "why": why}, stage=stage)
    npc.agenda.append(item)
    del npc.agenda[:-_MAX_ITEMS]
    return item


def note_progress(state, npc_id: str, progress: str) -> dict | None:
    """Fold in the NPC's self-report. Returns the newly opened beat, if one opened."""
    npc = state.npcs.get(npc_id)
    if npc is None:
        return None
    goal = open_goal(npc)
    if goal is None:
        return None
    progress = str(progress or "").strip().lower()
    if progress == "resolved" and can_resolve(npc):
        return advance_agenda(state, npc_id)
    if progress == "advanced":
        goal["stale"] = 0
    elif progress == "none":
        goal["stale"] = int(goal.get("stale", 0)) + 1
    return None


def tick_turn(state, npc_id: str) -> None:
    """Count a conversation turn against the open beat (gates MIN_TURNS)."""
    npc = state.npcs.get(npc_id)
    goal = open_goal(npc) if npc is not None else None
    if goal is not None:
        goal["turns"] = int(goal.get("turns", 0)) + 1


def prompt_block(state, npc_id: str) -> str:
    """The `# What you are trying to do right now` section of the system prompt."""
    npc = state.npcs.get(npc_id)
    goal = open_goal(npc) if npc is not None else None
    if goal is None:
        return ""
    out = ["\n# What you are trying to do right now", goal["want"]]
    if goal.get("why"):
        out.append(f"Why it matters to you: {goal['why']}")
    out.append(
        "Pursue this. Work it into the conversation where it fits — if the player could "
        "help, ask them for it plainly (use give_quest when it's a concrete task). If it "
        "is genuinely done, say so and name what you turn to next (resolve_goal, or "
        "set_goal for something of your own). Do not announce it as an objective; you are "
        "a person with something on your plate, not a quest dispenser."
    )
    delivered = goal.get("delivered") or []
    if delivered:
        out.append(
            "IT HAS BEEN DONE. The player finished what you asked for: "
            + "; ".join(f"“{t}”" for t in delivered)
            + ". Decide honestly whether that completes what you were after. If it does, "
            "say so and resolve_goal — then tell them what you turn to next, and ask for "
            "it if you need their hands for it. Do not keep asking for a thing you have "
            "already been given."
        )
    elif int(goal.get("stale", 0)) >= MAX_STALE:
        out.append(
            "You have been circling this for several conversations without getting "
            "anywhere. Press it THIS turn — ask outright for what you need."
        )
    return "\n".join(out) + "\n"
