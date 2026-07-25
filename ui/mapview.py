"""The minimap and the full map.

Both draw the same derived layout (engine/cartography.py): rooms as cells, doors as
lines between them, and a marker on every room an active quest is pointing at. Rooms
you haven't been to yet are drawn faintly if they neighbour somewhere you have, so
the map fills in as you explore instead of handing you the whole valley at once.
"""
from __future__ import annotations

import pygame

from engine.cartography import layout, links, visited, waypoints
from ui import theme as T
from ui.render import draw_text, wrap_text

MINI_CELL = 13
MINI_PAD = 8
# Wide, short cells on the full map: room names need horizontal room to sit under
# their box without colliding with the neighbours'.
FULL_CW, FULL_CH = 92, 64


def short_name(name: str) -> str:
    """'The Ridge — Windward Pass' -> 'Windward Pass'. Names have to fit a cell."""
    for prefix in ("The Ridge — ", "The "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _bounds(pos):
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    return min(xs), min(ys), max(xs), max(ys)


def _cells(world, rooms):
    """(layout, links, seen, waypoint rooms) — everything both views need."""
    pos = layout(rooms)
    seen = visited(world)
    # A room next to somewhere you've been shows up as an unexplored neighbour.
    known = set(seen)
    for a, b in links(rooms):
        if a in seen:
            known.add(b)
        if b in seen:
            known.add(a)
    return pos, links(rooms), seen, known, waypoints(world, rooms)


def draw_minimap(screen, world, rooms):
    pos, conns, seen, known, marks = _cells(world, rooms)
    x0, y0, x1, y1 = _bounds(pos)
    w = (x1 - x0 + 1) * MINI_CELL + MINI_PAD * 2
    h = (y1 - y0 + 1) * MINI_CELL + MINI_PAD * 2
    box = pygame.Rect(T.SCREEN_W - w - 10, 10, w, h)

    panel = pygame.Surface(box.size, pygame.SRCALPHA)
    panel.fill((*T.BOX_BG, 170))
    screen.blit(panel, box.topleft)
    pygame.draw.rect(screen, (*T.WALL, 200), box, 1, border_radius=3)

    def cell(rid):
        cx, cy = pos[rid]
        return (box.left + MINI_PAD + (cx - x0) * MINI_CELL + MINI_CELL // 2,
                box.top + MINI_PAD + (cy - y0) * MINI_CELL + MINI_CELL // 2)

    for a, b in conns:
        if a in seen and b in seen:
            pygame.draw.line(screen, (78, 74, 96), cell(a), cell(b), 1)

    for rid in pos:
        if rid not in known:
            continue
        cx, cy = cell(rid)
        r = pygame.Rect(0, 0, MINI_CELL - 5, MINI_CELL - 5)
        r.center = (cx, cy)
        if rid == world.player.room:
            pygame.draw.rect(screen, T.HEARTH, r, border_radius=2)
        elif rid in seen:
            pygame.draw.rect(screen, (108, 104, 130), r, border_radius=2)
        else:
            pygame.draw.rect(screen, (62, 60, 78), r, 1, border_radius=2)
        if rid in marks and rid != world.player.room:
            pygame.draw.circle(screen, T.TEXT_WARN, (cx, cy), 3)


def draw_full_map(screen, world, rooms):
    o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
    o.fill((0, 0, 0, 232))
    screen.blit(o, (0, 0))
    draw_text(screen, "EMBERHOLD", (28, 18), T.font(26, bold=True), T.HEARTH)

    pos, conns, seen, known, marks = _cells(world, rooms)
    x0, y0, x1, y1 = _bounds(pos)
    grid_w = (x1 - x0 + 1) * FULL_CW
    grid_h = (y1 - y0 + 1) * FULL_CH
    ox = (T.SCREEN_W - grid_w) // 2
    oy = 64 + max(0, (T.SCREEN_H - 190 - grid_h) // 2)

    def cell(rid):
        cx, cy = pos[rid]
        return (ox + (cx - x0) * FULL_CW + FULL_CW // 2,
                oy + (cy - y0) * FULL_CH + FULL_CH // 2)

    for a, b in conns:
        if a in seen and b in seen:
            pygame.draw.line(screen, (86, 82, 106), cell(a), cell(b), 2)

    small = T.font(11)
    for rid in pos:
        if rid not in known:
            continue
        cx, cy = cell(rid)
        r = pygame.Rect(0, 0, FULL_CW - 30, FULL_CH - 30)
        r.center = (cx, cy - 5)
        here = rid == world.player.room
        if here:
            pygame.draw.rect(screen, T.HEARTH, r, border_radius=3)
        elif rid in seen:
            pygame.draw.rect(screen, (70, 68, 90), r, border_radius=3)
            pygame.draw.rect(screen, (110, 106, 132), r, 1, border_radius=3)
        else:
            pygame.draw.rect(screen, (44, 42, 58), r, 1, border_radius=3)
        if rid in marks:
            pygame.draw.circle(screen, T.TEXT_WARN, (r.right - 1, r.top + 1), 4)
        if rid in seen:
            label = short_name(rooms[rid].name)
            while label and small.size(label)[0] > FULL_CW - 6:
                label = label[:-1]
            draw_text(screen, label, (cx, r.bottom + 4), small,
                      T.TEXT if here else T.TEXT_DIM, center=True)

    # what the markers mean
    y = T.SCREEN_H - 118
    if marks:
        draw_text(screen, "Where you're wanted:", (28, y), T.font(15, bold=True), T.TEXT_WARN)
        y += 22
        for rid, titles in list(marks.items())[:3]:
            line = f"• {rooms[rid].name} — {'; '.join(titles)}"
            for ln in wrap_text(line, T.font(14), T.SCREEN_W - 70)[:1]:
                draw_text(screen, ln, (34, y), T.font(14), T.TEXT)
                y += 19
    else:
        draw_text(screen, "Nothing is asking for you right now.", (28, y),
                  T.font(15), T.TEXT_DIM)
    draw_text(screen, "M/Esc close", (T.SCREEN_W // 2, T.SCREEN_H - 26),
              T.font(14), T.TEXT_DIM, center=True)
