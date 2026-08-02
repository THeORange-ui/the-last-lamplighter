"""A shared, chronological record of notable world events.

One EventLog lives on WorldState. It serves two audiences, and **that is the whole
difficulty**:
  - The player: the whole log powers the in-game journal (press J), written to them.
  - NPCs: recent *public* events are folded into every NPC's briefing as rumour, so
    they know the lamps were relit without being told.

For a long time both read the same string, which meant characters were handed the
player-facing wording — "Wren gave **you** the quest", "**You** bested Bram" — inside a
prompt where "you" means the character. Wren read her own act as gossip about herself,
in someone else's voice. This is the same fault `ActionResult` has three fields for
(`effects` / `self_effects` / `observed`); the event log needed the same separation.

So an event carries `text` (the player's journal) and, where the two differ, `npc_text`
(third person, nobody's "you"). It also carries `actor`, so the briefing can leave out
what the character being briefed did themselves — they have the first-person version in
memory already, and hearing it back as a rumour is how a character ends up reporting
their own actions to themselves.

Ordering uses a simple monotonic sequence counter, not wall-clock time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# How the player is referred to when a line is written *for a character to read*. "You"
# is the character in every prompt this game builds, so the player is always third
# person on that side of the fence. ui/dialogue.py imports this for the same reason.
PLAYER_LABEL = "the player"


@dataclass
class Event:
    seq: int
    kind: str
    text: str                # written to the player, for the journal
    public: bool = True      # public events are visible to NPCs via their briefing
    room: str | None = None  # where it happened — who was present to witness it
    salience: int = 1        # how much it's worth remembering (see engine/witness.py)
    npc_text: str = ""       # the same event told to a character; falls back to `text`
    actor: str = ""          # npc_id who caused it, so they aren't told their own news


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)
    _seq: int = 0

    def record(self, kind: str, text: str, *, public: bool = True,
               room: str | None = None, salience: int = 1,
               npc_text: str = "", actor: str = "") -> Event:
        """`text` is for the player. Pass `npc_text` whenever that wording says "you"
        to the player, and `actor` whenever an NPC caused it."""
        self._seq += 1
        ev = Event(seq=self._seq, kind=kind, text=text.strip(), public=public,
                   room=room, salience=salience, npc_text=npc_text.strip(), actor=actor)
        self.events.append(ev)
        return ev

    def recent(self, n: int = 8, *, public_only: bool = False) -> list[Event]:
        src = [e for e in self.events if e.public] if public_only else self.events
        return src[-n:]

    def all_newest_first(self) -> list[Event]:
        return list(reversed(self.events))

    def public_briefing(self, n: int = 6, *, exclude_actor: str = "") -> str:
        """What word has reached this character. Filter first, then take the last `n`,
        so excluding their own acts surfaces older news rather than shortening the list.
        """
        recent = [e for e in self.events
                  if e.public and not (exclude_actor and e.actor == exclude_actor)]
        if not recent:
            return ""
        return "\n".join(f"- {e.npc_text or e.text}" for e in recent[-n:])
