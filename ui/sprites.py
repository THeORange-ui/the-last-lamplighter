"""Procedural pixel-art sprites.

Sprites are drawn on a small low-resolution surface (hard-edged shapes, no
anti-aliasing) and then nearest-neighbour scaled up, which yields crisp,
chunky pixels in the Undertale vein. Built lazily and cached (needs the display
initialised first). Per-character tinting comes from a small palette table so we
don't hand-author four separate figures.
"""
from __future__ import annotations

import pygame

from ui import theme as T

# base resolution of an actor sprite, in "pixels"
AW, AH = 16, 20
SCALE = 2
OUTLINE = (18, 16, 26)
SKIN = (233, 201, 170)

# hair color per character (fallback derived from cloth otherwise)
_HAIR = {
    "player": (120, 92, 64),
    "wren": (170, 130, 86),
    "bram": (150, 120, 110),
    "sella": (60, 52, 70),
    "perrin": (176, 176, 184),
}

_cache: dict[str, pygame.Surface] = {}


def _darken(c, f=0.62):
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


def _cloth_for(key: str):
    return T.PLAYER if key == "player" else T.npc_color(key)


def _build_actor(key: str) -> pygame.Surface:
    cloth = _cloth_for(key)
    shade = _darken(cloth)
    hair = _HAIR.get(key, _darken(cloth, 0.5))
    s = pygame.Surface((AW, AH), pygame.SRCALPHA)

    # legs / boots
    pygame.draw.rect(s, OUTLINE, (5, 16, 2, 4))
    pygame.draw.rect(s, OUTLINE, (9, 16, 2, 4))
    pygame.draw.rect(s, _darken(shade, 0.8), (5, 18, 2, 2))
    pygame.draw.rect(s, _darken(shade, 0.8), (9, 18, 2, 2))

    # body (cloak) with outline + a shaded side for a little form
    pygame.draw.rect(s, OUTLINE, (2, 9, 12, 8), border_radius=2)
    pygame.draw.rect(s, cloth, (3, 10, 10, 6))
    pygame.draw.rect(s, shade, (9, 10, 4, 6))

    # head: outline ring, hair, then face
    pygame.draw.ellipse(s, OUTLINE, (2, 0, 12, 11))
    pygame.draw.ellipse(s, hair, (3, 1, 10, 9))
    pygame.draw.ellipse(s, SKIN, (4, 4, 8, 6))

    # eyes
    s.set_at((6, 6), OUTLINE)
    s.set_at((9, 6), OUTLINE)

    big = pygame.transform.scale(s, (AW * SCALE, AH * SCALE))
    return big


def actor_surface(key: str) -> pygame.Surface:
    if key not in _cache:
        _cache[key] = _build_actor(key)
    return _cache[key]


_ITEM_TINT = {
    "coin": (240, 205, 90),
    "bread": (206, 160, 96),
    "tavern_stew": (198, 138, 84),
    "tonic": (150, 210, 180),
    "ridge_map": (222, 208, 168),
    "old_key": (176, 176, 190),
    "oil_flask": (150, 196, 255),
    "scrap": (150, 146, 150),
}


def item_icon(item_id: str) -> pygame.Surface:
    """A small ground/inventory icon for an item (cached)."""
    key = f"__item_{item_id}"
    if key in _cache:
        return _cache[key]
    tint = _ITEM_TINT.get(item_id, (200, 200, 210))
    s = pygame.Surface((10, 10), pygame.SRCALPHA)
    pygame.draw.rect(s, OUTLINE, (1, 1, 8, 8), border_radius=2)
    pygame.draw.rect(s, tint, (2, 2, 6, 6), border_radius=2)
    pygame.draw.rect(s, (255, 255, 255), (3, 3, 2, 2))   # highlight
    big = pygame.transform.scale(s, (20, 20))
    _cache[key] = big
    return big


def lamp_surface(lit: bool) -> pygame.Surface:
    key = f"__lamp_{lit}"
    if key in _cache:
        return _cache[key]
    w, h = 12, 18
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    # post
    pygame.draw.rect(s, (48, 44, 58), (5, 6, 2, 11))
    pygame.draw.rect(s, (60, 56, 72), (3, 16, 6, 2))
    # lantern housing
    pygame.draw.rect(s, OUTLINE, (2, 0, 8, 8), border_radius=2)
    glass = T.LAMP_ON if lit else T.LAMP_OFF
    pygame.draw.rect(s, glass, (3, 1, 6, 6))
    if lit:
        pygame.draw.rect(s, (255, 244, 210), (5, 2, 2, 3))
    big = pygame.transform.scale(s, (w * 2, h * 2))
    _cache[key] = big
    return big
