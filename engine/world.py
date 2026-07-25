"""The world map for Emberhold: a linear main line town -> ridge, plus two forks.

A room is a tile grid. The border is wall except for door tiles that lead to other
rooms. Everything usable in a room is an `Interactable` (engine/interact.py); the
Hearthlight is scenery; NPC positions live in WorldState at runtime, seeded from
NPC_SPAWNS.

Each room also carries an LLM-facing `desc` and `features`, so a character standing
in a place can actually talk about it — only the room they are in gets described in
their briefing, which keeps the prompt small.

This module owns the one authored quest (go find Wren — everything after it emerges)
and the KnownEntities set that all emergent quests/actions are validated against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .interact import Interactable, interactable_kinds
from .items import ITEM_IDS
from .quests import KnownEntities, Objective, Quest, Reward
from .state import GroundItem, NPCRuntime, PlayerState, WorldState

TILE = 32
GRID_W = 19
GRID_H = 13


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
    # Everything usable in the room — lamps, fixtures, puzzles (see engine/interact.py).
    interactables: list[Interactable] = field(default_factory=list)
    obstacles: set[tuple[int, int]] = field(default_factory=set)
    hearthlight: tuple[int, int] | None = None
    biome: str = "town"          # "town" | "snow" | "camp" — drives the room's palette
    # LLM-facing: what this place is like, and scenery a character can refer to.
    # Only the room someone is actually standing in gets described in their briefing.
    desc: str = ""
    features: list[str] = field(default_factory=list)

    def blocked(self) -> set[tuple[int, int]]:
        """Static blocked tiles: border walls (minus doors) + obstacles + solid
        interactables (a lamp is walk-through; a chest is not)."""
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
        walls |= {i.pos for i in self.interactables if i.blocks}
        if self.hearthlight:
            walls.add(self.hearthlight)
        return walls

    def door_at(self, x: int, y: int) -> Door | None:
        return next((d for d in self.doors if d.x == x and d.y == y), None)

    def interactable_at(self, x: int, y: int) -> Interactable | None:
        return next((i for i in self.interactables if i.pos == (x, y)), None)

    def of_kind(self, kind: str) -> list[Interactable]:
        return [i for i in self.interactables if i.kind == kind]

    @property
    def lamps(self) -> dict[str, tuple[int, int]]:
        """lamp_id -> tile. The lamp registry in WorldState.lamps is seeded from this."""
        return {i.id: i.pos for i in self.interactables if i.kind == "lamp"}


def street_lamp(lamp_id: str, pos: tuple[int, int]) -> Interactable:
    """A dead street lamp. Lighting one costs an oil flask — the prerequisite is
    enforced here, in the engine, not left to an NPC to remember."""
    return Interactable(
        id=lamp_id, kind="lamp", pos=pos, name="dead lamp",
        desc="A street lamp gone cold and dark. A flask of oil would wake it.",
        hint="E: relight lamp",
        requires={"item": "oil_flask", "unlit": True,
                  "item_msg": "The lamp is dry. You need oil — perhaps Wren has some."},
        effects=[{"light_lamp": True}],
        use_msg="You pour the oil and coax the lamp back to light.",
        witness_msg="You watched the player pour oil into a dead lamp here and bring "
                    "it back to light.",
        blocks=False,          # you can walk over a lamp tile
    )


def build_rooms() -> dict[str, Room]:
    # A linear main line town -> ridge, with two optional forks (Perrin's house off
    # the road, a supply cellar off the tavern). Left/right doors carry the spine;
    # top/bottom doors are the forks.
    R = GRID_W - 1
    MIDY = 6

    square = Room(
        id="square",
        name="Town Square",
        desc="The heart of Emberhold. The great Hearthlight stands at the centre of it, and "
             "the whole town is laid out facing the lantern. The cobbles are worn pale in "
             "a ring where people used to gather close to be warm.",
        features=["the Hearthlight, the great lantern at the centre", "cobbles worn pale in a ring",
                  "shuttered houses facing inward"],
        hearthlight=(9, 6),
        interactables=[street_lamp("lamp_square", (4, 3))],
        doors=[Door(x=R, y=MIDY, to_room="tavern", spawn=(1, MIDY))],
    )
    tavern = Room(
        id="tavern",
        name="The Ember Tavern",
        desc="The Ember Tavern: low-ceilinged, smoke-stained, and the last room in town that "
             "is reliably warm. A long bar counter runs down one side. People come here "
             "mostly so as not to be alone.",
        features=["the long bar counter", "a hearth kept deliberately fed", "stools nobody sits on now"],
        interactables=[street_lamp("lamp_tavern", (14, 9))],
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
        desc="The tavern cellar. Barrels, crates, and the cold coming up through the stone. "
             "What supplies the town has left are stacked down here.",
        features=["stacked barrels and crates", "a low stone ceiling", "cold rising through the floor"],
        obstacles={(4, 4), (5, 4), (13, 8), (14, 8)},   # barrels and crates
        doors=[Door(x=9, y=0, to_room="tavern", spawn=(9, GRID_H - 2))],
    )
    market = Room(
        id="market",
        name="The Dusk Market",
        desc="The Dusk Market. Most of the stalls are folded up for good and one is still "
             "trading. What changes hands here now is salvage, not produce.",
        features=["one stall still trading", "folded-up empty stalls", "crates stacked against a wall"],
        interactables=[street_lamp("lamp_market", (4, 3))],
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
        desc="The Old Road out of town, running east toward the ridge. Frost in the ruts, "
             "roadside stones, and behind you the lamps of town getting further apart.",
        features=["frost standing in the wheel ruts", "roadside stones", "the lights of town behind you"],
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
        desc="Perrin's house. A cold hearth, one chair, a table with nothing on it. Nobody "
             "has been invited in here for a very long time.",
        features=["a hearth gone cold", "one chair, pulled away from it", "a bare table"],
        obstacles={(7, 8), (8, 8),                # a table
                   (13, 3), (14, 3)},             # a cold hearth / shelf
        doors=[Door(x=9, y=GRID_H - 1, to_room="road", spawn=(9, 1))],
    )
    camp = Room(
        id="camp",
        name="The Waystation",
        desc="The Waystation: a lean-to and a banked fire on the last flat ground before "
             "the climb. Travellers used to wait here for company before going up.",
        features=["a banked fire ringed with stones", "a lean-to open to the road", "the ridge rising east"],
        biome="camp",
        interactables=[
            Interactable(
                id="camp_fire", kind="campfire", pos=(9, 6), name="campfire",
                desc="A banked fire ringed with stones — the only real warmth on the road.",
                hint="E: rest by the fire",
                effects=[{"heal_full": True}, {"advance_day": True}],
                use_msg="You rest by the fire.",
                witness_msg="You sat out a night at the waystation fire with the player.",
            ),
            Interactable(
                id="camp_chest", kind="chest", pos=(6, 8), name="supply chest",
                desc="A dented iron chest, for stashing what you can't carry up the ridge.",
                hint="E: open the chest",
                effects=[{"open_panel": "storage"}],
            ),
        ],
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
        desc="The foot of the ridge. Snow to the knee, and the town's lights small and "
             "yellow far below. Tracks going up tend to stop somewhere around here.",
        features=["snow drifted over buried rock", "the town's lights far below", "tracks that stop"],
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
        desc="The Windward Pass: a narrow rocky throat where the wind never lets up. The "
             "cold in here does not behave like weather.",
        features=["a narrow rocky throat", "wind that never stops", "cold that pools and stays"],
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
        desc="The summit. Snow, sky, and a stillness with weight to it. The dark up here "
             "does not move the way dark is supposed to.",
        features=["nothing but snow and sky", "a stillness that has weight", "dark that does not move right"],
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
        # Derived from the map, so new content widens what quests may target for free.
        interactable_kinds=interactable_kinds(rooms),
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

    # Back-fill agendas for saves made before characters had them.
    from npc.agenda import seed_all
    seed_all(state)

    # Relocate the player / any NPC left in a room that no longer exists.
    if state.player.room not in rooms:
        state.player.room, state.player.x, state.player.y = "square", 9, 8
    for nid, npc in state.npcs.items():
        if npc.room not in rooms:
            room, x, y = NPC_SPAWNS.get(nid, ("square", 9, 6))
            npc.room, npc.x, npc.y = room, x, y


def starter_quest() -> Quest:
    """The one authored quest: find the lamplighter's apprentice.

    A true leaf — no follow-up, no check-back breadcrumb. Everything after this,
    the lamps included, comes out of Wren's own agenda (npc/agenda.py) rather than
    being scripted here. Talking to her completes it.
    """
    return Quest(
        id="find_the_apprentice",
        title="Find the lamplighter's apprentice",
        description="Somebody still tends the lamps in Emberhold. Find them.",
        # Deliberately no giver: nobody handed this to you, so Wren must not end up
        # remembering that she "gave you the quest" to come and find her.
        giver="",
        objective=Objective(type="talk_to", target="wren", count=1),
        reward=Reward(type="affinity", value="0"),
        followups=[],
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
        quests=[starter_quest()],       # in hand from the first frame

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
    from npc.agenda import seed_all                  # everyone starts with something to do
    seed_all(state)
    return state, rooms, known_entities(rooms, npc_ids)
