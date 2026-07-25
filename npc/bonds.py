"""What a character *cares* about — the weights that make world events land.

A bond is a declared attachment to a thing in the world:

    {"kind": "item"|"npc"|"room"|"topic", "ref": <id or word>, "weight": 1-3, "note": ...}

This is how Ansel's staff becomes central to the story without a single line of
staff-specific code. The staff is an object with weight, and weight is data: pick it
up in front of Wren and it lands as a pinned memory (engine/witness.py); carry it
into a conversation and her briefing tells her it's here, in her own words from the
`note`. Phase C uses the same scores to decide when a companion is moved enough to
interject, so the expensive path is only taken when a cheap check has already passed.

`topic` bonds match loose text (a name, a subject) rather than an id, so "ansel"
fires on "Ansel's staff" and on someone mentioning him out loud.
"""
from __future__ import annotations

from functools import lru_cache

_MAX_WEIGHT = 3
NAMED_BOOST = 2          # being addressed by name is inherently relevant


@lru_cache(maxsize=None)
def bonds_for(npc_id: str) -> tuple[dict, ...]:
    """This character's bonds, normalized. Cached: character files are static."""
    from npc.roster import load_character
    try:
        raw = load_character(npc_id).get("bonds") or []
    except KeyError:
        return ()
    out: list[dict] = []
    for b in raw:
        if not isinstance(b, dict):
            continue
        kind = str(b.get("kind", "")).strip().lower()
        ref = str(b.get("ref", "")).strip()
        if not kind or not ref:
            continue
        try:
            weight = int(b.get("weight", 1) or 1)
        except (TypeError, ValueError):
            weight = 1
        out.append({"kind": kind, "ref": ref,
                    "weight": max(1, min(_MAX_WEIGHT, weight)),
                    "note": str(b.get("note", "")).strip()})
    return tuple(out)


def bond_for(npc_id: str, kind: str, ref: str) -> dict | None:
    """This character's bond with one specific thing, if they have one."""
    ref = (ref or "").strip().lower()
    return next((b for b in bonds_for(npc_id)
                 if b["kind"] == kind and b["ref"].lower() == ref), None)


def relevance(npc_id: str, *, room: str | None = None, items=(), npcs=(),
              text: str = "") -> int:
    """How much a beat concerns this character: the summed weight of what it touches.

    `text` is scanned for `topic` bonds (and for the character's own name, which is
    always relevant to them).
    """
    lowered = (text or "").lower()
    score = 0
    for b in bonds_for(npc_id):
        kind, ref = b["kind"], b["ref"]
        if kind == "item" and ref in items:
            score += b["weight"]
        elif kind == "npc" and ref in npcs:
            score += b["weight"]
        elif kind == "room" and room == ref:
            score += b["weight"]
        elif kind == "topic" and ref.lower() in lowered:
            score += b["weight"]
    if lowered:
        from npc.roster import character_name
        if character_name(npc_id).lower() in lowered:
            score += NAMED_BOOST
    return score


def notes_here(npc_id: str, *, room: str | None = None, items=(), npcs=()) -> list[str]:
    """The character's own words about whichever of their attachments are present —
    fed into their briefing so the world reaches them in their own voice."""
    out: list[str] = []
    for b in bonds_for(npc_id):
        kind, ref, note = b["kind"], b["ref"], b["note"]
        if not note:
            continue
        if kind == "item" and ref in items:
            out.append(f"{note} (it is here, right now)")
        elif kind == "npc" and ref in npcs:
            out.append(note)
        elif kind == "room" and room == ref:
            out.append(f"{note} (you are standing in it)")
    return out
