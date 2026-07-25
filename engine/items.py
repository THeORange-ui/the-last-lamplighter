"""The item catalog — the closed set of things that can exist in inventories.

Items are referenced everywhere by their id (a string). This module defines which
ids are real, what they mean, what they're worth (coins, for the economy), and what
happens when they're used. NPCs can only offer/trade items they actually hold, and
quests can only target catalog items, so nothing untracked is invented by the LLM.

Per-item fields:
    name, desc  — display text
    value       — worth in coins (for buying/selling)
    use         — the use-verb: "eat" | "drink" | "read" | "key" | None
    heal        — HP restored by eat/drink
    read_text   — text shown when a readable item is used
    currency    — True for coins
"""
from __future__ import annotations

from dataclasses import dataclass

ITEMS: dict[str, dict] = {
    "oil_flask": {"name": "Oil Flask", "value": 3, "use": None,
                  "desc": "A small flask of lamp oil. Enough to relight one lamp."},
    "ridge_map": {"name": "Ridge Map", "value": 25, "use": "read",
                  "desc": "Ansel's hand-drawn map of the path up the ridge.",
                  "read_text": ("The map is faded but readable: past the last lamp, keep to "
                                "the left of the split rock, avoid the still air where the "
                                "cold pools, and climb only while a lamp is in sight behind "
                                "you. A shaky hand has written near the top: 'it is not "
                                "hungry — it is alone.'")},
    "old_key": {"name": "Old Key", "value": 8, "use": "key",
                "desc": "A worn iron key. It fits something, somewhere."},
    "coin": {"name": "Coin", "value": 1, "use": None, "currency": True,
             "desc": "A dull local coin, still worth a loaf."},
    "bread": {"name": "Loaf of Bread", "value": 2, "use": "eat", "heal": 8,
              "desc": "Warm, dense town bread."},
    "tavern_stew": {"name": "Bowl of Stew", "value": 4, "use": "eat", "heal": 15,
                    "desc": "Thick and hot. Steadies the nerves and the hands."},
    "scrap": {"name": "Scrap", "value": 4, "use": None,
              "desc": "Salvaged metal and oddments a scavenger prizes."},
    "tonic": {"name": "Warming Tonic", "value": 12, "use": "drink", "heal": 20,
              "desc": "A bitter draught said to hold off the cold."},
    "worn_staff": {"name": "Worn Lamplighter's Staff", "value": 18, "use": None,
                   "desc": ("A tall ashwood staff, its ferrule scorched from years of "
                            "lighting lamps. Initials are burned into the grip: A.W. — "
                            "it was Ansel's, Wren's lost mentor.")},
    # --- the Ansel chain: staff (ridge foot) -> lantern (wind shrine) -> last note ---
    "ansel_lantern": {"name": "Ansel's Lantern", "value": 22, "use": None,
                      "desc": ("A lamplighter's hand lantern, the glass starred with "
                               "cracks and the wick burned to nothing. Still shut, still "
                               "dry inside. Whoever set it down did it carefully.")},
    "ansel_note": {"name": "Ansel's Last Note", "value": 5, "use": "read",
                   "desc": "A single page, folded small, in a lamplighter's hand.",
                   "read_text": ("'If you are reading this then I did not come back, and "
                                 "I am sorry for it. It is not a beast. It is cold and it "
                                 "is by itself and it does not know what it is doing to "
                                 "us. Do not bring a blade up here and think that answers "
                                 "it. Wren — you kept better lamps than I did at your age. "
                                 "Keep them. Do not follow me. — A.'")},
    "rite_book": {"name": "Book of Lamplighters' Rites", "value": 14, "use": "read",
                  "desc": "A chapel book of the old keeping-rites, more habit than faith.",
                  "read_text": ("Mostly prayers for oil and patience. One page is "
                                "practical: the lamplighters sealed their stores with a "
                                "sigil, a lamp inside a ring, and the mark is undone by "
                                "drawing the ring closed with a wet thumb — 'so that the "
                                "light is never locked away from the one who tends it.'")},
    "cael_coat": {"name": "Cael's Coat", "value": 6, "use": None,
                  "desc": ("A heavy winter coat, far too small for Bram, kept brushed and "
                           "folded. It has been ready by the door for years.")},
    "lost_locket": {"name": "Tin Locket", "value": 4, "use": None,
                    "desc": "A cheap tin locket on a broken cord, the catch worn smooth."},
}

ITEM_IDS = set(ITEMS)
CURRENCY = "coin"

# Reading these sets a world flag — the player now KNOWS something, and knowing is
# what opens the matching lock (see engine/interact.py `requires: {"flag": ...}`).
# A character explaining the same thing sets the same flag (npc/actions.py FACT_FLAGS),
# so no single source can gate a path on its own.
READ_FLAGS = {"ridge_map": "map_read", "rite_book": "sigil_known"}


def is_item(item_id: str) -> bool:
    return item_id in ITEMS


def display_name(item_id: str) -> str:
    entry = ITEMS.get(item_id)
    return entry["name"] if entry else item_id.replace("_", " ").title()


def describe(item_id: str) -> str:
    entry = ITEMS.get(item_id)
    return entry["desc"] if entry else ""


def value_of(item_id: str) -> int:
    return int(ITEMS.get(item_id, {}).get("value", 1))


def is_currency(item_id: str) -> bool:
    return bool(ITEMS.get(item_id, {}).get("currency"))


def use_verb(item_id: str) -> str | None:
    """The label for the Use action, or None if the item isn't usable."""
    use = ITEMS.get(item_id, {}).get("use")
    return {"eat": "Eat", "drink": "Drink", "read": "Read", "key": "Use"}.get(use)


def catalog_for_prompt() -> str:
    return "\n".join(f"- {iid}: {e['name']} — {e['desc']}" for iid, e in ITEMS.items())


@dataclass
class UseResult:
    message: str
    consumed: bool = False


def use_item(state, item_id: str) -> UseResult:
    """Apply an item's use effect to the world/player. Returns what to tell the player."""
    spec = ITEMS.get(item_id, {})
    use = spec.get("use")
    name = spec.get("name", item_id)
    if use in ("eat", "drink"):
        healed = state.heal_player(int(spec.get("heal", 0)))
        verb = "eat" if use == "eat" else "drink"
        if healed > 0:
            return UseResult(
                f"You {verb} the {name}. (+{healed} HP — {state.player.hp}/{state.player.max_hp})",
                consumed=True)
        return UseResult(f"You {verb} the {name}, but you're already hale.", consumed=True)
    if use == "read":
        # Reading something can be a key: knowing how a lock works is what opens it.
        flag = READ_FLAGS.get(item_id)
        if flag:
            state.flags[flag] = True
        return UseResult(spec.get("read_text", "There's nothing written here."), consumed=False)
    if use == "key":
        return UseResult(f"The {name} fits nothing here.", consumed=False)
    return UseResult(f"You can't use the {name} right now.", consumed=False)
