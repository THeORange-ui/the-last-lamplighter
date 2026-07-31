"""The player's notebook (N), and the only part of the game the player writes.

Emberhold tells you things in passing — who keeps oil, which door needs which word,
what somebody's brother was called — and the journal only ever recorded what the
*engine* noticed. This is for what you noticed. You keep a line out of a conversation
(Enter on it in the hub) and it lands here, with who said it and what day.

Nothing here is ever shown to an NPC. A character reacting to what you chose to write
down about them is a different feature with different problems; see `engine/state.py`.

Pure UI, like `ui/party.py`: `handle_event` returns a command dict for the owner.
"""
from __future__ import annotations

import pygame

from npc.roster import character_name
from ui import theme as T
from ui.inventory import _overlay
from ui.render import draw_text, wrap_text

LINE_H = 21


class NotesPanel:
    def __init__(self, world):
        self.world = world
        self.sel = max(0, len(world.notes) - 1)
        self.message = ""

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_n, pygame.K_ESCAPE):
            return {"cmd": "close"}
        notes = self.world.notes
        if not notes:
            return None
        self.sel = min(self.sel, len(notes) - 1)
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = max(0, self.sel - 1)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = min(len(notes) - 1, self.sel + 1)
        elif event.key in (pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_x):
            gone = notes.pop(self.sel)
            self.sel = max(0, min(self.sel, len(notes) - 1))
            self.message = f"Struck out: {gone['text'][:44]}"
        return None

    def _lines(self, max_w: int):
        out = []
        for i, note in enumerate(self.world.notes):
            who = character_name(note["source"]) if note.get("source") else "you"
            out.append((i, f"Day {note.get('day', 1)} · {who}",
                        T.font(13, bold=True), T.TEXT_DIM, 0))
            for ln in wrap_text(note["text"], T.font(16), max_w - 22):
                out.append((i, ln, T.font(16), T.TEXT, 14))
            out.append((i, "", T.font(6), T.TEXT, 0))
        return out

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, "YOUR NOTES", (m, 20), T.font(26, bold=True), T.HEARTH)
        notes = self.world.notes
        draw_text(screen, f"{len(notes)} kept", (T.SCREEN_W - m, 28),
                  T.font(14), T.TEXT_DIM, right=True)

        view = pygame.Rect(m, 72, T.SCREEN_W - m * 2, T.SCREEN_H - 72 - 64)
        if not notes:
            draw_text(screen, "Nothing written down yet.", (m + 6, 78),
                      T.font(18), T.TEXT_DIM)
            draw_text(screen, "While talking to someone, press Ctrl, pick a line, and "
                              "press Enter to keep it.", (m + 6, 108),
                      T.font(15), T.TEXT_DIM)
        else:
            lines = self._lines(view.width)
            visible = max(1, view.height // LINE_H)
            last = max(i for i, (idx, *_) in enumerate(lines) if idx <= self.sel)
            start = max(0, min(last - visible + 1, len(lines) - visible))
            screen.set_clip(view)
            y = view.top
            for idx, text, fnt, color, indent in lines[start:start + visible]:
                if idx == self.sel:
                    pygame.draw.rect(screen, (36, 34, 52),
                                     pygame.Rect(view.left - 6, y - 1,
                                                 view.width + 12, LINE_H))
                if text:
                    draw_text(screen, text, (view.left + indent, y), fnt, color)
                y += LINE_H
            screen.set_clip(None)

        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 56), T.font(14), T.EFFECT)
        draw_text(screen, "Up/Down select · Backspace strike out · N/Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 30), T.font(14), T.TEXT_DIM, center=True)
