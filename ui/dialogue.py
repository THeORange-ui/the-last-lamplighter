"""The dialogue overlay: free-text input, a threaded NPC turn, typewriter reveal.

The LLM turn runs on a worker thread so the window stays responsive and shows a
"…" indicator instead of freezing. Only this box mutates world state during a
turn; the render loop only reads it, so the GIL keeps things safe enough for a
single-player prototype.
"""
from __future__ import annotations

import threading

import pygame

from engine.items import display_name
from engine.state import affinity_label
from engine.trade import buy_from_npc, give_to_npc, sell_to_npc
from npc.agent import APPROACH, npc_respond
from npc.roster import character_name
from ui import theme as T
from ui.inventory import TradePanel
from ui.render import draw_text, wrap_text

REVEAL_CPS = 55          # characters per second for the typewriter
CLOSE_DELAY = 0.6        # seconds to linger after an end_dialogue line

# Ctrl / Cmd toggles the trade view (I would collide with typing a message).
TRADE_KEYS = (pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LMETA, pygame.K_RMETA)


class _Turn:
    """Holds the result of a background NPC turn."""

    def __init__(self):
        self.done = False
        self.value: dict | None = None

    def run(self, fn):
        self.value = fn()
        self.done = True


class DialogueBox:
    def __init__(self, world, rooms, known, npc_id, memory):
        self.world = world
        self.rooms = rooms
        self.known = known
        self.npc_id = npc_id
        self.memory = memory
        self.name = character_name(npc_id)

        self.input_text = ""
        self.npc_line = ""
        self.reveal = 0.0
        self.mode = "thinking"          # thinking | reveal | await | closing
        self.banner: list[str] = []
        self._turn: _Turn | None = None
        self.finished = False           # set True when the box should close
        self._close_timer = 0.0
        self._end_after_reveal = False
        self._caret = 0.0
        self.trade: TradePanel | None = None
        self.combat_request: str | None = None   # npc_id if the NPC turned hostile

        self._start_turn(APPROACH)

    # --- turn plumbing ----------------------------------------------------
    def _start_turn(self, player_input):
        self.mode = "thinking"
        turn = _Turn()
        self._turn = turn
        t = threading.Thread(
            target=turn.run,
            args=(lambda: npc_respond(self.world, self.rooms, self.known,
                                      self.npc_id, player_input, self.memory),),
            daemon=True,
        )
        t.start()

    def _consume_result(self, out: dict):
        self.npc_line = out.get("dialogue", "…")
        self.reveal = 0.0
        self.mode = "reveal"
        self.banner = []
        for eff in (out.get("result").effects if out.get("result") else []):
            self.banner.append(eff)
        for q in out.get("completed_quests", []):
            self.banner.append(f"• Quest complete: “{q.title}”")
        if out.get("error"):
            self.banner.append("(the words don't come — connection trouble)")
        if out.get("result") and out["result"].end_dialogue:
            self._end_after_reveal = True
        if out.get("result") and out["result"].starts_combat:
            self.combat_request = self.npc_id
            self._end_after_reveal = True

    # --- update / events --------------------------------------------------
    def update(self, dt):
        self._caret = (self._caret + dt) % 1.0
        if self.mode == "thinking" and self._turn and self._turn.done:
            self._consume_result(self._turn.value)
            self._turn = None
        elif self.mode == "reveal":
            self.reveal += dt * REVEAL_CPS
            if self.reveal >= len(self.npc_line):
                self.reveal = len(self.npc_line)
                self.mode = "closing" if self._end_after_reveal else "await"
                if self.mode == "closing":
                    self._close_timer = CLOSE_DELAY
        elif self.mode == "closing":
            self._close_timer -= dt
            if self._close_timer <= 0:
                self.finished = True

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if self.trade is not None:
            cmd = self.trade.handle_event(event)
            if cmd:
                self._handle_trade(cmd)
            return
        if event.key == pygame.K_ESCAPE:
            self.finished = True
            return
        # Open the trade view with Ctrl/Cmd (so every letter key stays free for typing).
        if event.key in TRADE_KEYS and self.mode in ("reveal", "await"):
            self.trade = TradePanel(self.world, self.npc_id, self.name)
            return
        if self.mode == "reveal":
            # fast-forward the typewriter
            self.reveal = len(self.npc_line)
            return
        if self.mode != "await":
            return
        if event.key == pygame.K_RETURN:
            text = self.input_text.strip()
            if text:
                self.input_text = ""
                self._start_turn(text)
        elif event.key == pygame.K_BACKSPACE:
            self.input_text = self.input_text[:-1]
        elif event.unicode and event.unicode.isprintable() and len(self.input_text) < 160:
            self.input_text += event.unicode

    def _handle_trade(self, cmd):
        action = cmd.get("cmd")
        npc = self.world.npcs[self.npc_id]
        item = cmd.get("item")
        name = display_name(item) if item else ""
        if action == "close":
            self.trade = None
        elif action == "gift":
            if give_to_npc(self.world, npc, item):
                self.world.adjust_affinity(self.npc_id, 2)
                self.trade = None
                self._start_turn(f"Here — I'd like you to have my {name}. Please, take it.")
        elif action == "ask":
            self.trade = None
            self._start_turn(f"Could I have your {name}?")
        elif action == "buy":
            ok, why = buy_from_npc(self.world, npc, item)
            self.trade.message = (f"Bought the {name} ({why})." if ok
                                  else f"Can't buy the {name}: {why}.")
        elif action == "sell":
            ok, why = sell_to_npc(self.world, npc, item)
            self.trade.message = (f"Sold the {name} ({why})." if ok
                                  else f"Can't sell the {name}: {why}.")

    # --- draw -------------------------------------------------------------
    def draw(self, screen):
        box = pygame.Rect(12, T.PLAY_H - 210, T.SCREEN_W - 24, 198)
        panel = pygame.Surface(box.size, pygame.SRCALPHA)
        panel.fill((*T.BOX_BG, 235))
        screen.blit(panel, box.topleft)
        pygame.draw.rect(screen, T.BOX_BORDER, box, 2, border_radius=6)

        npc = self.world.npcs[self.npc_id]
        draw_text(screen, self.name, (box.left + 16, box.top + 10),
                  T.font(20, bold=True), T.npc_color(self.npc_id))
        draw_text(screen, affinity_label(npc.affinity),
                  (box.right - 16, box.top + 12), T.font(14), T.TEXT_DIM, right=True)
        pygame.draw.line(screen, T.WALL, (box.left + 14, box.top + 38),
                         (box.right - 14, box.top + 38), 1)

        body_x, body_y = box.left + 16, box.top + 48
        max_w = box.width - 32

        if self.mode == "thinking":
            dots = "." * (1 + int(self._caret * 3))
            draw_text(screen, dots, (body_x, body_y), T.font(22), T.TEXT_DIM)
        else:
            shown = self.npc_line[: int(self.reveal)]
            y = body_y
            for ln in wrap_text(shown, T.font(19), max_w):
                draw_text(screen, ln, (body_x, y), T.font(19), T.TEXT)
                y += 26

        # effect banner
        if self.banner:
            by = box.bottom - 66
            for line in self.banner[:2]:
                draw_text(screen, line, (body_x, by), T.font(14, bold=True), T.EFFECT)
                by += 18

        # input row
        iy = box.bottom - 34
        pygame.draw.line(screen, T.WALL, (box.left + 14, iy - 6),
                         (box.right - 14, iy - 6), 1)
        if self.mode == "await":
            caret = "|" if self._caret < 0.5 else " "
            draw_text(screen, "> " + self.input_text + caret,
                      (body_x, iy), T.font(18, mono=True), T.TEXT)
            draw_text(screen, "[Ctrl] trade", (box.right - 16, iy + 2),
                      T.font(13), T.TEXT_DIM, right=True)
        elif self.mode == "reveal":
            draw_text(screen, "[Enter] skip · [Ctrl] trade", (body_x, iy),
                      T.font(14), T.TEXT_DIM)
        else:
            draw_text(screen, "…", (body_x, iy), T.font(14), T.TEXT_DIM)

        if self.trade is not None:
            self.trade.draw(screen)
