"""The player's journal (J): what the world has done, and what it wants from you.

Sections fold. Everything used to be drawn in one pass down the screen and simply run
off the bottom — the log was cut off wherever the page ended, and by a dozen quests in
there was no way to reach any of it. It is now built as a flat list of lines with a
section index attached (the same shape `ui/convhub.py` uses), so scrolling is a slice
and folding a section is a filter.

Items are deliberately **not** here. That list was carried over from before there was
an inventory; the inventory owns it now, and duplicating it only made this page longer
in a place you cannot fold away.
"""
from __future__ import annotations

import pygame

from ui import theme as T
from ui.render import IDLE_HINT, draw_text, wrap_text

LINE_H = 21
BODY_TOP = 66
FOOTER = 44


class JournalPanel:
    """Owned by `main.Game` and kept between openings, so a section you folded away
    stays folded for the session. Pure UI: it reads the world and never writes it."""

    def __init__(self, world):
        self.world = world
        self.sel = 0
        self.scroll = 0
        self.folded: set[str] = set()
        self._sections: list[tuple[str, str]] = []      # (key, title), rebuilt on draw

    # --- events -----------------------------------------------------------
    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return None
        if event.key in (pygame.K_j, pygame.K_ESCAPE):
            return {"cmd": "close"}
        n = max(1, len(self._sections))
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % n
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % n
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_LEFT,
                           pygame.K_RIGHT, pygame.K_a, pygame.K_d):
            if self._sections:
                key = self._sections[self.sel][0]
                self.folded.symmetric_difference_update({key})
        elif event.key == pygame.K_PAGEUP:
            self.scroll = max(0, self.scroll - 8)
        elif event.key == pygame.K_PAGEDOWN:
            self.scroll += 8
        return None

    # --- content ----------------------------------------------------------
    def _content(self, max_w: int):
        """(section index, text, font, colour, indent) for everything on the page."""
        w = self.world
        active = w.active_quests()
        completed = [q for q in w.quests if q.status == "complete"]
        failed = [q for q in w.quests if q.status == "failed"]
        last_night = w.flags.get("last_night_reports") or []

        secs: list[tuple[str, str, list]] = []

        # What the world did while you slept. It was always in the log, but a flat
        # chronological list is the wrong shape for the one question you have on waking.
        if last_night:
            secs.append(("night", "Last night",
                         [(f"· {line}", T.font(15), T.TEXT, 8) for line in last_night]))

        rows = []
        if not active and not completed and not failed:
            rows.append(("Nothing yet. Talk to the townsfolk.", T.font(16), T.TEXT_DIM, 8))
        elif not active:
            # Everything you were carrying is done. Say what comes next rather than
            # leaving a list of ticked-off titles and no direction.
            rows.append((IDLE_HINT, T.font(16), T.TEXT_WARN, 8))
        for q in active:
            chain = "  (continues your path)" if getattr(q, "parent", None) else ""
            rows.append((f"• {q.title}  ({q.progress}/{q.objective.count}){chain}",
                         T.font(16, bold=True), T.TEXT_WARN, 8))
            for ln in wrap_text(q.description, T.font(14), max_w - 40):
                rows.append((ln, T.font(14), T.TEXT_DIM, 24))
        secs.append(("active", f"Quests ({len(active)})", rows))

        if completed:
            secs.append(("done", f"Finished ({len(completed)})",
                         [(f"• {q.title}", T.font(16), T.TEXT_GOOD, 8)
                          for q in completed]))
        # Called off by whoever asked. Kept on the page rather than quietly removed: a
        # thread that came to nothing is still something that happened to you.
        if failed:
            secs.append(("failed", f"Came to nothing ({len(failed)})",
                         [(f"• {q.title}", T.font(16), T.TEXT_BAD, 8) for q in failed]))

        events = w.events.all_newest_first()
        secs.append(("log", f"Log ({len(events)})",
                     [(f"· {e.text}", T.font(15), T.TEXT, 8) for e in events]
                     or [("—", T.font(16), T.TEXT_DIM, 8)]))

        self._sections = [(key, title) for key, title, _ in secs]
        self.sel = min(self.sel, max(0, len(secs) - 1))

        lines = []
        for i, (key, title, rows) in enumerate(secs):
            folded = key in self.folded
            mark = "+" if folded else "-"
            lines.append((i, f"[{mark}] {title}", T.font(18, bold=True),
                          T.HEARTH if i == self.sel else T.TEXT, 0))
            if not folded:
                lines += [(i, *row) for row in rows]
            lines.append((i, "", T.font(6), T.TEXT, 0))
        return lines

    # --- drawing ----------------------------------------------------------
    def draw(self, screen):
        o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        o.fill((0, 0, 0, 232))
        screen.blit(o, (0, 0))
        m = 28
        draw_text(screen, "JOURNAL", (m, 20), T.font(26, bold=True), T.HEARTH)
        draw_text(screen, f"Day {self.world.day}", (T.SCREEN_W - m, 26),
                  T.font(14), T.TEXT_DIM, right=True)

        view = pygame.Rect(m, BODY_TOP, T.SCREEN_W - m * 2,
                           T.SCREEN_H - BODY_TOP - FOOTER)
        lines = self._content(view.width)
        visible = max(1, view.height // LINE_H)
        # Keep the selected section's heading on screen; otherwise folding something
        # near the bottom scrolls your selection out from under you.
        head = next((i for i, ln in enumerate(lines) if ln[0] == self.sel), 0)
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - visible)))
        if head < self.scroll:
            self.scroll = head
        elif head >= self.scroll + visible:
            self.scroll = head - visible + 1

        screen.set_clip(view)
        y = view.top
        for _, text, fnt, color, indent in lines[self.scroll:self.scroll + visible]:
            if text:
                draw_text(screen, text, (view.left + indent, y), fnt, color)
            y += LINE_H
        screen.set_clip(None)

        if len(lines) > visible:
            track = pygame.Rect(view.right - 3, view.top, 3, view.height)
            pygame.draw.rect(screen, (52, 50, 68), track, border_radius=2)
            h = max(12, int(track.height * visible / len(lines)))
            top = track.top + int(track.height * self.scroll / len(lines))
            pygame.draw.rect(screen, (120, 116, 146),
                             pygame.Rect(track.left, min(top, track.bottom - h), 3, h),
                             border_radius=2)

        draw_text(screen, "Up/Down section · Enter fold · PgUp/PgDn scroll · J/Esc close",
                  (T.SCREEN_W // 2, T.SCREEN_H - 26), T.font(14), T.TEXT_DIM, center=True)
