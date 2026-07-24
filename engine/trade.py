"""Mechanical item/coin transfers for the economy.

Prices come from the item catalog's `value`. These functions only move items and
coins between inventories and report success — flavor text and any AI reactions
are the caller's job. Gifting and asking are handled through the dialogue AI, not
here; this module is the deterministic buy/sell/give plumbing.
"""
from __future__ import annotations

import math

from engine.items import CURRENCY, value_of

# A shop takes a margin: you buy above catalog value and sell below it.
SHOP_MARKUP = 1.5
SHOP_SELL_FACTOR = 0.5


def coins(inventory: list[str]) -> int:
    return inventory.count(CURRENCY)


def _move_item(src: list[str], dst: list[str], item: str) -> bool:
    if item in src:
        src.remove(item)
        dst.append(item)
        return True
    return False


def _move_coins(src: list[str], dst: list[str], n: int) -> bool:
    if coins(src) < n:
        return False
    for _ in range(n):
        src.remove(CURRENCY)
        dst.append(CURRENCY)
    return True


def give_to_npc(state, npc, item: str) -> bool:
    """Transfer an item from the player to an NPC (a free gift)."""
    return _move_item(state.player.inventory, npc.inventory, item)


def buy_from_npc(state, npc, item: str) -> tuple[bool, str]:
    """Player buys an NPC's item at its catalog value."""
    price = value_of(item)
    if item not in npc.inventory:
        return False, "they don't have that to sell"
    if coins(state.player.inventory) < price:
        return False, f"you can't afford it ({price} coins)"
    _move_coins(state.player.inventory, npc.inventory, price)
    _move_item(npc.inventory, state.player.inventory, item)
    return True, f"bought for {price} coins"


def sell_to_npc(state, npc, item: str) -> tuple[bool, str]:
    """Player sells an item to an NPC at its catalog value."""
    price = value_of(item)
    if item not in state.player.inventory:
        return False, "you don't have that"
    if coins(npc.inventory) < price:
        return False, "they can't afford that"
    _move_item(state.player.inventory, npc.inventory, item)
    _move_coins(npc.inventory, state.player.inventory, price)
    return True, f"sold for {price} coins"


# --- shop (a vendor with a margin and daily stock) ---------------------------
def shop_buy_price(item: str) -> int:
    return max(1, math.ceil(value_of(item) * SHOP_MARKUP))


def shop_sell_price(item: str) -> int:
    return max(1, int(value_of(item) * SHOP_SELL_FACTOR))


def shop_buy(state, vendor, item: str) -> tuple[bool, str]:
    """Player buys a vendor's stock item at the marked-up price."""
    price = shop_buy_price(item)
    if item not in vendor.inventory:
        return False, "out of stock"
    if coins(state.player.inventory) < price:
        return False, f"you can't afford it ({price} coins)"
    _move_coins(state.player.inventory, vendor.inventory, price)
    _move_item(vendor.inventory, state.player.inventory, item)
    return True, f"bought for {price} coins"


def shop_sell(state, vendor, item: str) -> tuple[bool, str]:
    """Player sells an item to a vendor at the discounted price."""
    price = shop_sell_price(item)
    if item not in state.player.inventory:
        return False, "you don't have that"
    if coins(vendor.inventory) < price:
        return False, "they can't spare the coin"
    _move_item(state.player.inventory, vendor.inventory, item)
    _move_coins(vendor.inventory, state.player.inventory, price)
    return True, f"sold for {price} coins"


# What each vendor stocks, plus a day-rotated "special" and a coin float to buy with.
VENDOR_STOCK = {
    "sella": ["oil_flask", "oil_flask", "oil_flask", "bread", "bread",
              "tavern_stew", "scrap"],
}
VENDOR_SPECIALS = {
    "sella": ["tonic", "old_key", "tavern_stew", "tonic"],
}
VENDOR_FLOAT = 30


def is_vendor(vendor_id: str) -> bool:
    from npc.roster import load_character
    try:
        return load_character(vendor_id).get("kind") == "vendor"
    except KeyError:
        return False


def restock_vendor(state, vendor_id: str, day: int) -> bool:
    """Refill a vendor's stock for the given day (once per day). No-op for non-vendors."""
    if vendor_id not in state.npcs or not is_vendor(vendor_id):
        return False
    npc = state.npcs[vendor_id]
    if npc.flags.get("stocked_day") == day:
        return False
    stock = list(VENDOR_STOCK.get(vendor_id, []))
    specials = VENDOR_SPECIALS.get(vendor_id)
    if specials:
        stock.append(specials[day % len(specials)])
    stock += [CURRENCY] * VENDOR_FLOAT
    npc.inventory = stock
    npc.flags["stocked_day"] = day
    return True
