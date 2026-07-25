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
    pal = T.BIOMES.get(getattr(room, "biome", "town"), T.BIOMES["town"])

    # floor
    for y in range(T.GRID_H):
        for x in range(T.GRID_W):
            r = _tile_rect(x, y)
            checker = (x + y) % 2 == 0
            pygame.draw.rect(screen, pal["floor"] if checker else pal["floor_alt"], r)
            if room.biome == "snow":
                # a couple of deterministic snow flecks per tile
                h = (x * 73 + y * 149) % 100
                if h < 22:
                    fx = r.x + 6 + (h * 7) % (T.TILE - 12)
                    fy = r.y + 6 + (h * 13) % (T.TILE - 12)
                    pygame.draw.rect(screen, (206, 216, 236), (fx, fy, 2, 2))

    # walls
    for (x, y) in blocked:
        if room.hearthlight == (x, y):
            continue  # drawn as the lantern, not a wall
        pygame.draw.rect(screen, pal["wall"], _tile_rect(x, y))

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

    # interactables: lamps, then everything else (see engine/interact.py)
    for inter in room.interactables:
        if inter.hidden:
            continue
        fx, fy = inter.pos
        cx, cy = fx * T.TILE + T.TILE // 2, fy * T.TILE + T.TILE // 2
        kind = inter.kind
        if kind == "lamp":
            lit = world.lamps.get(inter.id, False)
            if lit:
                _draw_glow(screen, (cx, cy - 6), T.TILE, T.LAMP_GLOW, 60)
            spr = sprites.lamp_surface(lit)
            screen.blit(spr, (fx * T.TILE + (T.TILE - spr.get_width()) // 2,
                              fy * T.TILE + (T.TILE - spr.get_height())))
        elif kind == "campfire":
            _draw_glow(screen, (cx, cy), int(T.TILE * 1.1), T.LAMP_GLOW, 80)
            pygame.draw.circle(screen, (86, 66, 54), (cx, cy + 7), int(T.TILE * 0.30))
            pygame.draw.circle(screen, T.HEARTH, (cx, cy), int(T.TILE * 0.22))
            pygame.draw.circle(screen, (255, 232, 156), (cx, cy - 3), int(T.TILE * 0.10))
        elif kind == "chest":
            r = _tile_rect(fx, fy).inflate(-8, -12)
            pygame.draw.rect(screen, (120, 86, 50), r, border_radius=3)
            pygame.draw.rect(screen, (150, 110, 66), (r.x, r.y, r.w, r.height // 2),
                             border_radius=3)
            pygame.draw.rect(screen, (60, 44, 26), r, 2, border_radius=3)
            pygame.draw.rect(screen, (230, 200, 120), (r.centerx - 2, r.centery - 1, 4, 5))

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
    draw_text(screen, f"Day {getattr(world, 'day', 1)}   HP {p.hp}/{p.max_hp}   "
                      f"Lamps {lit}/{len(world.lamps)}   "
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
        draw_text(screen, "WASD · E talk · I items · P party · J journal · Esc menu",
                  (T.SCREEN_W - 14, T.PLAY_H + 50), T.font(13), T.TEXT_DIM, right=True)
