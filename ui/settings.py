"""The settings page (Esc → Settings): how the game presents itself.

Only presentation lives here — nothing in this panel may change what happens in the
world, which is why it can be opened from the pause menu at any time without a save.

**Key bindings are shown, not bound.** Keys are hardcoded across `main.handle_events`,
the dialogue box, the hub and eight panels; making them rebindable means threading an
action-name layer through all of it, which is its own piece of work rather than a line
on this page. Until then the reference is worth having on its own, because the HUD hint
row cannot hold everything.

Pure UI, like `ui/party.py`: `handle_event` returns a command dict. It writes prefs
itself (via `engine/prefs.py`) because a preference is not world state and there is no
owner to hand it to.
"""
from __future__ import annotations

import pygame

from engine import prefs
from ui import theme as T
from ui.inventory import _overlay
from ui.render import draw_text

# (key, label, what it does when you're wondering why you'd want it)
OPTIONS = [
    ("paged_dialogue", "Replies in beats",
     "Break a reply into short beats you page through, instead of showing it whole."),
    ("text_speed", "Text speed",
     "How fast a reply types itself out."),
]

KEYS_OVERWORLD = [
    ("Arrows / WASD", "move"), ("E", "talk, or use what you're standing by"),
    ("R", "make camp"), ("I", "inventory"), ("P", "party"), ("N", "notes"),
    ("M", "map"), ("J", "journal"), ("Esc", "this menu"),
]
KEYS_TALKING = [
    ("Type + Enter", "say something"), ("Ctrl / Cmd", "the conversation hub"),
    ("Up / Down", "scroll their reply"), ("Left / Right", "move the caret"),
    ("Alt + Left/Right", "by word"), ("Cmd + Left/Right", "to the ends"),
    ("Esc", "walk away"),
]
KEYS_HUB = [
    ("I", "trade or shop"), ("P", "bring a companion in"), ("J", "journal"),
    ("M", "map"), ("N", "notes"), ("Enter", "keep the selected line as a note"),
]


class SettingsPanel:
    def __init__(self):
        self.sel = 0
        self.message = ""

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_ESCAPE, pygame.K_q):
            return {"cmd": "close"}
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(OPTIONS)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(OPTIONS)
        elif event.key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d,
                           pygame.K_RETURN, pygame.K_SPACE):
            back = event.key in (pygame.K_LEFT, pygame.K_a)
            self._cycle(OPTIONS[self.sel][0], -1 if back else 1)
        return None

    def _cycle(self, key: str, step: int) -> None:
        if key == "paged_dialogue":
            prefs.set(key, not prefs.get(key))
        elif key == "text_speed":
            speeds = prefs.TEXT_SPEEDS
            cur = prefs.get(key)
            i = speeds.index(cur) if cur in speeds else 2
            prefs.set(key, speeds[(i + step) % len(speeds)])
        self.message = "Saved."

    @staticmethod
    def _value(key: str) -> str:
        if key == "paged_dialogue":
            return "on" if prefs.get(key) else "off"
        if key == "text_speed":
            return prefs.speed_label(prefs.get(key))
        return str(prefs.get(key))

    def draw(self, screen):
        _overlay(screen)
        m = 28
        draw_text(screen, "SETTINGS", (m, 20), T.font(26, bold=True), T.HEARTH)

        y = 74
        for i, (key, label, blurb) in enumerate(OPTIONS):
            sel = i == self.sel
            draw_text(screen, ("> " if sel else "   ") + label, (m, y),
                      T.font(20, bold=sel), T.HEARTH if sel else T.TEXT)
            draw_text(screen, f"< {self._value(key)} >", (m + 250, y),
                      T.font(20, bold=sel), T.TEXT if sel else T.TEXT_DIM)
            y += 26
            draw_text(screen, blurb, (m + 22, y), T.font(13), T.TEXT_DIM)
            y += 30

        y += 6
        pygame.draw.line(screen, T.WALL, (m, y), (T.SCREEN_W - m, y), 1)
        y += 12
        draw_text(screen, "Keys", (m, y), T.font(17, bold=True), T.TEXT)
        draw_text(screen, "(fixed for now)", (m + 60, y + 3), T.font(12), T.TEXT_DIM)
        y += 26

        col_w = (T.SCREEN_W - m * 2) // 3
        for col, (title, rows) in enumerate(
                [("Walking about", KEYS_OVERWORLD), ("Talking", KEYS_TALKING),
                 ("In the hub", KEYS_HUB)]):
            x = m + col * col_w
            draw_text(screen, title, (x, y), T.font(13, bold=True), T.TEXT_WARN)
            ky = y + 20
            for combo, what in rows:
                draw_text(screen, combo, (x, ky), T.font(12), T.TEXT)
                draw_text(screen, what, (x, ky + 13), T.font(11), T.TEXT_DIM)
                ky += 30

        if self.message:
            draw_text(screen, self.message, (m, T.SCREEN_H - 56), T.font(14), T.EFFECT)
        draw_text(screen, "Up/Down pick · Left/Right change · Esc back",
                  (T.SCREEN_W // 2, T.SCREEN_H - 30), T.font(14), T.TEXT_DIM, center=True)
