"""The shop overlay for a vendor NPC: buy from their daily stock, sell from your
pack. Prices carry a margin (buy above catalog value, sell below), handled by
engine/trade.py. Mutates the world directly and reports the outcome inline.
"""
from __future__ import annotations

import pygame

from engine.items import CURRENCY, display_name
from engine.trade import (coins, shop_buy, shop_buy_price, shop_sell,
                          shop_sell_price)
from ui import theme as T
from ui.inventory import _draw_item_list, _overlay, grouped
from ui.render import draw_text


def _clamp(i, n):
    return 0 if n == 0 else max(0, min(i, n - 1))


class ShopPanel:
    def __init__(self, world, npc_id, npc_name):
        self.world = world
        self.npc_id = npc_id
        self.npc_name = npc_name
        self.side = "shop"         # shop | player
        self.sel = 0
        self.message = ""

    @property
    def _npc(self):
        return self.world.npcs[self.npc_id]

    def _list(self, side):
        inv = self._npc.inventory if side == "shop" else self.world.player.inventory
        return [x for x in grouped(inv) if x[0] != CURRENCY]

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_ESCAPE,) + _SHOP_CLOSE_KEYS:
            return {"cmd": "close"}
        items = self._list(self.side)
        self.sel = _clamp(self.sel, len(items))
        if event.key in (pygame.K_LEFT, pygame.K_a):
            self.side = "shop"; self.sel = 0; self.message = ""
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            self.side = "player"; self.sel = 0; self.message = ""
        elif event.key in (pygame.K_UP, pygame.K_w) and items:
            self.sel = (self.sel - 1) % len(items)
        elif event.key in (pygame.K_DOWN, pygame.K_s) and items:
            self.sel = (self.sel + 1) % len(items)
        elif event.key in (pygame.K_RETURN, pygame.K_e) and items:
            self._deal(items[self.sel][0])
        return None

    def _deal(self, item):
        if self.side == "shop":
            ok, why = shop_buy(self.world, self._npc, item)
            self.message = (f"Bought the {display_name(item)} ({why})." if ok
                            else f"Can't buy the {display_name(item)}: {why}.")
        else:
            ok, why = shop_sell(self.world, self._npc, item)
            self.message = (f"Sold the {display_name(item)} ({why})." if ok
                            else f"Can't sell the {display_name(item)}: {why}.")
        self.sel = _clamp(self.sel, len(self._list(self.side)))

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, f"SHOP — {self.npc_name}'s stall", (m, 18),
                  T.font(24, bold=True), T.HEARTH)
        pc = coins(self.world.player.inventory)
        colw = T.SCREEN_W // 2
        draw_text(screen, f"Wares (buy)", (m, 62), T.font(18, bold=True),
                  T.TEXT if self.side == "shop" else T.TEXT_DIM)
        draw_text(screen, f"You  ({pc} coins) — sell", (colw + 12, 62),
                  T.font(18, bold=True), T.TEXT if self.side == "player" else T.TEXT_DIM)

        _draw_priced_list(screen, m, 92, self._list("shop"),
                          self.sel if self.side == "shop" else -1,
                          active=self.side == "shop", price=shop_buy_price)
        _draw_priced_list(screen, colw + 12, 92, self._list("player"),
                          self.sel if self.side == "player" else -1,
                          active=self.side == "player", price=shop_sell_price)

        items = self._list(self.side)
        ay = T.SCREEN_H - 96
        if items:
            item_id = items[self.sel][0]
            if self.side == "shop":
                draw_text(screen, f"> Buy the {display_name(item_id)} "
                          f"for {shop_buy_price(item_id)} coins",
                          (m, ay), T.font(18, bold=True), T.HEARTH)
            else:
                draw_text(screen, f"> Sell the {display_name(item_id)} "
                          f"for {shop_sell_price(item_id)} coins",
                          (m, ay), T.font(18, bold=True), T.HEARTH)
        else:
            draw_text(screen, "(nothing here)", (m, ay), T.font(16), T.TEXT_DIM)
        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 58),
                      T.font(15, bold=True), T.EFFECT)
        draw_text(screen, "Left/Right side · Up/Down item · Enter deal · Ctrl/Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 26), T.font(14), T.TEXT_DIM, center=True)


# Ctrl/Cmd (the key that opened the shop) also closes it.
_SHOP_CLOSE_KEYS = (pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LMETA, pygame.K_RMETA)


def _draw_priced_list(screen, x, y, items, sel, *, active, price):
    if not items:
        draw_text(screen, "(empty)", (x + 6, y), T.font(16), T.TEXT_DIM)
        return
    for i, (item_id, n) in enumerate(items):
        selected = i == sel
        color = T.HEARTH if (selected and active) else (T.TEXT if selected else T.TEXT_DIM)
        label = display_name(item_id) + (f"  x{n}" if n > 1 else "")
        draw_text(screen, ("> " if selected else "  ") + label, (x + 6, y),
                  T.font(17, bold=selected), color)
        draw_text(screen, f"{price(item_id)}c", (x + 6 + 250, y),
                  T.font(15), T.TEXT_DIM)
        y += 26
