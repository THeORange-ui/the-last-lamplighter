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

**It is easy to overdo, and overdoing it is worse than not doing it.** The first
calibration counted every newly-seen room as one whole unit of progress and let a
heartbeat fire every second unit, so ten minutes of exploring the twenty-room map
produced four "Check on X" notes. Worse, those notes counted as *load*, so every
character the player then met believed they were snowed under and politely declined
to ask for anything — the world filled up with errands to go and be told nothing.
Hence: progress is **weighted** (a finished quest is worth three rooms), a note is
**not** work and is excluded from the load count, and only one may be outstanding.
"""
from __future__ import annotations

from engine.quests import CHECK_BACK, Objective, Quest, Reward

THREAD_CAP = 4          # real, workable threads before the world stays quiet
MIN_GAP = 6             # progress *points* between heartbeats (see WEIGHTS)
QUIET_TICKS = 4         # points since you last spoke before someone gets restless
MAX_OPEN_NOTES = 1      # outstanding "go and see someone" notes allowed at once

# Not all progress is equal. Walking into a new room is a step; finishing something
# somebody asked of you is an event. Counting them the same made exploration alone
# drive the whole pacing system.
WEIGHTS = {"quest": 3, "rest": 2, "room": 1}


# --- the tick ---------------------------------------------------------------
def tick(state) -> int:
    return int(state.flags.get("tick", 0))


def bump_tick(state, kind: str = "") -> int:
    """Progress happened, worth `WEIGHTS[kind]` points. Returns the new total."""
    t = tick(state) + WEIGHTS.get(kind, 1)
    state.flags["tick"] = t
    return t


def note_talked(state, npc_id: str) -> None:
    """Remember when the player last spoke to someone, for neglect scoring."""
    npc = state.npcs.get(npc_id)
    if npc is not None:
        npc.flags["last_talk_tick"] = tick(state)


# --- load -------------------------------------------------------------------
def open_threads(state) -> list:
    """Actual *work* the player is carrying — errands, journeys, things to fetch.

    Notes to go and see somebody are deliberately not counted. They used to be, and
    it fed back on itself viciously: the world dropped a note, the note counted as
    load, the load told everyone to hold off asking, so the only things left open
    were notes — and each one the player cleared freed the slot for the next.
    """
    return [q for q in state.active_quests() if q.objective.type != CHECK_BACK]


def open_notes(state) -> list:
    """The 'go and see someone' notes: heartbeats and check-back breadcrumbs alike.
    Not work, but still something the player is holding, so it caps itself."""
    return [q for q in state.active_quests() if q.objective.type == CHECK_BACK]


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
    seen_rooms = set(state.flags.get("visited") or [])
    unmet, known = [], []
    for npc_id, npc in state.npcs.items():
        if npc_id == "gloam" or _has_own_thread(state, npc_id):
            continue
        if npc.room not in seen_rooms:
            continue        # word doesn't reach you from a corner you've never walked
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
    if len(open_notes(state)) >= MAX_OPEN_NOTES:
        return None          # already owed a visit; don't stack another on top
    now = tick(state)
    # Default 0, not -MIN_GAP: the very first step into the world should not come
    # with a note attached before the player has met a single person.
    if now - int(state.flags.get("last_heartbeat", 0)) < MIN_GAP:
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
    stalled-arc problem this was built to fix. This is an *engine* judgement — it
    steers how hard the prompt leans, and is never shown to the character as a tally
    of what the player is carrying (see `prompt_block`).
    """
    threads = len(open_threads(state))
    mine, avg = arc_standing(state, npc_id)
    if threads >= THREAD_CAP or mine > avg + 0.5:
        return "hold"
    return "easy" if threads >= 3 else "free"


def prompt_block(state, npc_id: str) -> str:
    """What a character should know about their own position — and nothing more.

    This block used to list the player's open quests by name, so that the character
    could weigh their load. They weighed it out loud: "though you've already got
    Wren's lamps to mind". A townsman who recites your quest log is not a person, and
    the knowledge wasn't his to have. Restraint is still applied — it is just felt
    from this side of the conversation, as the character's own reticence, and the
    engine keeps the arithmetic to itself.
    """
    mine, avg = arc_standing(state, npc_id)
    out = ["\n# Asking things of them"]
    if len(open_threads(state)) >= THREAD_CAP:
        out.append(
            "Whatever is on your mind can keep for now. Do not set them a task this "
            "turn — talk, help, react. Not everything has to be asked today."
        )
    if mine > avg + 0.5:
        out.append(
            "You have already had more than your share of this person's time. Ease "
            "off — let them go and be somewhere else for a while. Pointing them at "
            "someone who needs them more is a perfectly good thing to do."
        )
    out.append(
        "Never hand over a task just because they look free. A person asks for help "
        "when they need it, not to fill an empty afternoon."
    )
    out.append(
        "You have no idea what else they have promised anyone. You know only what "
        "you have seen yourself or been told. Do not mention, tally, or make "
        "allowances for errands other people have set them."
    )
    return "\n".join(out) + "\n"
