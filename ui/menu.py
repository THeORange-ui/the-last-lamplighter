"""The in-game pause menu (Esc): Continue / Save / Load / Save As / Save and Quit.

Menu.handle_event returns a command dict for the Game to act on, or None. The
Game owns the actual save/load side effects; the menu only drives selection and
the Save-As text entry.
"""
from __future__ import annotations

import pygame

from engine.save import list_saves
from ui import theme as T
from ui.render import draw_text

MAIN_OPTIONS = ["Continue", "Settings", "Save", "Load", "Save As",
                "Save and Quit"]


class Menu:
    def __init__(self):
        self.mode = "main"        # main | load | save_as
        self.sel = 0
        self.saves: list[str] = []
        self.input_text = ""
        self._caret = 0.0

    def open(self):
        self.mode = "main"
        self.sel = 0

    def update(self, dt):
        self._caret = (self._caret + dt) % 1.0

    # --- input ------------------------------------------------------------
    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if self.mode == "main":
            return self._main_key(event)
        if self.mode == "load":
            return self._load_key(event)
        if self.mode == "save_as":
            return self._save_as_key(event)
        return None

    def _main_key(self, event):
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(MAIN_OPTIONS)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(MAIN_OPTIONS)
        elif event.key == pygame.K_ESCAPE:
            return {"cmd": "close"}
        elif event.key in (pygame.K_RETURN, pygame.K_e, pygame.K_SPACE):
            choice = MAIN_OPTIONS[self.sel]
            if choice == "Continue":
                return {"cmd": "close"}
            if choice == "Settings":
                return {"cmd": "settings"}
            if choice == "Save":
                return {"cmd": "save"}
            if choice == "Load":
                self.saves = list_saves()
                self.sel = 0
                self.mode = "load"
            elif choice == "Save As":
                self.input_text = ""
                self.mode = "save_as"
            elif choice == "Save and Quit":
                return {"cmd": "save_quit"}
        return None

    def _load_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.mode = "main"
            self.sel = 0
        elif not self.saves:
            return None
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(self.saves)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(self.saves)
        elif event.key in (pygame.K_RETURN, pygame.K_e):
            return {"cmd": "load", "name": self.saves[self.sel]}
        return None

    def _save_as_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.mode = "main"
        elif event.key == pygame.K_RETURN:
            name = self.input_text.strip()
            if name:
                return {"cmd": "save_as", "name": name}
        elif event.key == pygame.K_BACKSPACE:
            self.input_text = self.input_text[:-1]
        elif event.unicode and event.unicode.isprintable() and len(self.input_text) < 30:
            self.input_text += event.unicode
        return None

    # --- draw -------------------------------------------------------------
    def draw(self, screen, current_slot):
        overlay = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 210))
        screen.blit(overlay, (0, 0))
        cx = T.SCREEN_W // 2

        draw_text(screen, "MENU", (cx, 90), T.font(30, bold=True), T.HEARTH, center=True)
        draw_text(screen, f"current save: {current_slot}", (cx, 128),
                  T.font(14), T.TEXT_DIM, center=True)

        if self.mode == "main":
            self._draw_list(screen, cx, MAIN_OPTIONS, self.sel)
            hint = "Up/Down select · Enter choose · Esc close"
        elif self.mode == "load":
            if self.saves:
                self._draw_list(screen, cx, self.saves, self.sel)
            else:
                draw_text(screen, "(no saved games yet)", (cx, 200),
                          T.font(18), T.TEXT_DIM, center=True)
            hint = "Up/Down select · Enter load · Esc back"
        else:  # save_as
            draw_text(screen, "Name this save:", (cx, 190), T.font(18), T.TEXT, center=True)
            caret = "|" if self._caret < 0.5 else " "
            box = pygame.Rect(cx - 180, 220, 360, 40)
            pygame.draw.rect(screen, T.BOX_BG, box)
            pygame.draw.rect(screen, T.BOX_BORDER, box, 2, border_radius=4)
            draw_text(screen, self.input_text + caret, (box.left + 10, box.top + 9),
                      T.font(18, mono=True), T.TEXT)
            hint = "type a name · Enter save · Esc back"

        draw_text(screen, hint, (cx, T.SCREEN_H - 60), T.font(14), T.TEXT_DIM, center=True)

    def _draw_list(self, screen, cx, options, sel):
        y = 180
        for i, opt in enumerate(options):
            selected = i == sel
            color = T.HEARTH if selected else T.TEXT
            prefix = "> " if selected else "   "
            draw_text(screen, prefix + opt, (cx, y),
                      T.font(20, bold=selected), color, center=True)
            y += 34
