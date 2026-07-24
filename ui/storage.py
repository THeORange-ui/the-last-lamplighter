"""The camp storage chest overlay: stash items you don't want to carry.

Two columns — your pack and the chest — with Deposit / Withdraw. Coins stay on
your person (currency is filtered out). Mutates the world directly; handle_event
returns {"cmd": "close"} when the player backs out.
"""
from __future__ import annotations

import pygame

from engine.items import CURRENCY, display_name, value_of
from ui import theme as T
from ui.inventory import _draw_item_list, _overlay, grouped
from ui.render import draw_text


def _clamp(i, n):
    return 0 if n == 0 else max(0, min(i, n - 1))


class StoragePanel:
    def __init__(self, world):
        self.world = world
        self.side = "player"       # player | chest
        self.sel = 0
        self.message = ""

    def _list(self, side):
        inv = self.world.player.inventory if side == "player" else self.world.storage
        return [x for x in grouped(inv) if x[0] != CURRENCY]

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_ESCAPE,):
            return {"cmd": "close"}
        items = self._list(self.side)
        self.sel = _clamp(self.sel, len(items))
        if event.key in (pygame.K_LEFT, pygame.K_a):
            self.side = "player"; self.sel = 0; self.message = ""
        elif event.key in (pygame.K_RIGHT, pygame.K_d):
            self.side = "chest"; self.sel = 0; self.message = ""
        elif event.key in (pygame.K_UP, pygame.K_w) and items:
            self.sel = (self.sel - 1) % len(items)
        elif event.key in (pygame.K_DOWN, pygame.K_s) and items:
            self.sel = (self.sel + 1) % len(items)
        elif event.key in (pygame.K_RETURN, pygame.K_e) and items:
            self._move(items[self.sel][0])
        return None

    def _move(self, item):
        if self.side == "player":
            self.world.player.inventory.remove(item)
            self.world.storage.append(item)
            self.message = f"Stashed {display_name(item)}."
        else:
            self.world.storage.remove(item)
            self.world.player.inventory.append(item)
            self.message = f"Took {display_name(item)}."
        self.sel = _clamp(self.sel, len(self._list(self.side)))

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, "STORAGE — the camp chest", (m, 18),
                  T.font(24, bold=True), T.HEARTH)
        colw = T.SCREEN_W // 2
        draw_text(screen, "You", (m, 62), T.font(18, bold=True),
                  T.TEXT if self.side == "player" else T.TEXT_DIM)
        draw_text(screen, "Chest", (colw + 12, 62), T.font(18, bold=True),
                  T.TEXT if self.side == "chest" else T.TEXT_DIM)

        _draw_item_list(screen, m, 92, self._list("player"),
                        self.sel if self.side == "player" else -1,
                        active=self.side == "player", width=colw - m - 12)
        _draw_item_list(screen, colw + 12, 92, self._list("chest"),
                        self.sel if self.side == "chest" else -1,
                        active=self.side == "chest", width=colw - m - 12)

        items = self._list(self.side)
        ay = T.SCREEN_H - 96
        if items:
            item_id = items[self.sel][0]
            verb = "Deposit" if self.side == "player" else "Withdraw"
            draw_text(screen, f"{display_name(item_id)}  ·  worth {value_of(item_id)}",
                      (m, ay), T.font(15), T.TEXT_DIM)
            draw_text(screen, f"> {verb}", (m, ay + 24), T.font(18, bold=True), T.HEARTH)
        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 58),
                      T.font(15, bold=True), T.EFFECT)
        draw_text(screen, "Left/Right side · Up/Down item · Enter move · Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 26), T.font(14), T.TEXT_DIM, center=True)
