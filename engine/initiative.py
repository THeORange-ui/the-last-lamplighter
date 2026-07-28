"""The world's turn: who acts while the player sleeps, and what they may do about it.

Everything else in Emberhold happens because the player is standing there. This is the
one place it doesn't — it runs inside the night (ui/night.py), which is the only cut the
game has. That containment is the whole safety story: initiative can't fire twice in a
room, can't cascade, and can't surprise the player mid-step.

Two rules shape every decision here, and neither is negotiable:

**Every act must leave a way in.** A character may take what you wanted, walk off with
your errand, or get in your way — the verbs need no other limit, because each one is
required to end with something the player can reach. That is the quest-grounding
invariant pointed at a new target. It is enforced *before* applying rather than by
rolling back: `legal_rooms()` only ever offers findable destinations, and only
characters standing somewhere findable get to act at all, so a validated action is a
discoverable one by construction.

**The offscreen act is thin; the onscreen consequence is thick.** No journeys simulated
step by step, no fights resolved where nobody is watching, no deaths. A character goes
somewhere and is then *there*, in a state, with something unresolved. Everything
interesting about that happens later, with the player present. Anything settled out here
is content nobody gets to play.

The pacing module (engine/pacing.py) is the cautionary tale this is written against: it
generates content *and* reads world state to decide whether to generate more, and it
oscillated twice. So the cap here is hard, it lives inside the rest, and nothing
initiative creates is an input to whether initiative fires again.
"""
from __future__ import annotations

from engine.world import NPC_SPAWNS, PRIVATE_ROOMS, ridge_open

MAX_ACTORS = 2          # characters who may act in one night. Hard cap, never scaled.
MIN_PRESSURE = 2        # below this nobody has enough reason to bother
NIGHT_COOLDOWN = 2      # nights before the same person may act again

# The Gloam's room. You get up there by walking it with the player, not by anyone
# announcing it and turning up. (The rest of the ridge is fair game once it's open.)
FORBIDDEN_ROOMS = {"ridge_summit"}


# --- who is even findable ---------------------------------------------------
def findable_rooms(world, rooms) -> set[str]:
    """Rooms the player could plausibly walk into: ones they have already seen, plus
    anywhere one door beyond those. Word doesn't reach you from a corner you have never
    walked, and a character who moves somewhere you cannot follow has vanished rather
    than gone somewhere — the same principle engine/pacing.py picks its candidates by.
    """
    seen = set(world.flags.get("visited") or [])
    out = set(seen)
    for rid in seen:
        room = rooms.get(rid)
        if room is None:
            continue
        out.update(d.to_room for d in room.doors)
    return out


def legal_rooms(world, rooms, npc_id: str) -> list[str]:
    """Where this character may go tonight. The prompt is built from exactly this list,
    so the model is choosing among valid options rather than being corrected afterwards.
    """
    npc = world.npcs.get(npc_id)
    if npc is None:
        return []
    home = (NPC_SPAWNS.get(npc_id) or (None,))[0]
    reachable = findable_rooms(world, rooms)
    open_ridge = ridge_open(world)
    out = []
    for rid, room in rooms.items():
        if rid == npc.room or rid in FORBIDDEN_ROOMS or rid not in reachable:
            continue
        if rid in PRIVATE_ROOMS and rid != home:
            continue                       # nobody drifts into someone else's house
        if room.biome == "snow" and not open_ridge:
            continue                       # the climb is shut; going there is vanishing
        out.append(rid)
    return out


# --- who wants to act -------------------------------------------------------
def pressure(world, npc_id: str) -> int:
    """How badly this character needs to stop waiting and do something themselves.

    Built from what the game already tracks: a beat they keep failing to move, and how
    long since the player last gave them any attention. Deliberately not a new metric.
    """
    from npc.agenda import open_goal
    from engine import pacing
    npc = world.npcs.get(npc_id)
    goal = open_goal(npc) if npc is not None else None
    if goal is None:
        return 0                            # nothing to pursue; nothing to pursue it with
    stale = int(goal.get("stale", 0))
    quiet = pacing.tick(world) - int(npc.flags.get("last_talk_tick", 0))
    unmet = 0 if npc.talked_to else 1
    return stale + (quiet // pacing.MIN_GAP) + unmet


def candidates(world, rooms) -> list[str]:
    """Who acts tonight, most pressed first.

    Party members are excluded on purpose: they are with you, so their arc is already
    advancing alongside you. That exclusion is what makes *who you travel with* a real
    choice — the people you leave behind are the ones whose stories move without you.

    Ordering breaks ties by how often someone has already had a night, because pressure
    alone is nearly static and picked the same two people five nights running. This
    codebase has shipped that exact bug before, in the pacing heartbeat.
    """
    findable = findable_rooms(world, rooms)
    scored = []
    for npc_id, npc in world.npcs.items():
        if npc_id == "gloam" or world.in_party(npc_id):
            continue
        if npc.room == world.player.room:
            continue                        # they are standing right next to you
        if npc.room not in findable:
            continue                        # whatever they did, you could never find it
        last = npc.flags.get("last_night")
        if last is not None and world.day - int(last) < NIGHT_COOLDOWN:
            continue                        # they had their night; let someone else have one
        score = pressure(world, npc_id)
        if score >= MIN_PRESSURE:
            scored.append((-score, int(npc.flags.get("nights_acted", 0)), npc_id))
    scored.sort()
    return [npc_id for *_, npc_id in scored[:MAX_ACTORS]]


def note_asked(world, npc_id: str) -> None:
    """They were given the night and chose to sleep through it. That still spends their
    turn — otherwise a character who always declines sits at the top of the queue
    forever, taking a slot from everyone else every single night. Perrin did exactly
    that five nights running."""
    world.npcs[npc_id].flags["last_night"] = world.day


def note_acted(world, npc_id: str) -> None:
    """They did something about it tonight, so they are neither owed a turn nor still
    stuck. Resetting `stale` matters: acting IS the beat moving, and leaving the counter
    high would keep them at the top of the queue for doing something about it."""
    from npc.agenda import open_goal
    npc = world.npcs[npc_id]
    note_asked(world, npc_id)
    npc.flags["nights_acted"] = int(npc.flags.get("nights_acted", 0)) + 1
    goal = open_goal(npc)
    if goal is not None:
        goal["stale"] = 0


# --- running the night ------------------------------------------------------
def run_night(world, rooms, known) -> list[str]:
    """Let up to MAX_ACTORS characters act. Returns third-person lines describing what
    the world did, for the night's narration and the journal.

    Errors are swallowed per character: a night that half-happens is fine, a night that
    takes the game down with it is not.
    """
    from npc.nightly import decide_night
    reports: list[str] = []
    for npc_id in candidates(world, rooms):
        note_asked(world, npc_id)           # asked is spent, whatever they answer
        try:
            lines = decide_night(world, rooms, known, npc_id)
        except Exception:                   # noqa: BLE001 — never let a night crash
            continue
        reports.extend(lines)
    world.flags["last_night_reports"] = reports
    return reports
