"""The minimap and the full map.

Both draw the same derived layout (engine/cartography.py): rooms as cells, doors as
lines between them, and a marker on every room an active quest is pointing at. Rooms
you haven't been to yet are drawn faintly if they neighbour somewhere you have, so
the map fills in as you explore instead of handing you the whole valley at once.
"""
from __future__ import annotations

import math

import pygame

from engine.cartography import layout, links, visited, waypoints
from npc.roster import character_name
from ui import theme as T
from ui.render import draw_text

MINI_CELL = 13
MINI_PAD = 8
# Wide, short cells on the full map: room names need horizontal room to sit under
# their box, and a second line under that for who is standing in it.
FULL_CW, FULL_CH = 92, 76
MAX_LEGEND = 6           # waypoints spelled out below the map before it says "+n more"

# Which way an off-map waypoint lies, by the sign of the step from the player's cell.
COMPASS = {
    (0, -1): "north", (0, 1): "south", (1, 0): "east", (-1, 0): "west",
    (1, -1): "north-east", (-1, -1): "north-west",
    (1, 1): "south-east", (-1, 1): "south-west",
}


def short_name(name: str) -> str:
    """'The Ridge — Windward Pass' -> 'Windward Pass'. Names have to fit a cell."""
    for prefix in ("The Ridge — ", "The "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def _shade(color, factor: float):
    return tuple(min(255, int(c * factor)) for c in color)


def _palette(room):
    """Fill and edge for a room, taken from its biome — so the town reads purple, the
    ridge reads cold blue, and a biome added later colours itself in for free."""
    wall = T.BIOMES.get(getattr(room, "biome", "town"), T.BIOMES["town"])["wall"]
    return _shade(wall, 1.30), _shade(wall, 1.95)


def _fit(text: str, fnt, width: int) -> str:
    """Trim to a pixel width, since a room name will not always fit its cell."""
    if fnt.size(text)[0] <= width:
        return text
    while text and fnt.size(text + "…")[0] > width:
        text = text[:-1]
    return text + "…" if text else ""


def _occupants(world, room_id: str) -> list[str]:
    return [character_name(nid) for nid, n in world.npcs.items() if n.room == room_id]


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
        fill, _ = _palette(rooms[rid])
        if rid == world.player.room:
            pygame.draw.rect(screen, T.HEARTH, r, border_radius=2)
        elif rid in seen:
            pygame.draw.rect(screen, _shade(fill, 1.25), r, border_radius=2)
        else:
            pygame.draw.rect(screen, _shade(fill, 0.7), r, 1, border_radius=2)
        if rid in marks and rid != world.player.room:
            pygame.draw.circle(screen, T.TEXT_WARN, (cx, cy), 3)


def _badge(screen, center, number: int) -> None:
    """A numbered waypoint pip. The number is what ties a marker on the map to the line
    below it — without one, three markers and three quests are a guessing game."""
    pygame.draw.circle(screen, T.TEXT_WARN, center, 9)
    pygame.draw.circle(screen, (40, 28, 10), center, 9, 1)
    draw_text(screen, str(number), center, T.font(12, bold=True), (30, 20, 8), center=True)


def _pointer(screen, cell_rect, step, number: int, nth: int = 0) -> None:
    """A waypoint in a room you have never found still deserves to be on the map —
    hiding the marker altogether is how the player ends up with a quest and no idea
    which way to walk. So it sits just outside your own cell, pointing that way.

    It is drawn over the top of everything else on purpose: it is a heads-up marker,
    not part of the map, and on a grid this dense there is nowhere else to put it.
    """
    ang = math.atan2(step[1], step[0])
    dx, dy = math.cos(ang), math.sin(ang)
    # Push out to the edge of the player's cell first, or the arrow lands inside it.
    edge = min((cell_rect.width / 2 + 4) / abs(dx) if dx else 1e9,
               (cell_rect.height / 2 + 4) / abs(dy) if dy else 1e9)
    edge += nth * 26            # several this way: queue them up rather than stack them
    cx, cy = cell_rect.center

    def at(dist):
        return (cx + dist * dx, cy + dist * dy)

    wing = [(cx + (edge + 3) * dx + 9 * math.cos(ang + a),
             cy + (edge + 3) * dy + 9 * math.sin(ang + a)) for a in (2.2, -2.2)]
    pygame.draw.polygon(screen, (30, 20, 8), [at(edge + 17), *wing])
    pygame.draw.polygon(screen, T.TEXT_WARN, [at(edge + 15), *wing])
    _badge(screen, tuple(int(v) for v in at(edge + 28)), number)


def draw_full_map(screen, world, rooms):
    o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
    o.fill((0, 0, 0, 246))
    screen.blit(o, (0, 0))
    draw_text(screen, "EMBERHOLD", (28, 16), T.font(26, bold=True), T.HEARTH)
    draw_text(screen, f"Day {world.day}", (T.SCREEN_W - 28, 24),
              T.font(15), T.TEXT_DIM, right=True)

    pos, conns, seen, known, marks = _cells(world, rooms)
    # One number per marker, in the order they are listed underneath.
    numbers = {rid: i + 1 for i, rid in enumerate(marks)}
    x0, y0, x1, y1 = _bounds(pos)
    grid_w = (x1 - x0 + 1) * FULL_CW
    grid_h = (y1 - y0 + 1) * FULL_CH
    legend_top = T.SCREEN_H - 46 - 20 * min(len(marks) or 1, MAX_LEGEND)
    ox = (T.SCREEN_W - grid_w) // 2
    oy = 56 + max(0, (legend_top - 70 - grid_h) // 2)

    def cell(rid):
        cx, cy = pos[rid]
        return (ox + (cx - x0) * FULL_CW + FULL_CW // 2,
                oy + (cy - y0) * FULL_CH + FULL_CH // 2)

    for a, b in conns:
        if a in seen and b in seen:
            pygame.draw.line(screen, (86, 82, 106), cell(a), cell(b), 2)

    small, tiny = T.font(11), T.font(10)
    player_rect = pygame.Rect(*cell(world.player.room), 1, 1)
    for rid in pos:
        if rid not in known:
            continue
        cx, cy = cell(rid)
        r = pygame.Rect(0, 0, FULL_CW - 30, FULL_CH - 42)
        r.center = (cx, cy - 10)
        fill, edge = _palette(rooms[rid])
        here = rid == world.player.room
        if here:
            player_rect = r
            pygame.draw.rect(screen, T.HEARTH, r, border_radius=3)
            pygame.draw.rect(screen, T.TEXT, r, 2, border_radius=3)
        elif rid in seen:
            pygame.draw.rect(screen, fill, r, border_radius=3)
            pygame.draw.rect(screen, edge, r, 1, border_radius=3)
        else:
            # Somewhere you can see the way to but have not walked into yet.
            pygame.draw.rect(screen, _shade(fill, 0.45), r, 1, border_radius=3)
            draw_text(screen, "?", r.center, small, _shade(edge, 0.7), center=True)
        if rid in numbers:
            _badge(screen, (r.right, r.top), numbers[rid])
        if rid in seen:
            draw_text(screen, _fit(short_name(rooms[rid].name), small, FULL_CW - 8),
                      (cx, r.bottom + 4), small, T.TEXT if here else T.TEXT_DIM,
                      center=True)
            who = _occupants(world, rid)
            if who:
                draw_text(screen, _fit(", ".join(who), tiny, FULL_CW - 8),
                          (cx, r.bottom + 20), tiny, (128, 124, 150), center=True)

    # Waypoints in rooms you have not found: draw them beside you, pointing that way.
    px, py = pos[world.player.room]
    for i, rid in enumerate(r for r in marks if r not in known):
        tx, ty = pos[rid]
        step = ((tx > px) - (tx < px), (ty > py) - (ty < py))
        if step == (0, 0):
            continue
        _pointer(screen, player_rect, step, numbers[rid], i)

    # --- what the markers mean -------------------------------------------
    y = legend_top
    if marks:
        draw_text(screen, "Where you're wanted", (28, y - 24),
                  T.font(15, bold=True), T.TEXT_WARN)
        for rid, titles in list(marks.items())[:MAX_LEGEND]:
            if rid in known:
                where = rooms[rid].name
            else:
                tx, ty = pos[rid]
                step = ((tx > px) - (tx < px), (ty > py) - (ty < py))
                where = f"somewhere {COMPASS.get(step, 'out there')}"
            line = f"{numbers[rid]}. {where} — {'; '.join(titles)}"
            draw_text(screen, _fit(line, T.font(14), T.SCREEN_W - 62), (34, y),
                      T.font(14), T.TEXT)
            y += 20
        if len(marks) > MAX_LEGEND:
            draw_text(screen, f"...and {len(marks) - MAX_LEGEND} more in your journal.",
                      (34, y), T.font(13), T.TEXT_DIM)
    else:
        draw_text(screen, "Nothing is asking for you right now.", (28, y),
                  T.font(15), T.TEXT_DIM)
    draw_text(screen, "M/Esc close", (T.SCREEN_W // 2, T.SCREEN_H - 26),
              T.font(14), T.TEXT_DIM, center=True)
