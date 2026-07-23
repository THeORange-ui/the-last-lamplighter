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
}

ITEM_IDS = set(ITEMS)
CURRENCY = "coin"


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
        return UseResult(spec.get("read_text", "There's nothing written here."), consumed=False)
    if use == "key":
        return UseResult(f"The {name} fits nothing here.", consumed=False)
    return UseResult(f"You can't use the {name} right now.", consumed=False)
