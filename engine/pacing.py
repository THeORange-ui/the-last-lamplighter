"""How fast the world hands you things, and who it hands them to.

In play, one character's line ran away with the whole game: every conversation
advanced Wren's arc, every completion dropped a "check back" that walked the player
straight back to her, and she always had something new to ask. The other four had
plenty to say and no way to *start* anything, because starting things only ever
happened in conversation — and the player was always in a conversation with Wren.

Two mechanisms here, pulling opposite ways on the same problem:

  * the **tick** — a composite measure of progress (quests finished, nights rested,
    rooms newly seen) rather than elapsed time, so pacing tracks what the player
    actually does rather than how long they linger;
  * the **heartbeat** — on a tick, if the player isn't already carrying a full load,
    the world nudges a *neglected* character forward: it drops a "Check on Bram"
    breadcrumb, and the conversation that follows is the ordinary commissioner flow.
    Creating one costs no LLM call at all; the call happens if and when the player
    goes and knocks.

The heartbeat deliberately favours characters the player has never met, so the cast
is introduced as a drip rather than eight strangers all wanting something on day one.
"""
from __future__ import annotations

from engine.quests import CHECK_BACK, Objective, Quest, Reward

THREAD_CAP = 4          # at or above this many open threads, the world stays quiet
MIN_GAP = 2             # ticks between heartbeats, so they don't arrive in a clump
QUIET_TICKS = 2         # how long since you last spoke before someone gets restless


# --- the tick ---------------------------------------------------------------
def tick(state) -> int:
    return int(state.flags.get("tick", 0))


def bump_tick(state, kind: str = "") -> int:
    """One unit of progress happened. Returns the new tick."""
    t = tick(state) + 1
    state.flags["tick"] = t
    return t


def note_talked(state, npc_id: str) -> None:
    """Remember when the player last spoke to someone, for neglect scoring."""
    npc = state.npcs.get(npc_id)
    if npc is not None:
        npc.flags["last_talk_tick"] = tick(state)


# --- load -------------------------------------------------------------------
def open_threads(state) -> list:
    """Everything the player is currently carrying, breadcrumbs included — a
    "go and see someone" note is a thread as much as a fetch quest is."""
    return list(state.active_quests())


def thread_summary(state) -> str:
    from npc.roster import character_name
    out = []
    for q in open_threads(state):
        who = f" (for {character_name(q.giver)})" if q.giver else ""
        out.append(f"“{q.title}”{who}")
    return "; ".join(out)


# --- arc parity -------------------------------------------------------------
def beats_done(state, npc_id: str) -> int:
    npc = state.npcs.get(npc_id)
    if npc is None:
        return 0
    return sum(1 for a in npc.agenda if a.get("status") == "done")


def arc_standing(state, npc_id: str) -> tuple[int, float]:
    """(this character's closed beats, the average across everyone with an arc).

    A character who is streets ahead of the rest should ease off even when the
    player looks free — otherwise "the player has nothing on" reads as an
    invitation to pile on more, which is how one thread ate the game.
    """
    with_arcs = [nid for nid, n in state.npcs.items() if n.agenda]
    if not with_arcs:
        return (0, 0.0)
    total = sum(beats_done(state, nid) for nid in with_arcs)
    return (beats_done(state, npc_id), total / len(with_arcs))


# --- the heartbeat ----------------------------------------------------------
def _has_own_thread(state, npc_id: str) -> bool:
    """Is the player already being pointed at this person by anything?

    Not just quests they gave — one that *targets* them counts too, or the world
    would helpfully suggest visiting the person you were already on your way to see.
    """
    for q in state.active_quests():
        if q.giver == npc_id:
            return True
        o = q.objective
        if o.type in ("talk_to", CHECK_BACK) and o.target == npc_id:
            return True
        if o.type == "deliver" and o.npc == npc_id:
            return True
    return False


def _nudges(state, npc_id: str) -> int:
    return int(state.npcs[npc_id].flags.get("nudges", 0))


