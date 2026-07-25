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
    inventory: list[str] = field(default_factory=list)   # item ids this NPC holds
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
    hp: int = 20
    max_hp: int = 20


@dataclass
class GroundItem:
    room: str
    x: int
    y: int
    item: str


@dataclass
class WorldState:
    player: PlayerState
    npcs: dict[str, NPCRuntime] = field(default_factory=dict)
    lamps: dict[str, bool] = field(default_factory=dict)   # lamp_id -> lit?
    # Mutable state for everything in engine/interact.py, keyed by interactable id
    # (the definitions themselves are static, like the map). Lamps keep their own
    # dict above because the HUD, the ridge gate and quest progress all read it.
    interact_state: dict[str, dict] = field(default_factory=dict)
    quests: list = field(default_factory=list)              # list[Quest]
    flags: dict = field(default_factory=dict)               # arbitrary world flags
    world_facts: list[str] = field(default_factory=list)    # facts revealed in play
    hearthlight: int = 60                                    # the failing lantern, 0..100
    events: EventLog = field(default_factory=EventLog)      # shared world event log
    ground_items: list = field(default_factory=list)        # list[GroundItem]
    party: list[str] = field(default_factory=list)          # npc_ids travelling with you
    day: int = 1                                             # advances when you rest at camp
    storage: list[str] = field(default_factory=list)         # items stashed in the camp chest

    # --- party ------------------------------------------------------------
    def in_party(self, npc_id: str) -> bool:
        return npc_id in self.party

    def add_to_party(self, npc_id: str) -> bool:
        """Add an NPC to the party. Returns False if already a member."""
        if npc_id in self.party:
            return False
        self.party.append(npc_id)
        return True

    def remove_from_party(self, npc_id: str) -> bool:
        if npc_id in self.party:
            self.party.remove(npc_id)
            return True
        return False

    # --- inventory --------------------------------------------------------
    def has_item(self, item: str) -> bool:
        return item in self.player.inventory

    def consume_item(self, item: str) -> bool:
        if item in self.player.inventory:
            self.player.inventory.remove(item)
            return True
        return False

    # --- player vitals ----------------------------------------------------
    def heal_player(self, amount: int) -> int:
        """Heal up to max_hp; returns HP actually restored."""
        before = self.player.hp
        self.player.hp = min(self.player.max_hp, self.player.hp + max(0, amount))
        return self.player.hp - before

    # --- ground items -----------------------------------------------------
    def ground_items_in(self, room: str) -> list:
        return [g for g in self.ground_items if g.room == room]

    def ground_item_at(self, room: str, x: int, y: int):
        return next((g for g in self.ground_items
                     if g.room == room and g.x == x and g.y == y), None)

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
