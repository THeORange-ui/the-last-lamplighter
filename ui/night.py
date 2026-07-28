"""The night — the one moment the world is allowed to change without the player.

Everything else in Emberhold happens because the player is standing there. That was
the missing piece: the game had no *cut*, no fade-to-black during which anything could
occur, so every attempt at a world that moves on its own had nowhere to put itself.

Resting at the waystation fire is that cut. The player chooses it, which makes it fair;
it happens in one place, which bounds it; and "things happen while you sleep" is a rule
nobody needs explained.

Phase A (here) makes the night a scene and reports the day that just ended, written from
what the world already recorded. Phase B hangs the world's turn off the same moment —
characters elsewhere act, and the morning is where the player hears about it.

One short LLM call, with an authored fallback on `LLMError`: a night that doesn't arrive
is worse than a plain one, and this is the code path a fresh clone with no settings.json
hits first.
"""
from __future__ import annotations

import threading

import pygame

from llm.client import LLMError, complete_json
from ui import theme as T
from ui.render import draw_text, wrap_text

MAX_LINES = 5           # events fed to the writer; a night is a paragraph, not a log
_SEQ_FLAG = "last_rest_seq"


def night_facts(world, upto_seq: int | None = None) -> dict:
    """What the day just gone actually held, from what the world already recorded.

    `upto_seq` excludes the rest's own events — captured before the campfire fires, so
    "you sat out a night at the fire" doesn't get reported back to you as news.
    """
    since = int(world.flags.get(_SEQ_FLAG, 0))
    events = [e for e in world.events.events
              if e.seq > since and (upto_seq is None or e.seq <= upto_seq)]
    return {
        "day": world.day,
        "events": [e.text for e in events][-MAX_LINES:],
        "party": list(world.party),
    }


def mark_rested(world, upto_seq: int | None = None) -> None:
    """Draw the line: everything up to here has now been slept on."""
    world.flags[_SEQ_FLAG] = (upto_seq if upto_seq is not None
                              else world.events._seq)


def _fallback(facts: dict) -> str:
    if facts["events"]:
        return ("The fire burns down to embers. You go over the day a while before it "
                "lets you sleep.")
    return ("The fire burns down to embers. Nothing comes up the road, and nothing goes "
            "down it. You sleep.")


def write_night(world, facts: dict) -> str:
    """Two or three sentences on the night. Never a bulleted recap — the journal
    already does that, and a night that reads like a log is not a night."""
    from npc.roster import character_name
    who = ", ".join(character_name(n) for n in facts["party"]) or "nobody"
    system = (
        "You are narrating a single night's rest at a roadside waystation, in a town "
        "held in permanent dusk. Two or three sentences, second person, plain and "
        "unhurried. Describe the night and the fire, and let what the day held sit "
        "underneath it rather than listing anything. No bullet points, no summary of "
        "tasks, no encouragement, no questions. "
        'Reply as JSON: {"line": "<two or three sentences>"}'
    )
    user = (
        f"It is the end of day {facts['day'] - 1}, at the waystation fire.\n"
        f"Who is at the fire with you: {who}\n"
        "What the day held, in the order it happened:\n"
        + ("\n".join(f"- {t}" for t in facts["events"]) or "- nothing worth the telling")
    )
    try:
        out = complete_json(system, user, temperature=0.8, max_tokens=180)
    except LLMError:
        return _fallback(facts)
    return str(out.get("line", "")).strip() or _fallback(facts)


class NightScene:
    """Writes the night on a worker thread, shows it, then hands back the morning."""

    def __init__(self, world, facts: dict):
        self.world = world
        self.facts = facts
        self.line = ""
        self.ready = False
        self.finished = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.line = write_night(self.world, self.facts)
        except Exception:
            self.line = _fallback(self.facts)
        self.ready = True

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and self.ready:
            self.finished = True

    def draw(self, screen):
        o = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        o.fill((0, 0, 0, 248))       # a real fade-to-black; the HUD shouldn't bleed through
        screen.blit(o, (0, 0))
        cx = T.SCREEN_W // 2

        draw_text(screen, "THE FIRE BURNS DOWN", (cx, 110), T.font(26, bold=True),
                  T.HEARTH, center=True)
        if not self.ready:
            draw_text(screen, "...", (cx, 200), T.font(26), T.TEXT_DIM, center=True)
            return

        y = 190
        for ln in wrap_text(self.line, T.font(17), T.SCREEN_W - 200):
            draw_text(screen, ln, (cx, y), T.font(17), T.TEXT, center=True)
            y += 26

        draw_text(screen, f"Day {self.facts['day']}", (cx, y + 30),
                  T.font(19, bold=True), T.HEARTH, center=True)
        draw_text(screen, "Press any key", (cx, T.SCREEN_H - 44), T.font(14),
                  T.TEXT_DIM, center=True)
