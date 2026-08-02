"""Letting the people with you speak up when something actually touches them.

The hard part isn't producing a remark, it's deciding *when* — a companion who
comments on every doorway is worse than one who never speaks. So the decision is
made in two stages, and the expensive one only runs if the cheap one passes:

  1. A **free, rule-based gate** (`choose_interjector`): does this beat touch
     something the character has a bond with (npc/bonds.py)? Are they off cooldown?
     Have they already remarked on this exact thing before? No LLM call is made
     unless all three pass, so walking through the market costs nothing.
  2. **One short LLM call** (`interject`) for the single most-moved character, who
     gets one line. No actions — a bark can't change the world, so it can't go
     wrong in any way the engine would have to defend against.

Cooldowns are counted in *beats* rather than seconds, so a player who runs through
six rooms doesn't get six remarks; and `once_key` means Wren says something the
first time she stands at the foot of the ridge, not every time she passes through.
"""
from __future__ import annotations

from llm.client import LLMError, complete_json
from npc.agenda import open_goal
from npc.bonds import bonds_for, relevance
from npc.roster import load_character

MIN_RELEVANCE = 2        # bond weight needed before anyone opens their mouth
BEAT_COOLDOWN = 5        # beats one character must let pass before speaking again
_MAX_LINE = 220


def _cooldown_ok(world, npc_id: str, beat_no: int) -> bool:
    last = world.npcs[npc_id].flags.get("last_bark")
    return last is None or (beat_no - int(last)) >= BEAT_COOLDOWN


def _already_said(world, npc_id: str, once_key: str) -> bool:
    return bool(world.npcs[npc_id].flags.get("seen", {}).get(f"bark:{once_key}"))


def _mark(world, npc_id: str, beat: dict) -> None:
    npc = world.npcs[npc_id]
    npc.flags["last_bark"] = beat["n"]
    npc.flags.setdefault("seen", {})[f"bark:{beat['once_key']}"] = True


def candidates(world, room: str) -> list[str]:
    """Who could speak: whoever is here. Companions travel with you, so in practice
    it's usually them — but a resident standing in the room counts too."""
    return [nid for nid, n in world.npcs.items() if n.room == room]


def choose_interjector(world, beat: dict) -> str | None:
    """The free gate. Returns whoever is most moved by this beat, or None — which is
    the common case, and costs nothing."""
    best, best_score = None, 0
    for npc_id in candidates(world, beat["room"]):
        if not bonds_for(npc_id):
            continue
        if not _cooldown_ok(world, npc_id, beat["n"]):
            continue
        if _already_said(world, npc_id, beat["once_key"]):
            continue
        score = relevance(npc_id, room=beat["room"], items=beat["items"],
                          npcs=beat["npcs"], text=beat["text"])
        if score >= MIN_RELEVANCE and score > best_score:
            best, best_score = npc_id, score
    return best


def _why_block(npc_id: str, beat: dict) -> str:
    """The character's own words about whatever it is that just landed."""
    notes = []
    for b in bonds_for(npc_id):
        if not b["note"]:
            continue
        if ((b["kind"] == "item" and b["ref"] in beat["items"])
                or (b["kind"] == "room" and b["ref"] == beat["room"])
                or (b["kind"] == "npc" and b["ref"] in beat["npcs"])
                or (b["kind"] == "topic" and b["ref"].lower() in beat["text"].lower())):
            notes.append(b["note"])
    return "\n".join(f"- {n}" for n in notes)


def interject(world, npc_id: str, beat: dict, memory) -> str:
    """One short line, in character. Returns "" if the model has nothing or errors —
    silence is always an acceptable outcome here."""
    try:
        char = load_character(npc_id)
    except KeyError:
        return ""
    goal = open_goal(world.npcs[npc_id])
    system = (
        f"You are {char['name']} — {char.get('role', '')}.\n"
        f"Personality: {char.get('personality', '')}\n"
        f"Speech style: {char.get('speech_style', '')}\n"
        + (f"What you are trying to do: {goal['want']}\n" if goal else "")
        + "\nSomething just happened in front of you that touches something you care "
          "about:\n" + _why_block(npc_id, beat)
        + "\n\nSay ONE short line out loud about it — a remark to the person you are "
          "travelling with, not a speech. Under 25 words. It can be quiet, or wary, or "
          "barely relevant; people mostly say small things. If you would honestly say "
          "nothing at all, return an empty string.\n"
          'Reply as JSON: {"line": "<what you say, or empty>"}'
    )
    user = f"What just happened: {beat['text']}\n\nWhat do you say, if anything?"
    try:
        out = complete_json(system, user, temperature=0.9, max_tokens=90,
                            log_group=f"interject:{npc_id}")
    except LLMError:
        return ""
    line = str(out.get("line", "")).strip()[:_MAX_LINE]
    if line:
        _mark(world, npc_id, beat)
        memory.remember(f'Seeing {beat["text"]}, you said: "{line}"')
    return line


JOIN_LINES = 8           # how much of the exchange a newcomer is handed


def join_conversation(world, npc_id: str, host_id: str, said: list, memory) -> str:
    """A companion waved into a conversation that is already going.

    The point of bringing someone over is that they arrive *knowing what was said* — a
    companion who joins knowing nothing is just another bystander, which is what they
    already were. So they get the exchange so far, both in the prompt and written into
    their memory, and one line on arriving.

    Same shape as `interject`: one short call, no actions, silence is a fine answer.
    `said` is [(speaker display name, line)] — already rendered, so npc/ stays clear of
    ui/. Never hand this the third-person phrasing; the memory it writes is theirs.
    """
    try:
        char = load_character(npc_id)
    except KeyError:
        return ""
    from npc.roster import character_name
    host = character_name(host_id)
    recent = said[-JOIN_LINES:]
    goal = open_goal(world.npcs[npc_id])
    # An empty speaker is something that was *done* rather than said (a note read out,
    # an object held up); it is already written as a description, so it needs no name.
    transcript = "\n".join(f"{who}: {text}" if who else text
                           for who, text in recent) or "(nothing yet)"
    system = (
        f"You are {char['name']} — {char.get('role', '')}.\n"
        f"Personality: {char.get('personality', '')}\n"
        f"Speech style: {char.get('speech_style', '')}\n"
        + (f"What you are trying to do: {goal['want']}\n" if goal else "")
        + f"\nYou are travelling with the player. They have just waved you over into a "
          f"conversation they were already having with {host}, so you are part of it now.\n"
          "You have been standing near enough to have caught most of it.\n\n"
          "Say ONE line, out loud, as you step in. Under 30 words. Speak to whichever of "
          "them you would actually be speaking to. You might take a side, ask the thing "
          "neither of them has asked, or say something dry about being dragged into it — "
          "whatever you would honestly say. Do not summarise what was said; they were "
          "both there.\n"
          'Reply as JSON: {"line": "<what you say, or empty>"}'
    )
    user = f"What has been said so far:\n{transcript}\n\nYou step in. What do you say?"
    try:
        out = complete_json(system, user, temperature=0.9, max_tokens=110,
                            log_group=f"join:{npc_id}")
    except LLMError:
        return ""
    line = str(out.get("line", "")).strip()[:_MAX_LINE]
    heard = " / ".join(f"{who}: {text[:90]}" if who else text[:90]
                       for who, text in recent[-3:])
    memory.remember(
        f"The player brought you into their conversation with {host}. "
        f"You heard: {heard}" + (f' You said: "{line}"' if line else "")
    )
    return line
