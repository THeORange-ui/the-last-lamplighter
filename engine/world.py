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
from .state import GroundItem, NPCRuntime, PlayerState, WorldState

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
    biome: str = "town"          # "town" | "snow" | "camp" — drives the room's palette
    # Interactable fixtures keyed by tile: (x, y) -> kind ("campfire" | "chest").
    fixtures: dict[tuple[int, int], str] = field(default_factory=dict)

    def blocked(self) -> set[tuple[int, int]]:
        """Static blocked tiles: border walls (minus doors) + obstacles + fixtures."""
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
        walls |= set(self.fixtures)
        if self.hearthlight:
            walls.add(self.hearthlight)
        return walls

    def door_at(self, x: int, y: int) -> Door | None:
        return next((d for d in self.doors if d.x == x and d.y == y), None)

    def lamp_at(self, x: int, y: int) -> str | None:
        return next((lid for lid, (lx, ly) in self.lamps.items() if (lx, ly) == (x, y)), None)

    def fixture_at(self, x: int, y: int) -> str | None:
        return self.fixtures.get((x, y))


def build_rooms() -> dict[str, Room]:
    # A linear main line town -> ridge, with two optional forks (Perrin's house off
    # the road, a supply cellar off the tavern). Left/right doors carry the spine;
    # top/bottom doors are the forks.
    R = GRID_W - 1
    MIDY = 6

    square = Room(
        id="square",
        name="Town Square",
        hearthlight=(9, 6),
        lamps={"lamp_square": (4, 3)},
        doors=[Door(x=R, y=MIDY, to_room="tavern", spawn=(1, MIDY))],
    )
    tavern = Room(
        id="tavern",
        name="The Ember Tavern",
        lamps={"lamp_tavern": (14, 9)},
        obstacles={(3, 3), (4, 3), (5, 3)},       # the bar counter
        doors=[
            Door(x=0, y=MIDY, to_room="square", spawn=(R - 1, MIDY)),
            Door(x=R, y=MIDY, to_room="market", spawn=(1, MIDY)),
            Door(x=9, y=GRID_H - 1, to_room="cellar", spawn=(9, 1)),   # fork
        ],
    )
    cellar = Room(
        id="cellar",
        name="The Tavern Cellar",
        obstacles={(4, 4), (5, 4), (13, 8), (14, 8)},   # barrels and crates
        doors=[Door(x=9, y=0, to_room="tavern", spawn=(9, GRID_H - 2))],
    )
    market = Room(
        id="market",
        name="The Dusk Market",
        lamps={"lamp_market": (4, 3)},
        obstacles={(12, 4), (13, 4), (14, 4),     # Sella's stall
                   (4, 8), (5, 8)},                # stacked crates
        doors=[
            Door(x=0, y=MIDY, to_room="tavern", spawn=(R - 1, MIDY)),
            Door(x=R, y=MIDY, to_room="road", spawn=(1, MIDY)),
        ],
    )
    road = Room(
        id="road",
        name="The Old Road",
        obstacles={(6, 4), (12, 9)},              # roadside rocks
        doors=[
            Door(x=0, y=MIDY, to_room="market", spawn=(R - 1, MIDY)),
            Door(x=R, y=MIDY, to_room="camp", spawn=(1, MIDY)),
            Door(x=9, y=0, to_room="home", spawn=(9, GRID_H - 2)),      # fork
        ],
    )
    home = Room(
        id="home",
        name="Perrin's House",
        obstacles={(7, 8), (8, 8),                # a table
                   (13, 3), (14, 3)},             # a cold hearth / shelf
        doors=[Door(x=9, y=GRID_H - 1, to_room="road", spawn=(9, 1))],
    )
    camp = Room(
        id="camp",
        name="The Waystation",
        biome="camp",
        fixtures={(9, 6): "campfire", (6, 8): "chest"},
        obstacles={(13, 4), (14, 4)},             # a lean-to
        doors=[
            Door(x=0, y=MIDY, to_room="road", spawn=(R - 1, MIDY)),
            # Gating (read the map + lamps lit) is enforced in main.try_move.
            Door(x=R, y=MIDY, to_room="ridge_foot", spawn=(1, MIDY)),
        ],
    )
    # --- the ridge: a small snow-swept climb, the Gloam waiting at the top ---
    ridge_foot = Room(
        id="ridge_foot",
        name="The Ridge — Foot",
        biome="snow",
        obstacles={(4, 4), (14, 8)},              # snow-buried rocks
        doors=[
            Door(x=0, y=MIDY, to_room="camp", spawn=(R - 1, MIDY)),
            Door(x=9, y=GRID_H - 1, to_room="ridge_pass", spawn=(9, 1)),
        ],
    )
    ridge_pass = Room(
        id="ridge_pass",
        name="The Ridge — Windward Pass",
        biome="snow",
        obstacles={(5, 5), (6, 5), (13, 7), (14, 7), (9, 9)},  # a narrow, rocky pass
        doors=[
            Door(x=9, y=0, to_room="ridge_foot", spawn=(9, GRID_H - 2)),
            Door(x=9, y=GRID_H - 1, to_room="ridge_summit", spawn=(9, 1)),
        ],
    )
    ridge_summit = Room(
        id="ridge_summit",
        name="The Ridge — Summit",
        biome="snow",
        doors=[Door(x=9, y=0, to_room="ridge_pass", spawn=(9, GRID_H - 2))],
    )
    rooms = (square, tavern, cellar, market, road, home, camp,
             ridge_foot, ridge_pass, ridge_summit)
    return {r.id: r for r in rooms}


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
        rooms=set(rooms.keys()),
        npcs=set(npc_ids),
        items=set(ITEM_IDS),
        interactable_kinds=set(INTERACTABLE_KINDS),
    )


