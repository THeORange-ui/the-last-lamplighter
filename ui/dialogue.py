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
from llm import log as llm_log
from npc.agent import APPROACH, npc_respond
from npc.interject import interject, join_conversation
from npc.memory import NPCMemory
from npc.roster import character_name, load_character
from ui import theme as T
from ui.convhub import YOU, ConvHub
from ui.inventory import TradePanel
from ui.shop import ShopPanel
from ui.render import draw_text, wrap_text

REVEAL_CPS = 55          # characters per second for the typewriter
CLOSE_LOCKOUT = 0.4      # ignore keys this long after a parting line, so a held key
                         # can't dismiss it before it has been read
_BS_DELAY = 0.35         # hold time before backspace starts auto-repeating
_BS_INTERVAL = 0.045     # delete one more character every this many seconds while held
_INPUT_LINES = 3         # how many wrapped lines of the input to show (tail)
MAX_ASIDES = 2           # most times a bystander may cut into one conversation
JOIN_ASIDES = 2          # extra cut-ins earned by a companion you deliberately pulled in
PLAYER_LABEL = "The outsider"   # how the player is named *to another character*

BOX_H = 280              # replies routinely ran past the old 198 and buried the input
LINE_H = 25              # one body line; uniform, which is what makes scrolling simple
INPUT_LINE_H = 22

# Ctrl / Cmd opens the conversation hub (a letter key would collide with typing).
HUB_KEYS = (pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LMETA, pygame.K_RMETA)
# The body scrolls, because a long reply plus a cut-in does not fit any box we could
# reasonably draw over the world. Arrows are free here — typing never produces them.
SCROLL_UP = (pygame.K_UP, pygame.K_PAGEUP)
SCROLL_DOWN = (pygame.K_DOWN, pygame.K_PAGEDOWN)


class _Turn:
    """Holds the result of a background NPC turn."""

    def __init__(self):
        self.done = False
        self.value: dict | None = None

    def run(self, fn):
        # `done` is set whatever happens: if the worker dies the box would otherwise
        # sit on "thinking" forever, which is worse than showing an error.
        try:
            self.value = fn()
        except Exception as e:                                  # noqa: BLE001
            self.value = {"dialogue": "…", "result": None, "completed_quests": [],
                          "error": f"{type(e).__name__}: {e}"}
        finally:
            self.done = True


