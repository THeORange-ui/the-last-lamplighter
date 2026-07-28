"""What became of everyone, once the Gloam is answered.

The arcs need somewhere to land. Each main character's ending is written from how
far they actually got — which agenda beats they closed — and from what they
remember of the player, so it reflects the playthrough rather than a fixed script.
One short LLM call each, once, at the end of a whole game; if the endpoint is down,
the authored fallback for their stage is used instead, because an ending that
doesn't arrive is worse than a plain one.

Afterwards the player is returned to a lit Emberhold and can keep playing.
"""
from __future__ import annotations

import threading

import pygame

from llm.client import LLMError, complete_json
from npc.agenda import open_goal
from npc.memory import NPCMemory
from npc.roster import character_name, load_character
from ui import theme as T
from ui.render import draw_text, wrap_text

CAST = ["wren", "bram", "perrin", "sella"]


def _stage(world, npc_id) -> tuple[int, int]:
    """(beats closed, beats authored) — how far this arc actually travelled."""
    npc = world.npcs.get(npc_id)
    if npc is None:
        return (0, 0)
    done = sum(1 for a in npc.agenda if a.get("status") == "done")
    try:
        total = len(load_character(npc_id).get("agenda") or [])
    except KeyError:
        total = 0
    return (done, max(total, done))


def _fallback(world, npc_id) -> str:
    done, total = _stage(world, npc_id)
    name = character_name(npc_id)
    if total and done >= total:
        return f"{name} finished what they set out to do, and said so plainly."
    if done == 0:
        return (f"{name} never got far with you. The dusk lifted anyway, and they went "
                f"on much as before.")
    return (f"{name} got part of the way through what they wanted, and the rest of it "
            f"is still theirs to do.")


def _write_one(world, npc_id, outcome: str) -> str:
    done, total = _stage(world, npc_id)
    npc = world.npcs.get(npc_id)
    goal = open_goal(npc) if npc else None
    try:
        char = load_character(npc_id)
    except KeyError:
        return _fallback(world, npc_id)
    mem = NPCMemory(npc_id)
    closed = [a["want"] for a in (npc.agenda if npc else []) if a.get("status") == "done"]
    system = (
        f"You are writing the closing lines about {char['name']} — {char.get('role','')} "
        "— for the end of a game. Two sentences, past tense, plain and unsentimental. "
        "Say what became of them, in a way that follows from what actually happened to "
        "them in this playthrough. No speeches, no moral. Do not address the player as "
        '"you". Reply as JSON: {"line": "<two sentences>"}'
    )
    user = (
        f"How it ended on the ridge: the player {outcome} the Gloam.\n"
        f"What {char['name']} had set out to do, and finished: "
        + ("; ".join(closed) if closed else "(nothing — their arc never really started)")
        + f"\nWhat they were still trying to do at the end: "
        + (goal["want"] if goal else "(nothing left they had named)")
        + f"\nWhat they remember of the player:\n{mem.as_prompt(6)}"
    )
    try:
        out = complete_json(system, user, temperature=0.7, max_tokens=160,
                            log_group=f"epilogue:{npc_id}")
    except LLMError:
        return _fallback(world, npc_id)
    return str(out.get("line", "")).strip() or _fallback(world, npc_id)


class Epilogue:
    """Writes the endings on a worker thread, then shows them a page at a time."""

    def __init__(self, world, outcome: str):
        self.world = world
        self.outcome = outcome
        self.lines: list[tuple[str, str]] = []
        self.ready = False
        self.finished = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        for npc_id in CAST:
            if npc_id in self.world.npcs:
                try:
                    self.lines.append((npc_id, _write_one(self.world, npc_id, self.outcome)))
                except Exception:
                    self.lines.append((npc_id, _fallback(self.world, npc_id)))
        self.ready = True

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and self.ready:
            self.finished = True

    def draw(self, screen):
        o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        o.fill((0, 0, 0, 245))
        screen.blit(o, (0, 0))
        cx = T.SCREEN_W // 2

        headline = ("The dark did not have to be driven off. It only had to be answered."
                    if self.outcome == "spared" else
                    "The dark on the ridge is gone, and the valley is lit.")
        draw_text(screen, "THE DUSK LIFTS", (cx, 54), T.font(30, bold=True),
                  T.HEARTH, center=True)
        for i, ln in enumerate(wrap_text(headline, T.font(17), T.SCREEN_W - 160)):
            draw_text(screen, ln, (cx, 98 + i * 24), T.font(17), T.TEXT_DIM, center=True)

        if not self.ready:
            draw_text(screen, "…", (cx, 300), T.font(26), T.TEXT_DIM, center=True)
            return

        y = 160
        for npc_id, line in self.lines:
            draw_text(screen, character_name(npc_id), (70, y), T.font(19, bold=True),
                      T.npc_color(npc_id))
            y += 26
            for ln in wrap_text(line, T.font(16), T.SCREEN_W - 160)[:3]:
                draw_text(screen, ln, (86, y), T.font(16), T.TEXT)
                y += 22
            y += 12

        draw_text(screen, "Press any key — Emberhold is still there, and still yours to walk.",
                  (cx, T.SCREEN_H - 34), T.font(14), T.TEXT_DIM, center=True)
