"""The item catalog — the closed set of things that can exist in inventories.

Items are referenced everywhere by their id (a string). This module is the single
place that defines which ids are real and what they mean. NPCs can only offer items
they actually hold, and quests can only target catalog items, so nothing untracked
is ever invented by the LLM.
"""
from __future__ import annotations

ITEMS: dict[str, dict] = {
    "oil_flask": {"name": "Oil Flask",
                  "desc": "A small flask of lamp oil. Enough to relight one lamp."},
    "ridge_map": {"name": "Ridge Map",
                  "desc": "Ansel's hand-drawn map of the path up the ridge."},
    "old_key": {"name": "Old Key", "desc": "A worn iron key. It fits something, somewhere."},
    "coin": {"name": "Coin", "desc": "A dull local coin, still worth a loaf."},
    "bread": {"name": "Loaf of Bread", "desc": "Warm, dense town bread."},
    "tavern_stew": {"name": "Bowl of Stew", "desc": "Thick and hot. Steadies the nerves."},
    "scrap": {"name": "Scrap", "desc": "Salvaged metal and oddments a scavenger prizes."},
    "tonic": {"name": "Warming Tonic", "desc": "A bitter draught said to hold off the cold."},
}

ITEM_IDS = set(ITEMS)


def is_item(item_id: str) -> bool:
    return item_id in ITEMS


def display_name(item_id: str) -> str:
    entry = ITEMS.get(item_id)
    return entry["name"] if entry else item_id.replace("_", " ").title()


def describe(item_id: str) -> str:
    entry = ITEMS.get(item_id)
    return entry["desc"] if entry else ""


def catalog_for_prompt() -> str:
    return "\n".join(
        f"- {iid}: {e['name']} — {e['desc']}" for iid, e in ITEMS.items()
    )
