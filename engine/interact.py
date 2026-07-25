"""Interactable things in the world — one system for lamps, camp fixtures and puzzles.

Before this module there were three ad-hoc systems doing the same job: `Room.lamps`
with a bespoke oil-consuming branch, `Room.fixtures` with hardcoded campfire/chest
branches, and locked doors. A puzzle would have been a fourth. Now there is one
`Interactable`, and adding world content is **data** rather than code.

An Interactable is a *static definition* living on a Room (like the map itself);
only its mutable state is persisted, in `WorldState.interact_state`. Lamp lit-state
stays in `WorldState.lamps`, which several systems already read (the HUD, the ridge
gate, quest progress), so a lamp's `light_lamp` effect writes there.

    requires   what must be true to use it:
                 {"item": <item_id>}   the player must hold it (spent unless consumes=False)
                 {"flag": <name>}      a world flag must be set — this is how a
                                       "knowledge lock" works: a character tells you
                                       something, the engine sets the flag, the door opens
                 {"unlit": True}       lamp-specific: only usable while dark
    effects    what happens, as single-key dicts applied in order:
                 {"light_lamp": True} {"heal_full": True} {"advance_day": True}
                 {"open_panel": "storage"} {"set_flag": <name>} {"add_fact": <text>}
                 {"give_item": <item_id>}
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.items import display_name


@dataclass
class Interactable:
    id: str
    kind: str                    # "lamp" | "campfire" | "chest" | ...
    pos: tuple[int, int]
    name: str = ""
    desc: str = ""               # LLM-facing: what this thing is, for the room briefing
    hint: str = ""               # player-facing "E: ..." prompt
    requires: dict = field(default_factory=dict)
    effects: list[dict] = field(default_factory=list)
    use_msg: str = ""            # flavor line on a successful use, shown to the player
    witness_msg: str = ""        # what someone standing here remembers seeing (1st person)
    consumes: bool = True        # spend requires["item"] on success
    blocks: bool = True          # does it block its tile?
    once: bool = False           # usable only once
    hidden: bool = False         # not drawn and not interactable until revealed
    spent_hint: str = ""         # hint once it's used up (empty = no hint at all)

    @property
    def label(self) -> str:
        return self.name or self.kind.replace("_", " ").title()


@dataclass
class InteractResult:
    ok: bool = False
    message: str = ""
    panel: str | None = None                    # UI panel to open ("storage")
    quests_dirty: bool = False                  # world changed in a quest-relevant way
    events: list[tuple[str, str]] = field(default_factory=list)   # (kind, text)


# --- state ------------------------------------------------------------------
def state_of(world, inter: Interactable) -> dict:
    """The mutable state dict for this interactable (created on first touch)."""
    return world.interact_state.setdefault(inter.id, {})


def is_spent(world, inter: Interactable) -> bool:
    return bool(inter.once and state_of(world, inter).get("used"))


def is_live(world, inter: Interactable) -> bool:
    """Is there anything left to do here at all, requirements aside?"""
    if is_spent(world, inter):
        return False
    if inter.requires.get("unlit") and world.lamps.get(inter.id):
        return False
    return True


def can_interact(world, inter: Interactable) -> tuple[bool, str]:
    """(allowed, why-not). A why-not is player-facing, so it should be a nudge."""
    if not is_live(world, inter):
        return False, ""
    req = inter.requires
    item = req.get("item")
    if item and item not in world.player.inventory:
        return False, req.get("item_msg") or f"You need {display_name(item)} for that."
    flag = req.get("flag")
    if flag and not world.flags.get(flag):
        return False, req.get("flag_msg") or f"You don't know how to work the {inter.label}."
    return True, ""


# --- use --------------------------------------------------------------------
def apply_interaction(world, inter: Interactable, room_name: str = "") -> InteractResult:
    """Check requirements, then apply every effect in order.

    Returns what the caller should show/do; the caller owns the UI and the event
    log so this module stays free of both.
    """
    ok, why = can_interact(world, inter)
    if not ok:
        return InteractResult(ok=False, message=why)

    req_item = inter.requires.get("item")
    if req_item and inter.consumes:
        world.consume_item(req_item)

    result = InteractResult(ok=True, message=inter.use_msg)
    bits: list[str] = []

    for eff in inter.effects:
        for verb, val in eff.items():
            if verb == "light_lamp":
                lamp_id = inter.id if val is True else str(val)
                world.lamps[lamp_id] = True
                result.quests_dirty = True
                result.events.append(
                    ("lamp_lit", f"You relit a lamp in {room_name or 'the dark'}."))
            elif verb == "heal_full":
                healed = world.heal_player(world.player.max_hp)
                if healed:
                    bits.append(f"+{healed} HP")
            elif verb == "advance_day":
                world.day += 1
                from engine.trade import restock_vendor   # local: avoid an import cycle
                for nid in world.npcs:
                    restock_vendor(world, nid, world.day)  # only vendors actually restock
                bits.append(f"Day {world.day}")
                result.events.append(
                    ("rest", f"You rested at {room_name or 'camp'}. Day {world.day} begins."))
            elif verb == "open_panel":
                result.panel = str(val)
            elif verb == "set_flag":
                world.flags[str(val)] = True
                result.quests_dirty = True
            elif verb == "add_fact":
                world.add_fact(str(val))
            elif verb == "give_item":
                world.player.inventory.append(str(val))
                bits.append(f"got {display_name(str(val))}")

    # Record the use (with its kind, so progress can be counted from state alone).
    st = state_of(world, inter)
    st["kind"] = inter.kind
    st["uses"] = int(st.get("uses", 0)) + 1
    if inter.once:
        st["used"] = True
    if bits:
        result.message = (result.message + "  (" + ", ".join(bits) + ")").strip()
    return result


# --- map-wide queries -------------------------------------------------------
def interactable_kinds(rooms) -> set[str]:
    """Every kind present on the map — what an `interact` quest may legally target."""
    return {i.kind for r in rooms.values() for i in r.interactables}


def used_count(world, kind: str) -> int:
    """How many distinct interactables of a kind the player has used — read from
    WorldState alone (no map needed), so quest progress can be evaluated anywhere."""
    if kind == "lamp":
        return world.lit_lamp_count()
    return sum(1 for st in world.interact_state.values()
               if st.get("kind") == kind and st.get("uses"))


def count_of_kind(rooms, kind: str) -> int:
    return sum(1 for r in rooms.values() for i in r.interactables if i.kind == kind)
