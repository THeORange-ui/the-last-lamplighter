"""Drawing the overworld: rooms, lamps, the Hearthlight, NPCs, the player, HUD."""
from __future__ import annotations

import pygame

from npc.roster import character_name
from ui import sprites
from ui import theme as T


def wrap_text(text, fnt, max_w):
    """Word-wrap `text` to a pixel width, returning a list of lines."""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if fnt.size(trial)[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_text(surf, text, pos, fnt, color, *, center=False, right=False):
    img = fnt.render(text, True, color)
    rect = img.get_rect()
    if center:
        rect.center = pos
    elif right:
        rect.topright = pos
    else:
        rect.topleft = pos
    surf.blit(img, rect)
    return rect


def _tile_rect(x, y):
    return pygame.Rect(x * T.TILE, y * T.TILE, T.TILE, T.TILE)


def draw_overworld(screen, world, rooms):
    room = rooms[world.player.room]
    blocked = room.blocked()

    # floor
    for y in range(T.GRID_H):
        for x in range(T.GRID_W):
            r = _tile_rect(x, y)
            checker = (x + y) % 2 == 0
            pygame.draw.rect(screen, T.FLOOR if checker else T.FLOOR_ALT, r)

    # walls
    for (x, y) in blocked:
        if room.hearthlight == (x, y):
            continue  # drawn as the lantern, not a wall
        pygame.draw.rect(screen, T.WALL, _tile_rect(x, y))

    # doors
    for d in room.doors:
        color = T.DOOR_LOCKED if d.locked else T.DOOR
        r = _tile_rect(d.x, d.y).inflate(-6, -6)
        pygame.draw.rect(screen, color, r, border_radius=4)

    # hearthlight
    if room.hearthlight:
        hx, hy = room.hearthlight
        cx, cy = hx * T.TILE + T.TILE // 2, hy * T.TILE + T.TILE // 2
        strength = max(0, min(100, world.hearthlight)) / 100
        glow_r = int(T.TILE * (1.4 + strength))
        _draw_glow(screen, (cx, cy), glow_r, T.HEARTH_GLOW, int(70 * strength) + 20)
        pygame.draw.circle(screen, T.HEARTH, (cx, cy), int(T.TILE * 0.42))

    # lamps
    for lamp_id, (lx, ly) in room.lamps.items():
        cx, cy = lx * T.TILE + T.TILE // 2, ly * T.TILE + T.TILE // 2
        lit = world.lamps.get(lamp_id, False)
        if lit:
            _draw_glow(screen, (cx, cy - 6), T.TILE, T.LAMP_GLOW, 60)
        spr = sprites.lamp_surface(lit)
        screen.blit(spr, (lx * T.TILE + (T.TILE - spr.get_width()) // 2,
                          ly * T.TILE + (T.TILE - spr.get_height())))

    # dropped items on the floor
    for g in world.ground_items_in(room.id):
        icon = sprites.item_icon(g.item)
        screen.blit(icon, (g.x * T.TILE + (T.TILE - icon.get_width()) // 2,
                           g.y * T.TILE + (T.TILE - icon.get_height()) // 2))

    # NPCs in this room
    for npc in world.npcs.values():
        if npc.room != room.id:
            continue
        _draw_actor(screen, npc.x, npc.y, npc.npc_id, character_name(npc.npc_id))

    # player
    _draw_actor(screen, world.player.x, world.player.y, "player", None)


def _draw_actor(screen, tx, ty, key, name):
    spr = sprites.actor_surface(key)
    px = tx * T.TILE + (T.TILE - spr.get_width()) // 2
    py = ty * T.TILE + (T.TILE - spr.get_height())
    r = spr.get_rect(topleft=(px, py))
    screen.blit(spr, (px, py))
    if name:
        img = T.font(14, bold=True).render(name, True, T.TEXT)
        nr = img.get_rect(midbottom=(r.centerx, r.top - 2))
        bg = nr.inflate(8, 4)
        s = pygame.Surface(bg.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 150))
        screen.blit(s, bg.topleft)
        screen.blit(img, nr)


def _draw_glow(screen, center, radius, color, alpha):
    s = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    for i in range(4, 0, -1):
        a = int(alpha * i / 4)
        pygame.draw.circle(s, (*color, a), (radius, radius), int(radius * i / 4))
    screen.blit(s, (center[0] - radius, center[1] - radius))


def draw_hud(screen, world, rooms, hint=""):
    hud = pygame.Rect(0, T.PLAY_H, T.SCREEN_W, T.HUD_H)
    pygame.draw.rect(screen, T.BOX_BG, hud)
    pygame.draw.line(screen, T.WALL, (0, T.PLAY_H), (T.SCREEN_W, T.PLAY_H), 2)

    room = rooms[world.player.room]
    draw_text(screen, room.name, (14, T.PLAY_H + 8), T.font(20, bold=True), T.TEXT)
    lit = world.lit_lamp_count()
    p = world.player
    draw_text(screen, f"HP {p.hp}/{p.max_hp}   Lamps {lit}/{len(world.lamps)}   "
                      f"Hearthlight {world.hearthlight}/100",
              (14, T.PLAY_H + 34), T.font(15), T.TEXT_DIM)

    quests = world.active_quests()
    if quests:
        q = quests[0]
        draw_text(screen, f"• {q.title}: {q.progress}/{q.objective.count}",
                  (T.SCREEN_W - 14, T.PLAY_H + 8), T.font(15), T.TEXT_WARN, right=True)

    if hint:
        draw_text(screen, hint, (T.SCREEN_W - 14, T.PLAY_H + 50),
                  T.font(15, bold=True), T.TEXT_GOOD, right=True)
    else:
        draw_text(screen, "WASD · E talk · I items · J journal · Esc menu",
                  (T.SCREEN_W - 14, T.PLAY_H + 50), T.font(13), T.TEXT_DIM, right=True)
