"""The world map for Emberhold (M1): three small rooms.

A room is a tile grid. The border is wall except for door tiles that lead to
other rooms. Lamps and the Hearthlight are objects; NPC positions live in
WorldState at runtime (seeded from character files).

This module also owns the ONE authored quest (relight three lamps) and the set
of KnownEntities that all emergent quests/actions are validated against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .items import ITEM_IDS
from .quests import KnownEntities, Objective, Quest, Reward
from .state import NPCRuntime, PlayerState, WorldState

TILE = 32
GRID_W = 19
GRID_H = 13

# Items that exist in the world come from the item catalog (engine/items.py).
INTERACTABLE_KINDS = {"lamp"}


@dataclass
class Door:
    x: int
    y: int
    to_room: str
    spawn: tuple[int, int]
    locked: bool = False
    locked_msg: str = ""


@dataclass
class Room:
    id: str
    name: str
    doors: list[Door] = field(default_factory=list)
    lamps: dict[str, tuple[int, int]] = field(default_factory=dict)
    obstacles: set[tuple[int, int]] = field(default_factory=set)
    hearthlight: tuple[int, int] | None = None

    def blocked(self) -> set[tuple[int, int]]:
        """Static blocked tiles: border walls (minus doors) + obstacles."""
        walls: set[tuple[int, int]] = set()
        for x in range(GRID_W):
            walls.add((x, 0))
            walls.add((x, GRID_H - 1))
        for y in range(GRID_H):
            walls.add((0, y))
            walls.add((GRID_W - 1, y))
        for d in self.doors:
            walls.discard((d.x, d.y))
        walls |= self.obstacles
        if self.hearthlight:
            walls.add(self.hearthlight)
        return walls

    def door_at(self, x: int, y: int) -> Door | None:
        return next((d for d in self.doors if d.x == x and d.y == y), None)

    def lamp_at(self, x: int, y: int) -> str | None:
        return next((lid for lid, (lx, ly) in self.lamps.items() if (lx, ly) == (x, y)), None)


def build_rooms() -> dict[str, Room]:
    # The square is the hub; each neighbouring room hangs off one of its edges.
    square = Room(
        id="square",
        name="Town Square",
        hearthlight=(9, 6),
        lamps={"lamp_square": (4, 3)},
        doors=[
            Door(x=GRID_W - 1, y=6, to_room="tavern", spawn=(1, 6)),
            Door(x=0, y=6, to_room="market", spawn=(GRID_W - 2, 6)),
            Door(x=9, y=0, to_room="home", spawn=(9, GRID_H - 2)),
            Door(x=9, y=GRID_H - 1, to_room="path", spawn=(9, 1)),
        ],
    )
    tavern = Room(
        id="tavern",
        name="The Ember Tavern",
        lamps={"lamp_tavern": (14, 9)},
        obstacles={(3, 3), (4, 3), (5, 3)},  # the bar counter
        doors=[Door(x=0, y=6, to_room="square", spawn=(GRID_W - 2, 6))],
    )
    market = Room(
        id="market",
        name="The Dusk Market",
        obstacles={(12, 4), (13, 4), (14, 4),   # a scavenger's stall
                   (4, 8), (5, 8)},              # stacked crates
        doors=[Door(x=GRID_W - 1, y=6, to_room="square", spawn=(1, 6))],
    )
    home = Room(
        id="home",
        name="Perrin's House",
        obstacles={(7, 8), (8, 8),               # a table
                   (13, 3), (14, 3)},            # a cold hearth / shelf
        doors=[Door(x=9, y=GRID_H - 1, to_room="square", spawn=(9, 1))],
    )
    path = Room(
        id="path",
        name="The Ridge Path",
        lamps={"lamp_path": (9, 4)},
        doors=[
            Door(x=9, y=0, to_room="square", spawn=(9, GRID_H - 2)),
            Door(
                x=9,
                y=GRID_H - 1,
                to_room="ridge",
                spawn=(9, 1),
                locked=True,
                locked_msg="The way up the ridge is swallowed in cold dark. Not yet.",
            ),
        ],
    )
    return {r.id: r for r in (square, tavern, market, home, path)}


# --- Character seed placement ------------------------------------------------
# Which room/tile each NPC starts in. Static personality is in npc/characters/.
NPC_SPAWNS = {
    "wren": ("square", 9, 9),
    "bram": ("tavern", 9, 4),
    "sella": ("market", 9, 7),
    "perrin": ("home", 9, 6),
}


def known_entities(rooms: dict[str, Room], npc_ids) -> KnownEntities:
    return KnownEntities(
        rooms=set(rooms.keys()) | {"ridge"},
        npcs=set(npc_ids),
        items=set(ITEM_IDS),
        interactable_kinds=set(INTERACTABLE_KINDS),
    )


def ensure_world_complete(state: WorldState) -> None:
    """Add any NPCs/lamps introduced since a save was written (forward-compat)."""
    from npc.roster import load_character

    for nid, (room, x, y) in NPC_SPAWNS.items():
        if nid not in state.npcs:
            try:
                inv = list(load_character(nid).get("inventory", []))
            except KeyError:
                inv = []
            state.npcs[nid] = NPCRuntime(npc_id=nid, room=room, x=x, y=y, inventory=inv)
    for room in build_rooms().values():
        for lamp_id in room.lamps:
            state.lamps.setdefault(lamp_id, False)


def starter_quest() -> Quest:
    """The single authored quest: Wren asks you to relight three lamps."""
    return Quest(
        id="relight_the_lamps",
        title="Relight the Lamps",
        description="Relight the three dead lamps around town to hold back the dark.",
        giver="wren",
        objective=Objective(type="interact", target="lamp", count=3),
        reward=Reward(type="affinity", value="15"),
    )


def new_world() -> tuple[WorldState, dict[str, Room], KnownEntities]:
    rooms = build_rooms()
    npc_ids = list(NPC_SPAWNS.keys())

    from npc.roster import load_character  # local import: avoids engine→npc coupling

    npcs: dict[str, NPCRuntime] = {}
    for nid, (room, x, y) in NPC_SPAWNS.items():
        try:
            seed_inv = list(load_character(nid).get("inventory", []))
        except KeyError:
            seed_inv = []
        npcs[nid] = NPCRuntime(npc_id=nid, room=room, x=x, y=y, inventory=seed_inv)

    lamps: dict[str, bool] = {}
    for room in rooms.values():
        for lamp_id in room.lamps:
            lamps[lamp_id] = False  # all start dead

    state = WorldState(
        player=PlayerState(room="square", x=9, y=8, inventory=["coin"] * 5),
        npcs=npcs,
        lamps=lamps,
    )
    return state, rooms, known_entities(rooms, npc_ids)
