"""Mechanical item/coin transfers for the economy.

Prices come from the item catalog's `value`. These functions only move items and
coins between inventories and report success — flavor text and any AI reactions
are the caller's job. Gifting and asking are handled through the dialogue AI, not
here; this module is the deterministic buy/sell/give plumbing.
"""
from __future__ import annotations

from engine.items import CURRENCY, value_of


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