def ensure_world_complete(state: WorldState) -> None:
    """Migrate an older save bundle forward: add new NPCs, seed current lamps, drop
    lamps/rooms that no longer exist, and relocate anyone stranded by a map change."""
    from npc.roster import load_character

    rooms = build_rooms()
    valid_lamps = {lid for r in rooms.values() for lid in r.lamps}

    for nid, (room, x, y) in NPC_SPAWNS.items():
        if nid not in state.npcs:
            try:
                inv = list(load_character(nid).get("inventory", []))
            except KeyError:
                inv = []
            state.npcs[nid] = NPCRuntime(npc_id=nid, room=room, x=x, y=y, inventory=inv)

    # Seed lamps for all current rooms; forget lamps whose room was retired (else the
    # ridge could never open, needing a lamp with no tile to relight).
    for lid in valid_lamps:
        state.lamps.setdefault(lid, False)
    for lid in [l for l in state.lamps if l not in valid_lamps]:
        del state.lamps[lid]

    # Relocate the player / any NPC left in a room that no longer exists.
    if state.player.room not in rooms:
        state.player.room, state.player.x, state.player.y = "square", 9, 8
    for nid, npc in state.npcs.items():
        if npc.room not in rooms:
            room, x, y = NPC_SPAWNS.get(nid, ("square", 9, 6))
            npc.room, npc.x, npc.y = room, x, y


def starter_quest() -> Quest:
    """The single authored quest: Wren asks you to relight three lamps."""
    return Quest(
        id="relight_the_lamps",
        title="Relight the Lamps",
        description="Relight the three dead lamps around town to hold back the dark.",
        giver="wren",
        objective=Objective(type="interact", target="lamp", count=3),
        reward=Reward(type="affinity", value="15"),
        # After the lamps, Wren decides where the player's path leads next.
        followups=[{"kind": "decide_later"}],
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
        ground_items=[
            GroundItem("ridge_pass", 3, 3, "tonic"),          # a supply to find
            GroundItem("ridge_foot", 5, 8, "worn_staff"),     # Ansel's staff, on the ridge
            GroundItem("cellar", 6, 8, "oil_flask"),          # the off-path supply cache
            GroundItem("cellar", 12, 4, "bread"),
        ],
    )
    from engine.trade import restock_vendor          # seed the shop's opening stock
    for nid in npcs:
        restock_vendor(state, nid, state.day)
    return state, rooms, known_entities(rooms, npc_ids)
