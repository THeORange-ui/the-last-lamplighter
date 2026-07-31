"""The party overlay (press P in the overworld): see who travels with you and
speak with a companion.

Pure UI: handle_event returns a command dict for the owner to execute
({"cmd": "talk", "npc": <id>} or {"cmd": "close"}), and draw() reads the live
party so the display always reflects the world. Parting ways is not a menu button —
you tell a companion to leave in conversation, and they decide to go.
"""
from __future__ import annotations

import pygame

from engine.state import affinity_label
from npc.roster import character_name
from ui import theme as T
from ui.inventory import _overlay
from ui.render import draw_text


class PartyPanel:
    def __init__(self, world):
        self.world = world
        self.sel = 0
        self.message = ""

    def _members(self):
        return list(self.world.party)

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_p, pygame.K_ESCAPE):
            return {"cmd": "close"}
        members = self._members()
        if not members:
            return None
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(members)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(members)
        elif event.key in (pygame.K_RETURN, pygame.K_e):
            return {"cmd": "talk", "npc": members[self.sel]}
        return None

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, "YOUR PARTY", (m, 20), T.font(26, bold=True), T.HEARTH)
        members = self._members()
        if not members:
            draw_text(screen, "No one travels with you yet.", (m, 78),
                      T.font(18), T.TEXT_DIM)
            draw_text(screen, "Ask a townsperson to come with you, and they may.",
                      (m, 106), T.font(15), T.TEXT_DIM)
        else:
            y = 78
            for i, nid in enumerate(members):
                npc = self.world.npcs.get(nid)
                sel = i == self.sel
                name = character_name(nid)
                mood = affinity_label(npc.affinity) if npc else ""
                label = f"{name}   ({mood})" if mood else name
                draw_text(screen, ("> " if sel else "   ") + label, (m, y),
                          T.font(20, bold=sel), T.HEARTH if sel else T.TEXT)
                y += 32
            draw_text(screen, "They fight at your side. Press Enter to talk — and if you",
                      (m, y + 12), T.font(15), T.TEXT_DIM)
            draw_text(screen, "ask them to, they'll part ways.",
                      (m, y + 34), T.font(15), T.TEXT_DIM)
        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 56), T.font(14), T.EFFECT)
        draw_text(screen, "Up/Down select · Enter talk · P/Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 30), T.font(14), T.TEXT_DIM, center=True)