class DialogueBox:
    def __init__(self, world, rooms, known, npc_id, memory):
        self.world = world
        self.rooms = rooms
        self.known = known
        self.npc_id = npc_id
        self.memory = memory
        self.name = character_name(npc_id)
        # A new box is a new conversation: --log-llm starts a fresh transcript file, so
        # the turns of one exchange read top to bottom instead of piling into one file
        # per character for the whole session.
        llm_log.begin_conversation(npc_id)
        try:
            self._is_vendor = load_character(npc_id).get("kind") == "vendor"
        except KeyError:
            self._is_vendor = False

        self.input_text = ""
        self.npc_line = ""
        self.reveal = 0.0
        self.mode = "thinking"          # thinking | reveal | await | closing
        self.banner: list[str] = []
        self._turn: _Turn | None = None
        self.finished = False           # set True when the box should close
        self._close_timer = 0.0
        # Lines scrolled up from the bottom. Zero means pinned to the newest text, so
        # the typewriter follows itself for free; scrolling up locks the view there.
        self.scroll = 0
        self._max_scroll = 0            # recomputed each draw, once the box is measured
        self._end_after_reveal = False
        self._caret = 0.0
        self._bs_timer = _BS_DELAY               # backspace hold-to-repeat countdown
        self.trade: TradePanel | None = None
        # What has actually been said, in order. Feeds the hub's history view and the
        # notes, and is the thing a companion would need if they were pulled in.
        self.transcript: list[tuple[str, str]] = []
        self.hub: ConvHub | None = None
        # The hub asking for an overlay `main.Game` owns (journal / map / party /
        # notes). Set here, taken and cleared there — the dialogue box has no business
        # constructing the game's panels.
        self.overlay_request: str | None = None
        self.combat_request: str | None = None   # npc_id if the NPC turned hostile
        # Someone else in the room cutting in (npc/interject.py). Capped per
        # conversation so a crowded room doesn't turn into a chorus.
        self.aside: tuple[str, str] | None = None      # (npc_id, line)
        self._aside_job: dict | None = None
        self._asides_left = MAX_ASIDES
        # Companions the player has pulled into this conversation (P in the hub). They
        # get a bigger share of the cut-ins and go first when the speaker names anyone.
        self._joined: set[str] = set()

        self._start_turn(APPROACH)

    # --- turn plumbing ----------------------------------------------------
    def _start_turn(self, player_input):
        if player_input != APPROACH:      # "the player walked up" was never said aloud
            self.transcript.append((YOU, player_input))
        self.hub = None                   # saying something puts you back in the room
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

    def _maybe_aside(self, out: dict):
        """The speaker named someone else here who'd want to cut in.

        The speaker is the only one who can hear that a line landed on a bystander,
        and asking them costs nothing — it rides on the reply we already paid for.
        The engine still decides whether it actually happens.
        """
        if self._asides_left <= 0 or self._aside_job is not None:
            return
        here = self.world.npcs[self.npc_id].room
        named = list(out.get("invoke_others") or [])
        named.sort(key=lambda n: n not in self._joined)   # whoever you brought in first
        for nid in named:
            npc = self.world.npcs.get(nid)
            if npc is None or nid == self.npc_id or npc.room != here:
                continue
            beat = {"kind": "said", "text": out.get("dialogue", ""),
                    "once_key": f"aside:{self.npc_id}:{len(self.banner)}",
                    "items": (), "npcs": (self.npc_id,), "room": here, "n": 0}
            job = {"done": False, "npc": nid, "text": ""}

            def run(nid=nid, beat=beat, job=job):
                try:
                    job["text"] = interject(self.world, nid, beat, NPCMemory(nid))
                except Exception:
                    job["text"] = ""
                job["done"] = True

            threading.Thread(target=run, daemon=True).start()
            self._aside_job = job
            self._asides_left -= 1
            return

    def bring_in(self, npc_id: str) -> str:
        """Wave a companion into this conversation (P in the hub, then Enter).

        They are already standing here — party members travel at your shoulder — so what
        actually changes is that they arrive *knowing what has been said*, say something
        about it, and speak up more often afterwards. Runs on a worker thread like every
        other call in this box; returns "" on success, or why not.
        """
        if npc_id == self.npc_id:
            return "You're already talking to them."
        if npc_id in self._joined:
            return "They're already in on this."
        if self._aside_job is not None:
            return "Someone's already speaking up."
        npc = self.world.npcs.get(npc_id)
        if npc is None or npc.room != self.world.npcs[self.npc_id].room:
            return "They're not here."

        # "You" is the *character* in every prompt this game writes, so the player has
        # to be named in the third person here or Wren reads her own question back as
        # hers. This codebase has shipped that confusion three times; see the memory
        # compaction note in CLAUDE.md.
        said = [(PLAYER_LABEL if who == YOU else character_name(who), text)
                for who, text in self.transcript]
        job = {"done": False, "npc": npc_id, "text": ""}

        def run():
            try:
                job["text"] = join_conversation(self.world, npc_id, self.npc_id,
                                                said, NPCMemory(npc_id))
            except Exception:                                   # noqa: BLE001
                job["text"] = ""
            job["done"] = True

        threading.Thread(target=run, daemon=True).start()
        self._aside_job = job
        self._joined.add(npc_id)
        self._asides_left += JOIN_ASIDES
        self.hub = None                 # you waved them over; watch them arrive
        return ""

    def _consume_result(self, out: dict):
        self.npc_line = out.get("dialogue", "…")
        self.reveal = 0.0
        self.mode = "reveal"
        self.banner = []
        self.aside = None
        self.scroll = 0                 # a new line is what you want to be looking at
        self.transcript.append((self.npc_id, self.npc_line))
        self._maybe_aside(out)
        for eff in (out.get("result").effects if out.get("result") else []):
            self.banner.append(eff)
        for q in out.get("completed_quests", []):
            self.banner.append(f"• Quest complete: “{q.title}”")
        err = out.get("error") or ""
        if err:
            # A setup problem needs saying plainly; a flaky endpoint gets the flavour.
            self.banner.append(f"! {err[:70]}" if "settings.json" in err
                               else "(the words don't come — connection trouble)")
        if out.get("result") and out["result"].end_dialogue:
            self._end_after_reveal = True
        if out.get("result") and out["result"].starts_combat:
            self.combat_request = self.npc_id
            self._end_after_reveal = True

    # --- update / events --------------------------------------------------
    def update(self, dt):
        self._caret = (self._caret + dt) % 1.0
        if self._aside_job is not None and self._aside_job["done"]:
            job, self._aside_job = self._aside_job, None
            if job["text"]:
                self.aside = (job["npc"], job["text"])
                self.transcript.append((job["npc"], job["text"]))
                self.scroll = 0         # somebody just spoke; show what they said
        if self.mode == "thinking" and self._turn and self._turn.done:
            self._consume_result(self._turn.value)
            self._turn = None
        elif self.mode == "reveal":
            self.reveal += dt * REVEAL_CPS
            if self.reveal >= len(self.npc_line):
                self.reveal = len(self.npc_line)
                self.mode = "closing" if self._end_after_reveal else "await"
                if self.mode == "closing":
                    self._close_timer = CLOSE_LOCKOUT
        elif self.mode == "closing":
            # A parting line used to vanish 0.6s after it finished drawing, which is not
            # long enough to read the one line in a conversation that matters most. It
            # now waits to be dismissed; the timer is only a guard against a held key.
            self._close_timer -= dt

        # Hold Backspace to keep deleting (the first delete happens on key-down).
        if self.mode == "await" and self.input_text and \
                pygame.key.get_pressed()[pygame.K_BACKSPACE]:
            self._bs_timer -= dt
            if self._bs_timer <= 0:
                self.input_text = self.input_text[:-1]
                self._bs_timer = _BS_INTERVAL
        else:
            self._bs_timer = _BS_DELAY

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return
        if self.trade is not None:
            cmd = self.trade.handle_event(event)
            if cmd:
                self._handle_trade(cmd)
            return
        if self.hub is not None:
            cmd = self.hub.handle_event(event)
            if cmd:
                self._handle_hub(cmd)
            return
        # Scrolling works in every mode, including while a parting line is being read.
        if event.key in SCROLL_UP:
            self.scroll = min(self.scroll + 1, self._max_scroll)
            return
        if event.key in SCROLL_DOWN:
            self.scroll = max(0, self.scroll - 1)
            return
        if event.key == pygame.K_ESCAPE:
            self.finished = True
            return
        # Ctrl/Cmd steps out of typing and into the hub, where letters are commands
        # again. Trade is one of them now rather than the only thing Ctrl could do.
        if event.key in HUB_KEYS:
            self.hub = ConvHub(self.world, self.npc_id, self.transcript,
                               is_vendor=self._is_vendor)
            return
        if self.mode == "closing":
            if self._close_timer <= 0:
                self.finished = True
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
            self._bs_timer = _BS_DELAY        # pause before hold-repeat kicks in
        elif event.unicode and event.unicode.isprintable() and len(self.input_text) < 160:
            self.input_text += event.unicode

    def _handle_hub(self, cmd):
        """The hub asks; this decides. Trade it can open itself, since the panel is
        already owned here; the rest belong to `main.Game` and go out as a request."""
        what = cmd.get("what")
        if cmd.get("cmd") == "close":
            self.hub = None
        elif cmd.get("cmd") == "note":
            note = self.world.add_note(cmd["text"], cmd.get("source", ""))
            self.hub.message = ("Written down." if note
                                else "You've already written that down.")
        elif what == "trade":
            if self.mode not in ("reveal", "await"):
                self.hub.message = "Wait until they've answered you."
            else:
                self.trade = (ShopPanel(self.world, self.npc_id, self.name)
                              if self._is_vendor
                              else TradePanel(self.world, self.npc_id, self.name))
        else:
            self.overlay_request = what

    def _handle_trade(self, cmd):
        action = cmd.get("cmd")
        npc = self.world.npcs[self.npc_id]
        item = cmd.get("item")
        name = display_name(item) if item else ""
        if action == "close":
            self.trade = None
        elif action == "show":
            # Nothing changes hands — you just hold it out. The NPC's briefing already
            # carries their own words about anything here they have a bond with.
            self.trade = None
            self._start_turn(f"[You hold out the {name} where {self.name} can see it, "
                             f"and say nothing.]")
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

    def _aside_visible(self) -> bool:
        """An aside only shows once the speaker's own line has finished revealing."""
        return bool(self.aside) and self.mode != "thinking" \
            and self.reveal >= len(self.npc_line)

    def _body_lines(self, max_w: int) -> list[tuple[str, pygame.font.Font, tuple, int]]:
        """Everything above the input row, already wrapped, one entry per drawn line.

        Built as a flat list so the viewport can simply take a window of it — which is
        the whole reason the aside no longer grows the box or gets truncated to two
        lines. It is just more body, and body scrolls.
        """
        out: list[tuple[str, pygame.font.Font, tuple, int]] = []
        if self.mode == "thinking":
            return out
        for ln in wrap_text(self.npc_line[: int(self.reveal)], T.font(19), max_w):
            out.append((ln, T.font(19), T.TEXT, 0))
        if self._aside_visible():
            nid, line = self.aside
            out.append((f"{character_name(nid)}:", T.font(15, bold=True),
                        T.npc_color(nid), 0))
            for ln in wrap_text(f"“{line}”", T.font(17), max_w - 12):
                out.append((ln, T.font(17), T.TEXT_DIM, 12))
        return out

    # --- draw -------------------------------------------------------------
    def draw(self, screen):
        box = pygame.Rect(12, T.PLAY_H - BOX_H - 12, T.SCREEN_W - 24, BOX_H)
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

        # Measure from the bottom up: the input row and the effect banner take what they
        # need, and the body viewport is whatever is left. Anything that doesn't fit
        # scrolls rather than being drawn over the things underneath it.
        inp_font = T.font(18, mono=True)
        tlabel = "[Ctrl] more"          # history, trade, journal, map, party, notes
        if self._max_scroll:            # last frame's, which is close enough for a hint
            tlabel = "Up/Down scroll · " + tlabel
        reserve = T.font(13).size(tlabel)[0] + 24     # keep typing clear of the hint
        if self.mode == "await":
            in_lines = (wrap_text("> " + self.input_text, inp_font, max_w - reserve)
                        or ["> "])[-_INPUT_LINES:]
        else:
            in_lines = [""]                      # one row, for the hint
        input_top = box.bottom - 12 - INPUT_LINE_H * len(in_lines)
        rule_y = input_top - 8
        banner = self.banner[:2]
        banner_top = rule_y - 6 - 18 * len(banner)
        body_bot = banner_top - 4

        # --- the NPC's words, in a clipped, scrollable viewport ---
        view = pygame.Rect(body_x, body_y, max_w, max(LINE_H, body_bot - body_y))
        if self.mode == "thinking":
            draw_text(screen, "." * (1 + int(self._caret * 3)), (body_x, body_y),
                      T.font(22), T.TEXT_DIM)
        else:
            lines = self._body_lines(max_w)
            visible = max(1, view.height // LINE_H)
            self._max_scroll = max(0, len(lines) - visible)
            self.scroll = max(0, min(self.scroll, self._max_scroll))
            start = max(0, len(lines) - visible - self.scroll)
            screen.set_clip(view)
            y = view.top
            for text, fnt, color, indent in lines[start:start + visible]:
                draw_text(screen, text, (body_x + indent, y), fnt, color)
                y += LINE_H
            screen.set_clip(None)
            if self._max_scroll:
                self._draw_scrollbar(screen, view, len(lines), visible, start)

        if banner:
            by = banner_top
            for line in banner:
                draw_text(screen, line, (body_x, by), T.font(14, bold=True), T.EFFECT)
                by += 18

        # --- input row (shows the tail as it overflows) ---
        pygame.draw.line(screen, T.WALL, (box.left + 14, rule_y),
                         (box.right - 14, rule_y), 1)
        if self.mode == "await":
            caret = "|" if self._caret < 0.5 else ""
            ly = input_top
            for i, ln in enumerate(in_lines):
                draw_text(screen, ln + (caret if i == len(in_lines) - 1 else ""),
                          (body_x, ly), inp_font, T.TEXT)
                ly += INPUT_LINE_H
            draw_text(screen, tlabel, (box.right - 16, input_top + 2),
                      T.font(13), T.TEXT_DIM, right=True)
        elif self.mode == "reveal":
            draw_text(screen, f"[Enter] skip · {tlabel}", (body_x, input_top),
                      T.font(14), T.TEXT_DIM)
        elif self.mode == "closing":
            draw_text(screen, "[Enter] leave", (body_x, input_top),
                      T.font(14), T.TEXT_WARN)
        else:
            draw_text(screen, "…", (body_x, input_top), T.font(14), T.TEXT_DIM)

        # The trade panel's own backdrop is half-transparent (it was built to sit over
        # the small dialogue box), so the hub steps aside rather than showing through it.
        if self.hub is not None and self.trade is None:
            self.hub.draw(screen)
        if self.trade is not None:
            self.trade.draw(screen)

    @staticmethod
    def _draw_scrollbar(screen, view, total: int, visible: int, start: int) -> None:
        """A thin bar on the right edge — the font has no arrow glyphs to point with."""
        track = pygame.Rect(view.right - 3, view.top, 3, view.height)
        pygame.draw.rect(screen, (52, 50, 68), track, border_radius=2)
        h = max(10, int(track.height * visible / total))
        top = track.top + int(track.height * start / total)
        pygame.draw.rect(screen, (120, 116, 146),
                         pygame.Rect(track.left, min(top, track.bottom - h), 3, h),
                         border_radius=2)