def _candidates(state) -> list[str]:
    """Who could use a nudge, best first: strangers before the neglected.

    Ordered by how often the world has already nudged them, so it works round the
    cast instead of pestering whoever happens to be first in the roster.
    """
    now = tick(state)
    unmet, known = [], []
    for npc_id, npc in state.npcs.items():
        if npc_id == "gloam" or _has_own_thread(state, npc_id):
            continue
        if not npc.talked_to:
            unmet.append((_nudges(state, npc_id), npc_id))
            continue
        last = int(npc.flags.get("last_talk_tick", 0))
        if now - last < QUIET_TICKS:
            continue                       # you were just with them
        from npc.agenda import open_goal
        goal = open_goal(npc)
        if goal is None:
            continue                       # nothing they're pursuing to raise
        known.append((-_nudges(state, npc_id), now - last, int(goal.get("stale", 0)),
                      npc_id))
    unmet.sort()
    known.sort(reverse=True)
    return [nid for _, nid in unmet] + [nid for *_, nid in known]


def make_heartbeat_quest(state, npc_id: str) -> Quest:
    """A visible 'go and see them' note. No parent: this is not a follow-up to
    anything the player did, which is how npc/agent.py tells the two apart."""
    from npc.roster import character_name
    name = character_name(npc_id)
    met = state.npcs[npc_id].talked_to
    desc = (f"Something seems to be weighing on {name}. It might be worth hearing it."
            if met else
            f"Word has reached you that {name} could use a hand with something.")
    return Quest(
        id=f"heartbeat__{npc_id}__{tick(state)}",
        title=f"Check on {name}",
        description=desc,
        giver=npc_id,
        objective=Objective(type=CHECK_BACK, target=npc_id, count=1),
        reward=Reward(type="affinity", value="0"),
        parent=None,
        followups=[],
    )


def heartbeat(state) -> Quest | None:
    """Maybe start something for a character the player has been neglecting.

    Returns the quest it created, or None — which is the common case, because a
    player already carrying a full load should not be handed more.
    """
    if len(open_threads(state)) >= THREAD_CAP:
        return None
    now = tick(state)
    if now - int(state.flags.get("last_heartbeat", -MIN_GAP)) < MIN_GAP:
        return None
    for npc_id in _candidates(state):
        quest = make_heartbeat_quest(state, npc_id)
        if state.has_quest(quest.id):
            continue
        state.quests.append(quest)
        state.flags["last_heartbeat"] = now
        npc = state.npcs[npc_id]
        npc.flags["nudges"] = _nudges(state, npc_id) + 1   # so it works round the cast
        state.events.record("quest_start", f"New note: {quest.title}.")
        return quest
    return None


# --- what the character is told --------------------------------------------
def restraint(state, npc_id: str) -> str:
    """How much this character should hold back right now.

    "hold"  — the player is loaded, or this arc is streets ahead of the others
    "easy"  — they have a couple of things on; ask only if it matters
    "free"  — nothing much open and this arc is not ahead; asking is natural

    Calibration matters in both directions: blanket restraint would bring back the
    stalled-arc problem this was built to fix.
    """
    threads = len(open_threads(state))
    mine, avg = arc_standing(state, npc_id)
    if threads >= THREAD_CAP or mine > avg + 0.5:
        return "hold"
    return "easy" if threads >= 2 else "free"


def prompt_block(state, npc_id: str) -> str:
    """Everything a character should know about the shape of the player's game."""
    threads = open_threads(state)
    mine, avg = arc_standing(state, npc_id)
    out = ["\n# The player's plate",
           f"They are carrying {len(threads)} open thing(s) right now"
           + (f": {thread_summary(state)}" if threads else ".")]
    if len(threads) >= THREAD_CAP:
        out.append(
            "That is a full load. Do NOT add to it — no new tasks this turn, however "
            "much you want one done. Talk, help, react; let them finish something first."
        )
    elif len(threads) >= 2:
        out.append(
            "They have enough on. Only ask for something if it genuinely cannot wait."
        )
    if mine > avg + 0.5:
        out.append(
            f"Your own story with them is further along than most people's here "
            f"({mine} of your aims settled, against {avg:.1f} on average). You have had "
            "more than your share of their time. Ease off — let them go and be somewhere "
            "else for a while. Pointing them at someone who needs them more is a "
            "perfectly good thing to do."
        )
    out.append(
        "Never hand over a task just because they look free. A person asks for help "
        "when they need it, not to fill an empty afternoon."
    )
    return "\n".join(out) + "\n"
