"""A shared, chronological record of notable world events.

One EventLog lives on WorldState. It serves two audiences:
  - NPCs: recent *public* events are folded into every NPC's briefing, so they
    are aware of progression (lamps relit, quests finished) without being told.
  - The player: the whole log powers the in-game journal (press J).

Ordering uses a simple monotonic sequence counter, not wall-clock time.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    seq: int
    kind: str
    text: str
    public: bool = True     # public events are visible to NPCs via their briefing
    room: str | None = None  # where it happened — who was present to witness it
    salience: int = 1        # how much it's worth remembering (see engine/witness.py)


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)
    _seq: int = 0

    def record(self, kind: str, text: str, *, public: bool = True,
               room: str | None = None, salience: int = 1) -> Event:
        self._seq += 1
        ev = Event(seq=self._seq, kind=kind, text=text.strip(), public=public,
                   room=room, salience=salience)
        self.events.append(ev)
        return ev

    def recent(self, n: int = 8, *, public_only: bool = False) -> list[Event]:
        src = [e for e in self.events if e.public] if public_only else self.events
        return src[-n:]

    def all_newest_first(self) -> list[Event]:
        return list(reversed(self.events))

    def public_briefing(self, n: int = 6) -> str:
        recent = self.recent(n, public_only=True)
        if not recent:
            return ""
        return "\n".join(f"- {e.text}" for e in recent)
