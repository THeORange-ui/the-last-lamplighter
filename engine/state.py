"""WorldState — the single source of truth for the whole game.

The LLM reads a view of this and *proposes* changes via validated actions; it
never mutates state directly. Everything the game needs to know at runtime lives
here: player, NPC runtime state, lamps, quests, flags, and revealed world facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.journal import EventLog

# Affinity thresholds → categorical disposition.
AFFINITY_MIN, AFFINITY_MAX = -100, 100


def affinity_label(value: int) -> str:
    if value <= -50:
        return "hostile"
    if value < -10:
        return "wary"
    if value < 40:
        return "neutral"
    return "friendly"


@dataclass
class NPCRuntime:
    """Mutable per-NPC state (static personality lives in the character file)."""

    npc_id: str
    room: str
    x: int
    y: int
    affinity: int = 0            # -100..100 toward the player
    talked_to: bool = False
    flags: dict = field(default_factory=dict)

    @property
    def disposition(self) -> str:
        return affinity_label(self.affinity)


@dataclass
class PlayerState:
    room: str
    x: int
    y: int
    inventory: list[str] = field(default_factory=list)


@dataclass
class WorldState:
    player: PlayerState
    npcs: dict[str, NPCRuntime] = field(default_factory=dict)
    lamps: dict[str, bool] = field(default_factory=dict)   # lamp_id -> lit?
    quests: list = field(default_factory=list)              # list[Quest]
    flags: dict = field(default_factory=dict)               # arbitrary world flags
    world_facts: list[str] = field(default_factory=list)    # facts revealed in play
    hearthlight: int = 60                                    # the failing lantern, 0..100
    events: EventLog = field(default_factory=EventLog)      # shared world event log

    # --- inventory --------------------------------------------------------
    def has_item(self, item: str) -> bool:
        return item in self.player.inventory

    def consume_item(self, item: str) -> bool:
        if item in self.player.inventory:
            self.player.inventory.remove(item)
            return True
        return False

    # --- affinity ---------------------------------------------------------
    def adjust_affinity(self, npc_id: str, delta: int) -> int:
        npc = self.npcs[npc_id]
        npc.affinity = max(AFFINITY_MIN, min(AFFINITY_MAX, npc.affinity + delta))
        return npc.affinity

    # --- lamps ------------------------------------------------------------
    def lit_lamp_count(self) -> int:
        return sum(1 for lit in self.lamps.values() if lit)

    # --- facts ------------------------------------------------------------
    def add_fact(self, fact: str) -> None:
        fact = fact.strip()
        if fact and fact not in self.world_facts:
            self.world_facts.append(fact)

    # --- quests -----------------------------------------------------------
    def active_quests(self) -> list:
        return [q for q in self.quests if q.status == "active"]

    def quest_by_id(self, quest_id: str):
        return next((q for q in self.quests if q.id == quest_id), None)

    def has_quest(self, quest_id: str) -> bool:
        return self.quest_by_id(quest_id) is not None
