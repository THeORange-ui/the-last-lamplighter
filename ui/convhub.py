"""The pause inside a conversation (Ctrl/Cmd while talking).

A conversation used to be a trap: once you were in one the only keys that did anything
were typing, Ctrl for trade, and Esc to walk away. You could not check what a quest
actually said, look at where someone lives, or re-read what this person told you two
lines ago — all of which are things you want *during* a conversation far more than
outside one.

So Ctrl now opens this instead of going straight to trade. It shows the exchange so far
and puts the ordinary function keys back within reach; trade is one of them rather than
the only one. Esc returns you to the conversation, which is still sitting there.

Pure UI, like `ui/party.py`: `handle_event` returns a command dict and the owner
(`ui/dialogue.py`) executes it. This panel never touches world state.
"""
from __future__ import annotations

import pygame

from engine.state import affinity_label
from npc.roster import character_name
from ui import theme as T
from ui.render import draw_text, wrap_text

LINE_H = 21
BODY_TOP = 78
FOOTER_H = 62
YOU = "you"          # transcript speaker id for the player


class ConvHub:
    def __init__(self, world, npc_id: str, transcript: list, *, is_vendor: bool = False):
        self.world = world
        self.npc_id = npc_id
        self.name = character_name(npc_id)
        self.transcript = transcript      # the live list, so it stays current
        self.is_vendor = is_vendor
        self.sel = max(0, len(transcript) - 1)
        self.message = ""

    # --- events -----------------------------------------------------------
    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key == pygame.K_ESCAPE:
            return {"cmd": "close"}
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = max(0, self.sel - 1)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = min(len(self.transcript) - 1, self.sel + 1)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if 0 <= self.sel < len(self.transcript):
                who, text = self.transcript[self.sel]
                return {"cmd": "note", "text": text, "source": "" if who == YOU else who}
        elif event.key == pygame.K_i:
            return {"cmd": "open", "what": "trade"}
        elif event.key == pygame.K_p:
            return {"cmd": "open", "what": "party"}
        elif event.key == pygame.K_j:
            return {"cmd": "open", "what": "journal"}
        elif event.key == pygame.K_m:
            return {"cmd": "open", "what": "map"}
        elif event.key == pygame.K_n:
            return {"cmd": "open", "what": "notes"}
        return None

    # --- drawing ----------------------------------------------------------
    def _lines(self, max_w: int) -> list[tuple[int, str, object, tuple, int]]:
        """(entry index, text, font, colour, indent) — flat, so scrolling is a slice."""
        out = []
        for i, (who, text) in enumerate(self.transcript):
            you = who == YOU
            label = "You" if you else character_name(who)
            color = T.TEXT if you else T.npc_color(who)
            out.append((i, label, T.font(14, bold=True), color, 0))
            body = T.font(16)
            for ln in wrap_text(text, body, max_w - 22):
                out.append((i, ln, body, T.TEXT if you else T.TEXT_DIM, 14))
            out.append((i, "", T.font(6), T.TEXT, 0))
        return out

    def draw(self, screen):
        o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        o.fill((6, 6, 12, 252))
        screen.blit(o, (0, 0))

        m = 28
        draw_text(screen, f"TALKING WITH {self.name.upper()}", (m, 18),
                  T.font(24, bold=True), T.npc_color(self.npc_id))
        npc = self.world.npcs.get(self.npc_id)
        if npc is not None:
            draw_text(screen, affinity_label(npc.affinity), (T.SCREEN_W - m, 26),
                      T.font(14), T.TEXT_DIM, right=True)
        pygame.draw.line(screen, T.WALL, (m, BODY_TOP - 12),
                         (T.SCREEN_W - m, BODY_TOP - 12), 1)

        view = pygame.Rect(m, BODY_TOP, T.SCREEN_W - m * 2,
                           T.SCREEN_H - BODY_TOP - FOOTER_H)
        lines = self._lines(view.width)
        if not lines:
            draw_text(screen, "Nothing said yet.", (m + 6, BODY_TOP + 6),
                      T.font(16), T.TEXT_DIM)
        else:
            visible = max(1, view.height // LINE_H)
            # Keep the selected entry on screen: scroll so its last line is the last
            # thing drawn, unless the whole thing already fits.
            last = max(i for i, (idx, *_) in enumerate(lines) if idx <= self.sel)
            start = max(0, min(last - visible + 1, len(lines) - visible))
            start = max(0, start)
            screen.set_clip(view)
            y = view.top
            for idx, text, fnt, color, indent in lines[start:start + visible]:
                if idx == self.sel:
                    pygame.draw.rect(screen, (34, 32, 50),
                                     pygame.Rect(view.left - 6, y - 1,
                                                 view.width + 12, LINE_H))
                if text:
                    draw_text(screen, text, (view.left + indent, y), fnt, color)
                y += LINE_H
            screen.set_clip(None)

        y = T.SCREEN_H - FOOTER_H + 4
        if self.message:
            draw_text(screen, self.message, (m, y), T.font(14), T.EFFECT)
        keys = [("I", "shop" if self.is_vendor else "trade"), ("P", "party"),
                ("J", "journal"), ("M", "map"), ("N", "notes")]
        draw_text(screen, "  ".join(f"[{k}] {v}" for k, v in keys), (m, y + 20),
                  T.font(14), T.TEXT_DIM)
        draw_text(screen, "Up/Down · [Enter] keep the line · [Esc] back",
                  (T.SCREEN_W - m, y + 20), T.font(14), T.TEXT_DIM, right=True)
