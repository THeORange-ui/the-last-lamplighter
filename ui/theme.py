"""Rendering constants: a dusk palette + fonts. Placeholder art, Undertale-plain."""
from __future__ import annotations

import pygame

TILE = 40                    # on-screen size of one logical tile
from engine.world import GRID_H, GRID_W  # noqa: E402

PLAY_W = GRID_W * TILE
PLAY_H = GRID_H * TILE
HUD_H = 76
SCREEN_W = PLAY_W
SCREEN_H = PLAY_H + HUD_H

FPS = 60

# --- dusk palette ---
BG = (14, 13, 22)
FLOOR = (30, 28, 44)
FLOOR_ALT = (34, 32, 50)
WALL = (54, 50, 74)
GRID_LINE = (24, 22, 36)

# per-biome palettes (floor, floor_alt, wall) — snow is cold blue, camp is warm firelight
BIOMES = {
    "town": {"floor": (30, 28, 44), "floor_alt": (34, 32, 50), "wall": (54, 50, 74)},
    "snow": {"floor": (40, 50, 74), "floor_alt": (46, 58, 84), "wall": (92, 112, 150)},
    "camp": {"floor": (44, 34, 34), "floor_alt": (50, 38, 36), "wall": (78, 60, 52)},
    # the chapel and its vault: dry pale stone, and something colder underneath
    "stone": {"floor": (46, 45, 52), "floor_alt": (52, 51, 58), "wall": (92, 90, 100)},
    "under": {"floor": (26, 27, 34), "floor_alt": (30, 31, 38), "wall": (60, 58, 70)},
}
DOOR = (96, 84, 132)
DOOR_LOCKED = (70, 40, 40)

PLAYER = (243, 233, 190)
PLAYER_EDGE = (120, 110, 70)

LAMP_OFF = (58, 58, 70)
LAMP_ON = (255, 201, 92)
LAMP_GLOW = (255, 201, 92)
HEARTH = (255, 158, 66)
HEARTH_GLOW = (255, 120, 40)

TEXT = (236, 236, 242)
TEXT_DIM = (150, 150, 168)
TEXT_WARN = (240, 180, 90)
TEXT_GOOD = (150, 230, 150)
TEXT_BAD = (216, 126, 126)

BOX_BG = (9, 9, 16)
BOX_BORDER = (206, 206, 220)
EFFECT = (255, 214, 120)

# Per-NPC placeholder colors (fallback generated from id hash).
NPC_COLORS = {
    "wren": (140, 196, 255),
    "bram": (220, 150, 116),
    "sella": (196, 160, 240),
    "perrin": (150, 168, 160),
    "hessa": (226, 214, 178),
    "moss": (168, 226, 160),
    "tilda": (206, 176, 128),
    "corvin": (188, 156, 148),
}


def npc_color(npc_id: str) -> tuple[int, int, int]:
    if npc_id in NPC_COLORS:
        return NPC_COLORS[npc_id]
    h = sum(ord(c) for c in npc_id)
    return (120 + h % 120, 100 + (h * 7) % 120, 140 + (h * 13) % 100)


_FONTS: dict[tuple[str, int, bool], pygame.font.Font] = {}


def font(size: int = 20, *, mono: bool = False, bold: bool = False) -> pygame.font.Font:
    key = ("mono" if mono else "sans", size, bold)
    if key not in _FONTS:
        name = "menlo,dejavusansmono,consolas,monospace" if mono else \
               "avenirnext,helveticaneue,arial,sans"
        _FONTS[key] = pygame.font.SysFont(name, size, bold=bold)
    return _FONTS[key]
