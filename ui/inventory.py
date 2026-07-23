"""Inventory overlays.

InventoryPanel — the out-of-conversation inventory (press I in the overworld):
browse items, Use or Drop them.

TradePanel — the in-conversation trade view (press I while talking): both your
inventory and the NPC's, with Gift / Sell on your items and Ask / Buy on theirs.

Both are pure UI: handle_event returns a command dict for the owner to execute,
and draw() reads live inventories so the display always reflects the world.
"""
from __future__ import annotations

import pygame

from engine.items import CURRENCY, describe, display_name, use_verb, value_of
from ui import theme as T
from ui.render import draw_text, wrap_text


def grouped(inventory: list[str]) -> list[tuple[str, int]]:
    """Item ids with counts, in first-seen order, coins last."""
    order: list[str] = []
    counts: dict[str, int] = {}
    for it in inventory:
        if it not in counts:
            order.append(it)
        counts[it] = counts.get(it, 0) + 1
    order.sort(key=lambda i: (i == CURRENCY))
    return [(i, counts[i]) for i in order]


def _clamp(i, n):
    return 0 if n == 0 else max(0, min(i, n - 1))


class InventoryPanel:
    def __init__(self, world):
        self.world = world
        self.sel = 0
        self.mode = "items"        # items | actions
        self.action_sel = 0
        self.message = ""

    def _items(self):
        return [x for x in grouped(self.world.player.inventory) if x[0] != CURRENCY]

    def _actions(self, item_id):
        acts = []
        v = use_verb(item_id)
        if v:
            acts.append(("use", v))
        acts.append(("drop", "Drop"))
        return acts

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        items = self._items()
        self.sel = _clamp(self.sel, len(items))
        if event.key in (pygame.K_i, pygame.K_ESCAPE):
            if self.mode == "actions":
                self.mode = "items"
                return None
            return {"cmd": "close"}
        if not items:
            return None
        if self.mode == "items":
            if event.key in (pygame.K_UP, pygame.K_w):
                self.sel = (self.sel - 1) % len(items)
                self.message = ""
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.sel = (self.sel + 1) % len(items)
                self.message = ""
            elif event.key in (pygame.K_RETURN, pygame.K_e):
                self.mode = "actions"
                self.action_sel = 0
        else:  # actions
            acts = self._actions(items[self.sel][0])
            if event.key in (pygame.K_UP, pygame.K_w):
                self.action_sel = (self.action_sel - 1) % len(acts)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.action_sel = (self.action_sel + 1) % len(acts)
            elif event.key in (pygame.K_RETURN, pygame.K_e):
                kind = acts[self.action_sel][0]
                self.mode = "items"
                return {"cmd": kind, "item": items[self.sel][0]}
        return None

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, "INVENTORY", (m, 20), T.font(26, bold=True), T.HEARTH)
        p = self.world.player
        draw_text(screen, f"HP {p.hp}/{p.max_hp}    Coins {p.inventory.count(CURRENCY)}",
                  (T.SCREEN_W - m, 26), T.font(15), T.TEXT_DIM, right=True)

        items = self._items()
        _draw_item_list(screen, m, 70, items, self.sel, active=(self.mode == "items"))

        # right detail column
        rx = T.SCREEN_W // 2 + 10
        if items:
            item_id = items[self.sel][0]
            draw_text(screen, display_name(item_id), (rx, 70), T.font(20, bold=True), T.TEXT)
            y = 100
            for ln in wrap_text(describe(item_id), T.font(15), T.SCREEN_W - rx - 28):
                draw_text(screen, ln, (rx, y), T.font(15), T.TEXT_DIM)
                y += 22
            draw_text(screen, f"Worth: {value_of(item_id)} coins", (rx, y + 6),
                      T.font(14), T.TEXT_DIM)
            y += 40
            for i, (_, label) in enumerate(self._actions(item_id)):
                sel = self.mode == "actions" and i == self.action_sel
                draw_text(screen, ("> " if sel else "   ") + label, (rx, y),
                          T.font(18, bold=sel), T.HEARTH if sel else T.TEXT)
                y += 28
            if self.message:
                y += 10
                for ln in wrap_text(self.message, T.font(15), T.SCREEN_W - rx - 28):
                    draw_text(screen, ln, (rx, y), T.font(15), T.TEXT_GOOD)
                    y += 22
        else:
            draw_text(screen, "Your pack is empty.", (rx, 70), T.font(16), T.TEXT_DIM)

        hint = ("Up/Down choose · Enter act · Esc back" if self.mode == "actions"
                else "Up/Down select · Enter item · I/Esc close")
        draw_text(screen, hint, (T.SCREEN_W // 2, T.SCREEN_H - 30),
                  T.font(14), T.TEXT_DIM, center=True)


class TradePanel:
    def __init__(self, world, npc_id, npc_name):
        self.world = world
        self.npc_id = npc_id
        self.npc_name = npc_name
        self.side = "player"       # player | npc
        self.sel = 0
        self.mode = "items"
        self.action_sel = 0
        self.message = ""

    @property
    def _npc(self):
        return self.world.npcs[self.npc_id]

    def _list(self, side):
        inv = self.world.player.inventory if side == "player" else self._npc.inventory
        return [x for x in grouped(inv) if x[0] != CURRENCY]

    def _actions(self, side):
        return ([("gift", "Gift"), ("sell", "Sell")] if side == "player"
                else [("ask", "Ask for"), ("buy", "Buy")])

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_i, pygame.K_ESCAPE):
            if self.mode == "actions":
                self.mode = "items"
                return None
            return {"cmd": "close"}
        items = self._list(self.side)
        self.sel = _clamp(self.sel, len(items))
        if self.mode == "items":
            if event.key in (pygame.K_LEFT, pygame.K_a):
                self.side = "player"; self.sel = 0; self.message = ""
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self.side = "npc"; self.sel = 0; self.message = ""
            elif event.key in (pygame.K_UP, pygame.K_w) and items:
                self.sel = (self.sel - 1) % len(items)
            elif event.key in (pygame.K_DOWN, pygame.K_s) and items:
                self.sel = (self.sel + 1) % len(items)
            elif event.key in (pygame.K_RETURN, pygame.K_e) and items:
                self.mode = "actions"; self.action_sel = 0
        else:  # actions
            acts = self._actions(self.side)
            if event.key in (pygame.K_UP, pygame.K_w):
                self.action_sel = (self.action_sel - 1) % len(acts)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.action_sel = (self.action_sel + 1) % len(acts)
            elif event.key in (pygame.K_RETURN, pygame.K_e):
                kind = acts[self.action_sel][0]
                self.mode = "items"
                return {"cmd": kind, "item": items[self.sel][0]}
        return None

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, f"TRADE — with {self.npc_name}", (m, 18),
                  T.font(24, bold=True), T.HEARTH)

        pc = self.world.player.inventory.count(CURRENCY)
        nc = self._npc.inventory.count(CURRENCY)
        colw = T.SCREEN_W // 2
        draw_text(screen, f"You  ({pc} coins)", (m, 62), T.font(18, bold=True),
                  T.TEXT if self.side == "player" else T.TEXT_DIM)
        draw_text(screen, f"{self.npc_name}  ({nc} coins)", (colw + 12, 62),
                  T.font(18, bold=True), T.TEXT if self.side == "npc" else T.TEXT_DIM)

        _draw_item_list(screen, m, 92, self._list("player"),
                        self.sel if self.side == "player" else -1,
                        active=self.side == "player", width=colw - m - 12)
        _draw_item_list(screen, colw + 12, 92, self._list("npc"),
                        self.sel if self.side == "npc" else -1,
                        active=self.side == "npc", width=colw - m - 12)

        # action row for the active side / selected item
        items = self._list(self.side)
        ay = T.SCREEN_H - 118
        if items:
            item_id = items[self.sel][0]
            draw_text(screen, f"{display_name(item_id)}  ·  {value_of(item_id)} coins",
                      (m, ay), T.font(15), T.TEXT_DIM)
            x = m
            for i, (_, label) in enumerate(self._actions(self.side)):
                sel = self.mode == "actions" and i == self.action_sel
                draw_text(screen, ("> " if sel else "  ") + label, (x, ay + 24),
                          T.font(18, bold=sel), T.HEARTH if sel else T.TEXT)
                x += 150
        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 58),
                      T.font(15, bold=True), T.EFFECT)
        draw_text(screen, "Left/Right side · Up/Down item · Enter act · I/Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 28), T.font(14), T.TEXT_DIM, center=True)


# --- shared drawing ----------------------------------------------------------
def _overlay(screen):
    o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
    o.fill((0, 0, 0, 224))
    screen.blit(o, (0, 0))


def _draw_item_list(screen, x, y, items, sel, *, active, width=None):
    if not items:
        draw_text(screen, "(empty)", (x + 6, y), T.font(16), T.TEXT_DIM)
        return
    for i, (item_id, n) in enumerate(items):
        selected = i == sel
        color = T.HEARTH if (selected and active) else (T.TEXT if selected else T.TEXT_DIM)
        label = display_name(item_id) + (f"  x{n}" if n > 1 else "")
        prefix = "> " if selected else "  "
        draw_text(screen, prefix + label, (x + 6, y), T.font(17, bold=selected), color)
        y += 26
