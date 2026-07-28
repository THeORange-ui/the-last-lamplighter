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

# The climb. Nobody goes up here on their own between conversations: townsfolk don't
# wander onto it (main._ambient_step) and an NPC can't `move_to` it (npc/actions.py).
# A character reaches the ridge by walking it *with* the player, as a companion.
RIDGE_ROOMS = {"ridge_foot", "ridge_pass", "ridge_summit"}

# Somebody's house, a locked store, a sealed vault. People don't drift into these on
# a stroll — only whoever lives there belongs. In a playtest an ex-hermit and a
# scavenger both ended up loitering in the undercroft, which is behind a sigil door.
PRIVATE_ROOMS = {"home", "cellar", "undercroft"}


@dataclass
class Door:
    x: int
    y: int
    to_room: str
    spawn: tuple[int, int]
    locked: bool = False
    locked_msg: str = ""
    # A door that opens on knowledge rather than a key: passable once this world flag
    # is set (by reading something, or by a character explaining it). See
    # engine.items.READ_FLAGS and npc.actions.FACT_FLAGS.
    requires_flag: str = ""

    def passable(self, state) -> bool:
        if self.locked:
            return False
        return not self.requires_flag or bool(state.flags.get(self.requires_flag))


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
            Door(x=R, y=MIDY, to_room="camp", spawn=(1, MIDY)),
        ],
    )
    road = Room(
        id="road",
        name="The Old Road",
        desc="The Old Road past the waystation, running east toward the ridge. Frost in the "
             "ruts, roadside stones, and behind you the lamps of town getting further apart.",
        features=["frost standing in the wheel ruts", "roadside stones", "the lights of town behind you"],
        obstacles={(6, 4), (12, 9)},              # roadside rocks
        doors=[
            Door(x=0, y=MIDY, to_room="camp", spawn=(R - 1, MIDY)),
            # Gating (read the map + lamps lit) is enforced in main.try_move.
            Door(x=R, y=MIDY, to_room="ridge_foot", spawn=(1, MIDY)),
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
        # Deliberately early on the main line, one room past the market: this is where
        # every night of the game is spent, and a camp you have to hike back to is a camp
        # nobody uses (see ui/night.py — resting is the world's turn).
        name="The Waystation",
        desc="The Waystation: a lean-to and a banked fire where the Old Road leaves town. "
             "Travellers used to muster here for company before going up the ridge.",
        features=["a banked fire ringed with stones", "a lean-to open to the road",
                  "the road running east, and the ridge somewhere beyond it"],
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
            Door(x=0, y=MIDY, to_room="market", spawn=(R - 1, MIDY)),
            Door(x=R, y=MIDY, to_room="road", spawn=(1, MIDY)),
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
            Door(x=0, y=MIDY, to_room="road", spawn=(R - 1, MIDY)),
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
    # --- the town, off the spine ------------------------------------------
    lamp_store = Room(
        id="lamp_store",
        name="The Lamplighters' Store",
        desc="Ansel's workshop, and Wren's. Racks of empty flasks, a bench worn into a "
             "dip where someone sat every evening for thirty years, and the smell of "
             "cold oil. Two aprons hang by the door. Only one of them gets used.",
        features=["racks of empty oil flasks", "a bench worn into a dip",
                  "two aprons on one hook"],
        biome="stone",
        interactables=[
            Interactable(
                id="ansel_bench", kind="bench", pos=(5, 4), name="Ansel's bench",
                desc="The workbench, with a lamplighter's tools still laid out in the "
                     "order somebody liked them in.",
                hint="E: look over the bench", once=True,
                effects=[{"add_fact": "Ansel kept his bench in strict order, and left "
                                      "it that way the evening he went up the ridge — "
                                      "he meant to come back to it."}],
                use_msg="The tools are laid out for tomorrow. Nobody has moved them.",
                witness_msg="You stood with the player at Ansel's bench, where his tools "
                            "are still set out for a morning that didn't come.",
                spent_hint="his tools, still in order"),
        ],
        doors=[Door(x=9, y=GRID_H - 1, to_room="square", spawn=(9, 1))],
    )
    chapel = Room(
        id="chapel",
        name="The Lamp Chapel",
        desc="A small stone chapel to nothing in particular — the lamplighters' own, "
             "where the keeping-rites were said over the oil. Cold, swept, and still "
             "used by exactly one person.",
        features=["a low stone altar with a lamp cut into it", "swept flagstones",
                  "a stair going down at the back"],
        biome="stone",
        interactables=[
            Interactable(
                id="chapel_shrine", kind="shrine", pos=(9, 3), name="the lamp altar",
                desc="An altar with a lamp carved into the stone, ringed by a circle.",
                hint="E: read the altar", once=True,
                effects=[{"add_fact": "The lamplighters' mark is a lamp inside a ring — "
                                      "a sigil, cut wherever they sealed something away."},
                         {"give_item": "rite_book"}],
                use_msg="The same mark is cut into the altar: a lamp, inside a ring. A "
                        "book of the rites lies open beneath it.",
                witness_msg="You read the lamp altar in the chapel with the player.",
                spent_hint="a lamp, inside a ring"),
        ],
        doors=[
            Door(x=9, y=0, to_room="square", spawn=(9, GRID_H - 2)),
            Door(x=9, y=GRID_H - 1, to_room="undercroft", spawn=(9, 1)),
        ],
    )
    undercroft = Room(
        id="undercroft",
        name="The Undercroft",
        desc="Under the chapel: a long, dry vault where the lamplighters kept their "
             "stores. It runs further east than it should — toward the tavern — and "
             "that end is closed off by a door with the sigil cut into it.",
        features=["stone shelving, mostly bare", "a sealed door at the east end",
                  "a sigil cut into the door: a lamp inside a ring"],
        biome="under",
        interactables=[
            Interactable(
                id="ansel_cache", kind="cache", pos=(4, 4), name="a lamplighter's cache",
                desc="A stone locker at the back of the vault, its lid pushed aside "
                     "already. Somebody came for something here, and left the rest.",
                hint="E: search the cache", once=True,
                effects=[{"give_item": "ansel_note"}, {"give_item": "oil_flask"}],
                use_msg="Oil, a spare wick — and a page folded small, tucked where it "
                        "would be found.",
                witness_msg="You were there when the player opened the old cache in the "
                            "undercroft and found a folded page inside.",
                spent_hint="emptied"),
            Interactable(
                id="sigil_door", kind="sigil", pos=(R - 1, MIDY), name="the sigil door",
                desc="The sealed east door. The mark on it is a lamp inside a ring; the "
                     "ring is cut open at the top.",
                hint="E: work the sigil",
                requires={"flag": "sigil_known",
                          "flag_msg": "The mark means something, but not to you. Someone "
                                      "who kept lamps would know it."},
                effects=[{"set_flag": "undercroft_open"}],
                use_msg="You close the ring with a wet thumb, the way the rite says. The "
                        "door gives inward onto the tavern's cellar.",
                witness_msg="You watched the player undo the lamplighters' sigil and open "
                            "the old way through to the tavern cellar.",
                blocks=False, once=True, spent_hint="the ring is closed"),
        ],
        doors=[
            Door(x=9, y=0, to_room="chapel", spawn=(9, GRID_H - 2)),
            Door(x=R, y=MIDY, to_room="cellar", spawn=(1, MIDY),
                 requires_flag="undercroft_open",
                 locked_msg="The sigil door is sealed. The mark is a lamp in a ring."),
        ],
    )
    well_yard = Room(
        id="well_yard",
        name="The Well Yard",
        desc="A yard behind the market with the town's well in it. The rope still turns, "
             "which is more than most things here do. Children are told to stay out and "
             "come here anyway.",
        features=["a deep well with a working windlass", "a wall low enough to sit on",
                  "chalk marks on the flagstones"],
        interactables=[
            Interactable(
                id="the_well", kind="well", pos=(9, 5), name="the well",
                desc="The town well. Something small and bright is caught on the rope, "
                     "a little way down.",
                hint="E: turn the windlass", once=True,
                effects=[{"give_item": "lost_locket"}],
                use_msg="The bucket comes up wet and empty — but there's a tin locket "
                        "snagged on the rope below it.",
                witness_msg="You watched the player wind up the well and find a tin "
                            "locket caught on the rope.",
                spent_hint="just water now"),
        ],
        doors=[Door(x=9, y=GRID_H - 1, to_room="market", spawn=(9, 1))],
    )
    farm_track = Room(
        id="farm_track",
        name="The Farm Track",
        desc="A rutted track south off the Old Road, running out to the last farm still "
             "worked. Hedges on both sides, and every few yards a lamp-post with no lamp "
             "left in it.",
        features=["empty lamp-posts along the hedge", "deep frozen ruts",
                  "a gate standing open"],
        obstacles={(5, 3), (13, 9)},
        doors=[
            Door(x=9, y=0, to_room="road", spawn=(9, GRID_H - 2)),
            Door(x=R, y=MIDY, to_room="outfarm", spawn=(1, MIDY)),
        ],
    )
    outfarm = Room(
        id="outfarm",
        name="The Outfarm",
        desc="The last worked farm in the valley: a low house, a byre, and fields that "
             "have given up. It is further from the Hearthlight than anyone else lives, "
             "and it shows in how dark the yard is.",
        features=["a byre with two thin cows", "a house with one lit window",
                  "fields gone to frost"],
        obstacles={(4, 4), (5, 4), (6, 4), (14, 8), (15, 8)},
        interactables=[
            Interactable(
                id="outfarm_post", kind="lamp_post", pos=(9, 3), name="the farm's lamp-post",
                desc="A lamp-post by the farm gate with no lamp in the bracket at all. "
                     "The glass was taken out and never put back.",
                hint="E: check the lamp-post", once=True,
                effects=[{"add_fact": "The outfarm's lamp was taken away years ago and "
                                      "never replaced — the farm has been keeping itself "
                                      "dark at the edge of the valley ever since."}],
                use_msg="Empty bracket, and rust where the glass sat. Nobody has kept "
                        "this one in a long time.",
                spent_hint="an empty bracket"),
        ],
        doors=[Door(x=0, y=MIDY, to_room="farm_track", spawn=(R - 1, MIDY))],
    )
    # --- the ridge, off the climb -----------------------------------------
    ridge_shelf = Room(
        id="ridge_shelf",
        name="The Ridge — Shelf",
        biome="snow",
        desc="A wide shelf of rock off the climb, out of the wind. You can see the whole "
             "valley from here, and the town in it, very small. There is a way down the "
             "scree from the far side — quick, and only downward.",
        features=["the whole valley laid out below", "a scree slope going down",
                  "shelter from the wind"],
        obstacles={(6, 3), (7, 3)},
        interactables=[
            Interactable(
                id="shelf_view", kind="vantage", pos=(9, 4), name="the valley edge",
                desc="The lip of the shelf, where the whole of Emberhold is visible at "
                     "once — every lamp in it, lit or dark.",
                hint="E: look out over the valley", once=True,
                effects=[{"add_fact": "From the ridge shelf you can see every lamp in "
                                      "Emberhold at once, and how far the dark has come "
                                      "in from the valley's edges."}],
                use_msg="The town is a handful of small lights, and the dark stands "
                        "around it like water around a stone.",
                witness_msg="You stood with the player on the shelf and looked down at "
                            "the whole of Emberhold, lit and unlit.",
                blocks=False, spent_hint="the valley, below"),
        ],
        doors=[
            Door(x=0, y=MIDY, to_room="ridge_foot", spawn=(R - 1, MIDY)),
            Door(x=R, y=MIDY, to_room="snow_cairn", spawn=(1, MIDY)),
            # One-way: you can go down the scree, but not back up it. It drops to the road
            # — the ground directly below the ridge now that the waystation sits in town.
            Door(x=9, y=GRID_H - 1, to_room="road", spawn=(9, 2)),
        ],
    )
    snow_cairn = Room(
        id="snow_cairn",
        name="The Ridge — Cairn",
        biome="snow",
        desc="A cairn of stacked stones at the end of the shelf, half buried. People "
             "have been putting stones on it for longer than the Gloam has been awake. "
             "Some of the stones have names scratched into them.",
        features=["a cairn of stacked stones", "names scratched into the lower stones",
                  "snow drifted up one side"],
        interactables=[
            Interactable(
                id="the_cairn", kind="cairn", pos=(9, 6), name="the cairn",
                desc="A memorial cairn. The names on it are of people who went up the "
                     "ridge and did not come down.",
                hint="E: read the names", once=True,
                effects=[{"add_fact": "The cairn on the ridge carries the names of those "
                                      "who went up and never came down. Cael's name is "
                                      "on it. Ansel's is not — nobody has put a stone "
                                      "there for him yet."}],
                use_msg="Names, cut small to save room. One of them is CAEL. There is no "
                        "stone for Ansel.",
                witness_msg="You read the cairn on the ridge with the player. Cael's name "
                            "is cut into it. Ansel's is not there at all.",
                spent_hint="the names"),
        ],
        doors=[Door(x=0, y=MIDY, to_room="ridge_shelf", spawn=(R - 1, MIDY))],
    )
    wind_shrine = Room(
        id="wind_shrine",
        name="The Ridge — Wind Shrine",
        biome="snow",
        desc="A niche cut into the rock beside the pass, where lamplighters used to "
             "leave a light burning for whoever was still up the mountain. The niche is "
             "out of the wind. There is something set down carefully inside it.",
        features=["a niche cut for a lamp", "old wax run down the stone",
                  "shelter, of a kind"],
        interactables=[
            Interactable(
                id="the_niche", kind="niche", pos=(9, 5), name="the lamp niche",
                desc="The shrine niche. A hand lantern stands in it, upright, its glass "
                     "cracked through and its wick burned away to nothing.",
                hint="E: take what's in the niche", once=True,
                effects=[{"give_item": "ansel_lantern"}],
                use_msg="A lamplighter's lantern, set down square in the middle of the "
                        "niche. Whoever left it here took the time to stand it upright.",
                witness_msg="You were with the player at the wind shrine when they lifted "
                            "a cracked lamplighter's lantern out of the niche.",
                spent_hint="an empty niche"),
        ],
        doors=[Door(x=0, y=MIDY, to_room="ridge_pass", spawn=(R - 1, MIDY))],
    )
    the_hollow = Room(
        id="the_hollow",
        name="The Ridge — The Hollow",
        biome="snow",
        desc="A bowl in the rock off the windward side where the air does not move at "
             "all. The wind is loud everywhere else on this mountain and silent here. "
             "The cold in the hollow is not the same cold as outside it.",
        features=["air that does not move", "no wind sound at all", "cold that pools"],
        obstacles={(4, 8), (14, 4)},
        interactables=[
            Interactable(
                id="still_air", kind="stillness", pos=(9, 6), name="the still air",
                desc="The middle of the hollow, where the cold sits deepest and the "
                     "quiet has a shape to it.",
                hint="E: stand in the stillness", once=True,
                effects=[{"add_fact": "In the hollow on the ridge the cold pools and the "
                                      "air goes silent, and people who stand in it hear "
                                      "themselves called by names only they know."}],
                use_msg="The quiet leans in. For a moment it sounds very much like "
                        "somebody saying your name, kindly, from a long way off.",
                witness_msg="You stood in the still air of the hollow with the player, "
                            "and heard what it does there.",
                blocks=False, spent_hint="quiet, and waiting"),
        ],
        doors=[Door(x=R, y=MIDY, to_room="ridge_pass", spawn=(1, MIDY))],
    )

    # New doors onto the existing spine (kept here so the spine reads plainly above).
    square.doors += [
        Door(x=9, y=0, to_room="lamp_store", spawn=(9, GRID_H - 2)),
        Door(x=9, y=GRID_H - 1, to_room="chapel", spawn=(9, 1)),
    ]
    cellar.doors.append(
        Door(x=0, y=MIDY, to_room="undercroft", spawn=(R - 1, MIDY),
             requires_flag="undercroft_open",
             locked_msg="A sealed door, with a lamp-and-ring cut into it."))
    market.doors.append(Door(x=9, y=0, to_room="well_yard", spawn=(9, GRID_H - 2)))
    road.doors.append(Door(x=9, y=GRID_H - 1, to_room="farm_track", spawn=(9, 1)))
    ridge_foot.doors.append(Door(x=R, y=MIDY, to_room="ridge_shelf", spawn=(1, MIDY)))
    ridge_pass.doors += [
        Door(x=0, y=MIDY, to_room="the_hollow", spawn=(R - 1, MIDY)),
        Door(x=R, y=MIDY, to_room="wind_shrine", spawn=(1, MIDY)),
    ]

    rooms = (square, tavern, cellar, market, road, home, camp,
             ridge_foot, ridge_pass, ridge_summit,
             lamp_store, chapel, undercroft, well_yard, farm_track, outfarm,
             ridge_shelf, snow_cairn, wind_shrine, the_hollow)
    return {r.id: r for r in rooms}


# --- Character seed placement ------------------------------------------------
# Which room/tile each NPC starts in. Static personality is in npc/characters/.
NPC_SPAWNS = {
    "wren": ("square", 9, 9),
    "bram": ("tavern", 9, 4),
    "sella": ("market", 9, 7),
    "perrin": ("home", 9, 6),
    # minor characters — lore and small favours, no arcs (npc/actions.py ACTION_SETS)
    "hessa": ("chapel", 6, 6),
    "moss": ("well_yard", 6, 7),
    "tilda": ("outfarm", 9, 7),
    "corvin": ("camp", 12, 8),
}


def ridge_open(state: WorldState) -> bool:
    """Is the climb passable? Every lamp lit and Ansel's map read.

    Lives here rather than in the UI because two very different callers need the same
    answer: the door in `main.try_move`, and `engine/initiative.py`, which must not send
    a character up a mountain the player has no way to follow them onto.
    """
    if state.flags.get("gloam_resolved"):
        return True
    return bool(state.flags.get("map_read")) and state.lit_lamp_count() == len(state.lamps)


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
