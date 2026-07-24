"""The combat scene: menu-driven turns, an ACT/mercy submenu, a threaded enemy
round (LLM), and a message log. Drives engine/combat.py and npc/combat_agent.py.

CombatScene.finished + CombatScene.outcome tell the Game when a fight is over.
"""
from __future__ import annotations

import threading

import pygame

from engine.combat import (Combat, player_attack, player_defend, player_spare)
from engine.items import ITEMS, display_name
from npc.combat_agent import ally_turn, enemy_turn, mercy_attempt, speak_to_ally
from ui import sprites
from ui import theme as T
from ui.render import draw_text, wrap_text


class CombatScene:
    def __init__(self, world, combat: Combat):
        self.world = world
        self.combat = combat
        self.phase = "menu"          # menu | act_target | act_input | item | target | resolving | ended
        self.sel = 0
        self.sub_sel = 0
        self.act_text = ""           # free-text speech for ACT
        self._act_target = None      # whoever you're speaking to (foe or companion)
        self.finished = False
        self.outcome = ""
        self._busy = False           # a turn is resolving on the worker thread
        self._caret = 0.0

    # --- menu contents ----------------------------------------------------
    def _menu(self):
        opts = ["Attack", "Defend", "Act", "Item"]
        if any(e.spareable for e in self.combat.enemies()):
            opts.append("Spare")
        opts.append("Flee")
        return opts

    def _heal_items(self):
        inv = self.world.player.inventory
        seen, out = set(), []
        for it in inv:
            if it in seen:
                continue
            seen.add(it)
            if ITEMS.get(it, {}).get("heal", 0) > 0:
                out.append((it, inv.count(it)))
        return out

    def _target(self):
        alive = self.combat.enemies()
        return alive[min(self.sub_sel, len(alive) - 1)] if alive else None

    def _act_targets(self):
        """Who you can speak to: standing foes first, then your companions."""
        return self.combat.enemies() + [a for a in self.combat.allies() if a.alive]

    # --- turn resolution (threaded, because enemy turns hit the LLM) ------
    def _resolve(self, player_action):
        """Run the player's action then the opponents' round on a worker thread."""
        self._busy = True
        self.phase = "resolving"

        def work():
            try:
                if player_action:
                    player_action()
                self.combat.check_end()
                if not self.combat.over:
                    for ally in self.combat.allies():
                        if ally.alive:
                            ally_turn(self.combat, ally, self.world)
                        if self.combat.over:
                            break
                    self.combat.check_end()
                if not self.combat.over:
                    for enemy in list(self.combat.enemies()):
                        if enemy.alive and not enemy.spareable:
                            enemy_turn(self.combat, enemy)
                        if self.combat.over:
                            break
                self.combat.check_end()
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def _finish(self):
        # write combat HP back to the world and report the outcome
        self.world.player.hp = self.combat.player().hp
        self.finished = True
        self.outcome = self.combat.outcome

    # --- input ------------------------------------------------------------
    def handle_event(self, event):
        if event.type != pygame.KEYDOWN or self._busy or self.phase == "resolving":
            return
        if self.phase == "menu":
            self._menu_key(event)
        elif self.phase == "target":
            self._list_key(event, self.combat.enemies(), self._on_target)
        elif self.phase == "act_target":
            self._list_key(event, self._act_targets(), self._on_act_target)
        elif self.phase == "act_input":
            self._act_input_key(event)
        elif self.phase == "item":
            self._list_key(event, self._heal_items(), self._on_item)

    def _menu_key(self, event):
        opts = self._menu()
        if event.key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(opts)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(opts)
        elif event.key in (pygame.K_RETURN, pygame.K_e, pygame.K_SPACE):
            self._choose(opts[self.sel])

    def _list_key(self, event, items, on_choose):
        if event.key == pygame.K_ESCAPE:
            self.phase = "menu"
        elif not items:
            self.phase = "menu"
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.sub_sel = (self.sub_sel - 1) % len(items)
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.sub_sel = (self.sub_sel + 1) % len(items)
        elif event.key in (pygame.K_RETURN, pygame.K_e):
            on_choose(items[self.sub_sel])

    def _choose(self, option):
        enemies = self.combat.enemies()
        if option == "Attack":
            if len(enemies) == 1:
                self._resolve(lambda t=enemies[0]: player_attack(self.combat, t))
            else:
                self.sub_sel = 0; self.phase = "target"
        elif option == "Defend":
            self._resolve(lambda: player_defend(self.combat))
        elif option == "Act":
            targets = self._act_targets()
            if len(targets) <= 1:
                self._act_target = targets[0] if targets else None
                self.act_text = ""
                self.phase = "act_input"
            else:
                self.sub_sel = 0
                self.phase = "act_target"
        elif option == "Item":
            self.sub_sel = 0; self.phase = "item"
        elif option == "Spare":
            target = next((e for e in enemies if e.spareable), None)
            if target:
                self._resolve(lambda: player_spare(self.combat, target))
        elif option == "Flee":
            self._resolve(self._flee)

    def _on_target(self, enemy):
        self._resolve(lambda: player_attack(self.combat, enemy))

    def _on_act_target(self, combatant):
        self._act_target = combatant
        self.act_text = ""
        self.phase = "act_input"

    def _act_input_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.phase = "menu"
        elif event.key == pygame.K_RETURN:
            said = self.act_text.strip()
            if said:
                tgt = self._act_target
                self.combat.add_log(f"You: “{said}”")
                if tgt is not None and tgt.side == "ally":
                    self._resolve(lambda: speak_to_ally(self.combat, tgt, said, self.world))
                else:
                    self._resolve(lambda: mercy_attempt(self.combat, tgt, said))
        elif event.key == pygame.K_BACKSPACE:
            self.act_text = self.act_text[:-1]
        elif event.unicode and event.unicode.isprintable() and len(self.act_text) < 140:
            self.act_text += event.unicode

    def _on_item(self, entry):
        item = entry[0]

        def use():
            heal = ITEMS.get(item, {}).get("heal", 0)
            p = self.combat.player()
            before = p.hp
            p.hp = min(p.max_hp, p.hp + heal)
            if item in self.world.player.inventory:
                self.world.player.inventory.remove(item)
            self.combat.add_log(
                f"You use the {display_name(item)}. (+{p.hp - before} HP)")
        self._resolve(use)

    def _flee(self):
        # a coin-flip escape; failure just wastes the turn
        if (self.combat.player().hp % 2) == 0:
            self.combat.over, self.combat.outcome = True, "fled"
            self.combat.add_log("You break away and flee down the ridge.")
        else:
            self.combat.add_log("You try to flee, but the dark closes the way.")

    # --- update -----------------------------------------------------------
    def update(self, dt):
        self._caret = (self._caret + dt) % 1.0
        if self.phase == "resolving" and not self._busy:
            if self.combat.over:
                self.phase = "ended"
                self._finish()
            else:
                self.phase = "menu"
                self.sel = 0

    # --- draw -------------------------------------------------------------
    def draw(self, screen):
        screen.fill((10, 9, 16))
        # enemies
        enemies = self.combat.enemies(alive_only=False)
        n = max(1, len(enemies))
        for i, e in enumerate(enemies):
            cx = int(T.SCREEN_W * (i + 1) / (n + 1))
            spr = sprites.enemy_surface(e.id.split("_")[0])
            if not e.alive:
                spr = spr.copy(); spr.set_alpha(60)
            screen.blit(spr, (cx - spr.get_width() // 2, 40))
            _bar(screen, cx - 90, 250, 180, e.hp, e.max_hp, (210, 90, 90), e.name)
            if e.alive and e.persona:
                _bar(screen, cx - 90, 272, 180, 100 - e.resolve, 100,
                     (120, 170, 220), "resolve worn", small=True)
            if e.spareable:
                draw_text(screen, "can be spared", (cx, 296), T.font(14, bold=True),
                          T.TEXT_GOOD, center=True)

        # player HP
        p = self.combat.player()
        _bar(screen, 20, T.PLAY_H - 40, 220, p.hp, p.max_hp, (110, 200, 120),
             f"You  {p.hp}/{p.max_hp}")

        # companions fighting beside you
        ax = 260
        for a in self.combat.allies(alive_only=False):
            col = (120, 170, 220) if a.alive else (90, 90, 100)
            _bar(screen, ax, T.PLAY_H - 40, 170, a.hp, a.max_hp, col,
                 f"{a.name}  {a.hp}/{a.max_hp}")
            ax += 190

        # log
        self._draw_log(screen)
        # menu / submenu
        if self.phase == "resolving":
            dots = "." * (1 + int(self._caret * 3))
            draw_text(screen, dots, (T.SCREEN_W // 2, T.SCREEN_H - 60),
                      T.font(24), T.TEXT_DIM, center=True)
        elif self.phase == "ended":
            draw_text(screen, "[Enter] continue", (T.SCREEN_W // 2, T.SCREEN_H - 30),
                      T.font(16), T.TEXT_DIM, center=True)
        else:
            self._draw_menu(screen)

    def _draw_log(self, screen):
        box = pygame.Rect(20, T.PLAY_H - 150, T.SCREEN_W - 40, 96)
        s = pygame.Surface(box.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 150))
        screen.blit(s, box.topleft)
        y = box.top + 8
        for line in self.combat.log[-3:]:
            for ln in wrap_text(line, T.font(15), box.width - 20):
                draw_text(screen, ln, (box.left + 10, y), T.font(15), T.TEXT)
                y += 20

    def _draw_act_input(self, screen):
        tgt = self._act_target
        y = T.SCREEN_H - 92
        draw_text(screen, f"Speak to {tgt.name if tgt else 'it'}:",
                  (30, y), T.font(17, bold=True), T.TEXT)
        caret = "|" if self._caret < 0.5 else " "
        draw_text(screen, "> " + self.act_text + caret, (30, y + 28),
                  T.font(18, mono=True), T.HEARTH)
        draw_text(screen, "Enter to speak · Esc back", (T.SCREEN_W - 20, T.SCREEN_H - 28),
                  T.font(14), T.TEXT_DIM, right=True)

    def _draw_menu(self, screen):
        if self.phase == "act_input":
            self._draw_act_input(screen)
            return
        if self.phase == "menu":
            items = self._menu()
            sel = self.sel
        elif self.phase == "target":
            items = [e.name for e in self.combat.enemies()]
            sel = self.sub_sel
        elif self.phase == "act_target":
            items = [f"{c.name} ({'foe' if c.side == 'enemy' else 'companion'})"
                     for c in self._act_targets()]
            sel = self.sub_sel
        else:  # item
            heals = self._heal_items()
            items = [f"{display_name(i)} x{n}" for i, n in heals] or ["(no usable items)"]
            sel = self.sub_sel

        x = 30
        y = T.SCREEN_H - 92
        for i, opt in enumerate(items):
            chosen = i == sel
            draw_text(screen, ("> " if chosen else "  ") + opt, (x, y),
                      T.font(20, bold=chosen), T.HEARTH if chosen else T.TEXT)
            y += 28
            if y > T.SCREEN_H - 20:
                y = T.SCREEN_H - 92; x += 220
        if self.phase != "menu":
            draw_text(screen, "[Esc] back", (T.SCREEN_W - 20, T.SCREEN_H - 28),
                      T.font(14), T.TEXT_DIM, right=True)


def _bar(screen, x, y, w, val, maxv, color, label, small=False):
    h = 10 if small else 16
    pygame.draw.rect(screen, (40, 38, 52), (x, y, w, h), border_radius=3)
    frac = 0 if maxv <= 0 else max(0.0, min(1.0, val / maxv))
    if frac > 0:
        pygame.draw.rect(screen, color, (x, y, int(w * frac), h), border_radius=3)
    pygame.draw.rect(screen, (12, 12, 18), (x, y, w, h), 1, border_radius=3)
    if label:
        draw_text(screen, label, (x, y - (16 if not small else 14)),
                  T.font(13 if small else 15, bold=not small),
                  T.TEXT_DIM if small else T.TEXT)
