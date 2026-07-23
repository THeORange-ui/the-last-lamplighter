"""The player's journal overlay (toggle with J): quests + a log of what's happened."""
from __future__ import annotations

from collections import Counter

import pygame

from engine.items import display_name
from ui import theme as T
from ui.render import draw_text


def draw_journal(screen, world):
    overlay = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 220))
    screen.blit(overlay, (0, 0))

    margin = 28
    draw_text(screen, "JOURNAL", (margin, 20), T.font(26, bold=True), T.HEARTH)
    draw_text(screen, "[J / Esc] close", (T.SCREEN_W - margin, 26),
              T.font(14), T.TEXT_DIM, right=True)

    y = 64
    active = world.active_quests()
    completed = [q for q in world.quests if q.status == "complete"]

    draw_text(screen, "Quests", (margin, y), T.font(19, bold=True), T.TEXT)
    y += 28
    if not active and not completed:
        draw_text(screen, "Nothing yet. Talk to the townsfolk.", (margin + 8, y),
                  T.font(16), T.TEXT_DIM)
        y += 24
    for q in active:
        draw_text(screen, f"• {q.title}  ({q.progress}/{q.objective.count})",
                  (margin + 8, y), T.font(16, bold=True), T.TEXT_WARN)
        y += 22
        draw_text(screen, q.description, (margin + 24, y), T.font(14), T.TEXT_DIM)
        y += 26
    for q in completed:
        draw_text(screen, f"• {q.title}  — done", (margin + 8, y),
                  T.font(16), T.TEXT_GOOD)
        y += 24

    y += 12
    draw_text(screen, "Items", (margin, y), T.font(19, bold=True), T.TEXT)
    y += 28
    counts = Counter(world.player.inventory)
    if not counts:
        draw_text(screen, "Your pack is empty.", (margin + 8, y), T.font(16), T.TEXT_DIM)
        y += 24
    else:
        for item_id, n in counts.items():
            label = display_name(item_id) + (f"  x{n}" if n > 1 else "")
            draw_text(screen, f"• {label}", (margin + 8, y), T.font(16), T.TEXT)
            y += 22

    y += 12
    draw_text(screen, "Log", (margin, y), T.font(19, bold=True), T.TEXT)
    y += 28
    events = world.events.all_newest_first()
    if not events:
        draw_text(screen, "—", (margin + 8, y), T.font(16), T.TEXT_DIM)
    max_y = T.SCREEN_H - 24
    for ev in events:
        if y > max_y:
            break
        draw_text(screen, f"· {ev.text}", (margin + 8, y), T.font(15), T.TEXT)
        y += 22
