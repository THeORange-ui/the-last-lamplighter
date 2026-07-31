"""An editable line of text with a real caret.

There are exactly two places in this game where the player types — what you say in a
conversation, and what you say to something that is trying to kill you — and both had
the same append-only box: no caret, backspace the only way back, so fixing a typo six
words ago meant retyping six words. This is that fixed, once, for both.

It owns editing and drawing but not layout: the caller asks for a `layout()` (which
says how many lines the text needs *right now*) and places it, because the dialogue box
grows its input upward into the reply and the combat box does not.

Wrapping is done as (start, end) index pairs rather than strings — the caret has to sit
at an exact character, and recovering offsets from already-wrapped strings is guesswork
the moment the text contains a double space.
"""
from __future__ import annotations

from typing import NamedTuple

import pygame

from ui import theme as T
from ui.render import draw_text

REPEAT_DELAY = 0.35      # hold time before a held edit key starts auto-repeating
REPEAT_INTERVAL = 0.045  # and how often it fires after that
# Keys that repeat while held. Left/Right are why the caret exists at all.
EDIT_KEYS = (pygame.K_BACKSPACE, pygame.K_DELETE, pygame.K_LEFT, pygame.K_RIGHT)
JUMP_KEYS = (pygame.K_HOME, pygame.K_END)
_WORD_BREAK = " \t-—,.;:!?\"'()[]"


def wrap_spans(text: str, fnt, width: int) -> list[tuple[int, int]]:
    """(start, end) per display line. Breaks after a space where there is one, hard-
    breaks where there isn't, and never fails to advance."""
    spans, i, n = [], 0, len(text)
    while i < n:
        lo, hi = i + 1, n                       # largest j where text[i:j] still fits
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fnt.size(text[i:mid])[0] <= width:
                lo = mid
            else:
                hi = mid - 1
        j = max(lo, i + 1)
        if j < n:
            k = text.rfind(" ", i + 1, j + 1)
            if k > i:
                j = k + 1
        spans.append((i, j))
        i = j
    return spans or [(0, 0)]


class Layout(NamedTuple):
    display: str                    # prefix + text, which is what gets drawn
    spans: list                     # every wrapped line, as (start, end)
    first: int                      # index of the first line shown
    shown: int                      # how many lines are shown
    cline: int                      # which line the caret is on
    cpos: int                       # caret's index into `display`


class TextInput:
    def __init__(self, max_len: int = 400):
        self.text = ""
        self.caret = 0
        self.max_len = max_len
        self._rep_key: int | None = None
        self._rep_timer = REPEAT_DELAY

    def clear(self) -> None:
        self.text, self.caret = "", 0

    # --- editing ----------------------------------------------------------
    def _word_edge(self, direction: int) -> int:
        """Where Alt-Left / Alt-Right lands: past the run of breaks, then the word."""
        i, n = self.caret, len(self.text)
        if direction < 0:
            while i > 0 and self.text[i - 1] in _WORD_BREAK:
                i -= 1
            while i > 0 and self.text[i - 1] not in _WORD_BREAK:
                i -= 1
        else:
            while i < n and self.text[i] in _WORD_BREAK:
                i += 1
            while i < n and self.text[i] not in _WORD_BREAK:
                i += 1
        return i

    def edit(self, key: int, mods: int) -> None:
        """One editing keystroke. Shared by key-down and hold-to-repeat, which is why
        it takes a key rather than an event."""
        word = bool(mods & pygame.KMOD_ALT)
        line = bool(mods & (pygame.KMOD_META | pygame.KMOD_CTRL))
        text, at = self.text, self.caret
        if key == pygame.K_LEFT:
            self.caret = 0 if line else (self._word_edge(-1) if word else max(0, at - 1))
        elif key == pygame.K_RIGHT:
            self.caret = (len(text) if line
                          else self._word_edge(1) if word else min(len(text), at + 1))
        elif key == pygame.K_BACKSPACE and at > 0:
            cut = 0 if line else self._word_edge(-1) if word else at - 1
            self.text, self.caret = text[:cut] + text[at:], cut
        elif key == pygame.K_DELETE and at < len(text):
            cut = len(text) if line else self._word_edge(1) if word else at + 1
            self.text = text[:at] + text[cut:]
        elif key == pygame.K_HOME:
            self.caret = 0
        elif key == pygame.K_END:
            self.caret = len(text)

    def insert(self, s: str) -> None:
        room = self.max_len - len(self.text)
        if room <= 0:
            return
        s = s[:room]
        self.text = self.text[:self.caret] + s + self.text[self.caret:]
        self.caret += len(s)

    def handle_key(self, event) -> bool:
        """True if this key was typing or editing, and so belongs to the input."""
        if event.key in EDIT_KEYS or event.key in JUMP_KEYS:
            self.edit(event.key, event.mod)
            self._rep_timer = REPEAT_DELAY     # pause before hold-repeat kicks in
            return True
        if event.unicode and event.unicode.isprintable():
            self.insert(event.unicode)
            return True
        return False

    def update(self, dt: float, active: bool) -> None:
        """Hold an edit key to keep it firing; the first one happens on key-down."""
        pressed = pygame.key.get_pressed() if active else None
        # `is not None`, not truthiness: what get_pressed returns is a sequence-like
        # object, and asking whether it is "empty" is not a question about key state.
        held = (next((k for k in EDIT_KEYS if pressed[k]), None)
                if pressed is not None else None)
        if held is None or held != self._rep_key:
            self._rep_key, self._rep_timer = held, REPEAT_DELAY
            return
        self._rep_timer -= dt
        if self._rep_timer <= 0:
            self.edit(held, pygame.key.get_mods())
            self._rep_timer = REPEAT_INTERVAL

    # --- drawing ----------------------------------------------------------
    def layout(self, fnt, width: int, *, prefix: str = "> ", max_lines: int = 3) -> Layout:
        display = prefix + self.text
        cpos = len(prefix) + self.caret
        spans = wrap_spans(display, fnt, width)
        cline = next((i for i, (_, b) in enumerate(spans) if cpos < b), len(spans) - 1)
        shown = min(max_lines, len(spans))
        # Window the lines around the caret rather than showing the tail: the whole
        # point of a caret is going back to fix something, and a tail hides you doing it.
        first = max(0, min(cline - shown + 1, len(spans) - shown))
        return Layout(display, spans, first, shown, cline, cpos)

    def draw(self, screen, pos, fnt, lay: Layout, *, line_h: int = 22,
             blink: float = 0.0, color=T.TEXT) -> None:
        x, y = pos
        for idx in range(lay.first, lay.first + lay.shown):
            a, b = lay.spans[idx]
            draw_text(screen, lay.display[a:b], (x, y), fnt, color)
            if idx == lay.cline and blink < 0.55:
                cx = x + fnt.size(lay.display[a:lay.cpos])[0]
                pygame.draw.line(screen, color, (cx, y + 1), (cx, y + 19), 2)
            y += line_h
