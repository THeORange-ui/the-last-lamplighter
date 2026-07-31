"""Turning things that happen into things characters have *experienced*.

Before this, every notable event went into one log and was broadcast to every NPC
through `EventLog.public_briefing()` — so nobody could tell "I was there" from "I
heard about it". Now one function does both jobs:

    record_experience(...)  ->  the journal + the rumor feed  (as before)
                            ->  a first-person memory for everyone who was present

Non-witnesses learn only through the `tell` action, through the public feed as
rumor, or from the player's own mouth. That distinction is most of what makes the
world feel inhabited rather than announced.

Two guards keep memory from drowning, which matters because the prompt only shows
PROMPT_ENTRIES (12) recent lines:
  * `salience` — below WITNESS_MIN an event is journal-only and nobody remembers it.
  * `once_key` — dedupes per character, so walking into the cellar for the fortieth
    time doesn't write a fortieth memory.

A bonded object (see npc/bonds.py) is the exception worth spending permanence on:
if something happens to a thing a character has a bond with, that memory is
*pinned*, so compaction can never take it away.
"""
from __future__ import annotations

# Salience tiers, for call sites to name rather than pass bare ints.
AMBIENT = 0      # journal only — nobody carries this around
NOTE = 1         # ordinary: entering somewhere new, lighting a lamp, resting
BEAT = 2         # a real event: a quest done, an item changing hands, a fight
MAJOR = 3        # the ones that define an arc: a bonded object surfacing, a death

WITNESS_MIN = NOTE      # below this, an event writes no memories


def witnesses(state, room: str, exclude=()) -> list[str]:
    """Everyone who experienced something in `room`: whoever is standing there, plus
    the party, who travel at the player's shoulder."""
    out = [nid for nid, n in state.npcs.items()
           if n.room == room and nid not in exclude]
    for nid in state.party:                     # belt and braces if a follower
        if nid not in out and nid not in exclude:   # hasn't been repositioned yet
            out.append(nid)
    return out


def _seen(state, npc_id: str) -> dict:
    """Per-NPC record of once-only experiences (json-safe, so it saves)."""
    return state.npcs[npc_id].flags.setdefault("seen", {})


def has_seen(state, npc_id: str, once_key: str) -> bool:
    return bool(_seen(state, npc_id).get(once_key))


def record_experience(state, kind: str, text: str, *, room: str | None = None,
                      first_person: str | None = None, salience: int = NOTE,
                      public: bool = True, once_key: str | None = None,
                      targets=None, exclude=(), bond_items=(), npc_text: str = "",
                      actor: str = ""):
    """Log an event, and write it into the memory of those who were there.

    `text` is the player's journal line. `npc_text` is the same event as another
    character would hear it — pass it whenever `text` is written to the player, since
    "you" means the character on that side. `first_person` is what a witness remembers
    ("You watched the player..."). `targets` overrides the witness set when the
    experience isn't simply "everyone in the room" — room entry, for instance, is
    something the *party* did, not the residents who saw it.
    """
    ev = state.events.record(kind, text, public=public, room=room, salience=salience,
                             npc_text=npc_text, actor=actor)
    if not first_person or salience < WITNESS_MIN:
        return ev

    who = list(targets) if targets is not None else (
        witnesses(state, room, exclude) if room else [])

    from npc.bonds import bond_for          # local: engine shouldn't import npc eagerly
    from npc.memory import NPCMemory
    for npc_id in who:
        if npc_id not in state.npcs:
            continue
        if once_key and has_seen(state, npc_id, once_key):
            continue
        mem = NPCMemory.remember_for(npc_id, first_person)
        # Something happening to a thing they care about is worth permanence.
        if any(bond_for(npc_id, "item", item) for item in bond_items):
            mem.pin(first_person)
        if once_key:
            _seen(state, npc_id)[once_key] = True
    return ev
