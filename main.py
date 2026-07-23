"""The Last Lamplighter — entry point and overworld loop.

Run:  .venv/bin/python main.py           (persists NPC memory across runs)
      .venv/bin/python main.py --fresh   (wipes runtime memory first)
"""
from __future__ import annotations

import sys

import pygame

from engine.quests import refresh_and_complete
from engine.save import (AUTOSAVE, latest_save, load_bundle, save_bundle,
                         wipe_all_saves)
from engine.world import ensure_world_complete, new_world, starter_quest
from npc.memory import NPCMemory
from npc.roster import character_name
from ui import theme as T
from ui.dialogue import DialogueBox
from ui.journal import draw_journal
from ui.menu import Menu
from ui.render import draw_hud, draw_overworld, draw_text

MOVE_DELAY = 0.12   # seconds between grid steps while a direction is held
TOAST_TIME = 2.6

DIRS = {
    pygame.K_LEFT: (-1, 0), pygame.K_a: (-1, 0),
    pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
    pygame.K_UP: (0, -1), pygame.K_w: (0, -1),
    pygame.K_DOWN: (0, 1), pygame.K_s: (0, 1),
}
INTERACT_KEYS = {pygame.K_e, pygame.K_SPACE, pygame.K_RETURN}


class Game:
    def __init__(self, fresh: bool = False):
        if fresh:
            NPCMemory.wipe_all()
            wipe_all_saves()
        pygame.init()
        pygame.display.set_caption("The Last Lamplighter")
        self.screen = pygame.display.set_mode((T.SCREEN_W, T.SCREEN_H))
        self.clock = pygame.time.Clock()

        self.world, self.rooms, self.known = new_world()
        self.save_name = AUTOSAVE
        self.loaded_save = False
        continue_slot = None if fresh else latest_save()
        if continue_slot:
            try:
                world, mems = load_bundle(continue_slot)
                NPCMemory.restore_all(mems)
                ensure_world_complete(world)
                self.world = world
                self.save_name = continue_slot
                self.loaded_save = True
            except (ValueError, KeyError, OSError) as e:
                print(f"Could not load save ({e}); starting fresh.")

        self.memories: dict[str, NPCMemory] = {}
        self.menu = Menu()
        self.scene = "intro"          # intro | overworld | dialogue
        self.dialogue: DialogueBox | None = None
        self.journal_open = False
        self.menu_open = False
        self.move_timer = 0.0
        self.toast = ""
        self.toast_timer = 0.0
        self.running = True

    def memory_for(self, npc_id: str) -> NPCMemory:
        if npc_id not in self.memories:
            self.memories[npc_id] = NPCMemory(npc_id)
        return self.memories[npc_id]

    # --- persistence ------------------------------------------------------
    def do_save(self, name: str) -> None:
        save_bundle(self.world, NPCMemory.snapshot_all(), name)
        self.save_name = name

    def do_load(self, name: str) -> None:
        world, mems = load_bundle(name)
        NPCMemory.restore_all(mems)
        ensure_world_complete(world)
        self.world = world
        self.memories = {}            # drop cache so live memory reloads from disk
        self.save_name = name
        self.menu_open = False
        self.scene = "overworld"
        self.dialogue = None
        self.set_toast(f"Loaded “{name}”.")

    def handle_menu_command(self, cmd: dict) -> None:
        action = cmd.get("cmd")
        if action == "close":
            self.menu_open = False
        elif action == "save":
            self.do_save(self.save_name)
            self.set_toast(f"Saved to “{self.save_name}”.")
        elif action == "save_as":
            self.do_save(cmd["name"])
            self.menu.mode = "main"
            self.set_toast(f"Saved as “{self.save_name}”.")
        elif action == "load":
            self.do_load(cmd["name"])
        elif action == "save_quit":
            self.do_save(self.save_name)
            self.running = False

    def set_toast(self, text: str):
        self.toast = text
        self.toast_timer = TOAST_TIME

    def on_quests_completed(self, completed):
        """Announce completions and give each quest's giver a personal memory."""
        for q in completed:
            self.set_toast(f"Quest complete: {q.title}!")
            if q.giver in self.world.npcs:
                self.memory_for(q.giver).remember(
                    f'The player completed the quest you gave them: "{q.title}".'
                )

    # --- helpers ----------------------------------------------------------
    def occupied(self, x, y) -> bool:
        room = self.world.player.room
        return any(n.room == room and (n.x, n.y) == (x, y) for n in self.world.npcs.values())

    def adjacent_targets(self):
        """Return (kind, id) for whatever the player can interact with nearby."""
        px, py = self.world.player.x, self.world.player.y
        room = self.rooms[self.world.player.room]
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tx, ty = px + dx, py + dy
            for n in self.world.npcs.values():
                if n.room == room.id and (n.x, n.y) == (tx, ty):
                    return ("npc", n.npc_id)
            lamp = room.lamp_at(tx, ty)
            if lamp:
                return ("lamp", lamp)
            door = room.door_at(tx, ty)
            if door and door.locked:
                return ("locked", door.locked_msg)
        return (None, None)

    def interaction_hint(self) -> str:
        kind, ident = self.adjacent_targets()
        if kind == "npc":
            return f"E: talk to {character_name(ident)}"
        if kind == "lamp":
            return "E: relight lamp" if not self.world.lamps.get(ident) else ""
        if kind == "locked":
            return "the ridge path is sealed"
        return ""

    # --- overworld actions ------------------------------------------------
    def try_move(self, dx, dy):
        p = self.world.player
        room = self.rooms[p.room]
        tx, ty = p.x + dx, p.y + dy
        door = room.door_at(tx, ty)
        if door:
            if door.locked:
                self.set_toast(door.locked_msg)
                return
            p.room = door.to_room
            p.x, p.y = door.spawn
            self.world.events.record(
                "arrive", f"You entered {self.rooms[p.room].name}.", public=False
            )
            self.on_quests_completed(refresh_and_complete(self.world))  # "reach" objectives
            return
        if (tx, ty) in room.blocked() or self.occupied(tx, ty):
            return
        p.x, p.y = tx, ty

    def interact(self):
        kind, ident = self.adjacent_targets()
        if kind == "npc":
            self.open_dialogue(ident)
        elif kind == "lamp":
            if not self.world.lamps.get(ident):
                if not self.world.consume_item("oil_flask"):
                    self.set_toast("The lamp is dry. You need oil — perhaps Wren has some.")
                    return
                self.world.lamps[ident] = True
                room = self.rooms[self.world.player.room]
                self.world.events.record("lamp_lit", f"You relit a lamp in {room.name}.")
                self.set_toast("You pour the oil and coax the lamp back to light.")
                self.on_quests_completed(refresh_and_complete(self.world))
        elif kind == "locked":
            self.set_toast(ident)

    def open_dialogue(self, npc_id):
        # Deterministic onboarding: Wren always has the starter quest to give,
        # and reliably supplies the oil needed to light the lamps.
        if npc_id == "wren" and not self.world.has_quest("relight_the_lamps"):
            self.world.quests.append(starter_quest())
            wren = self.world.npcs["wren"]
            moved = 0
            while moved < 3 and "oil_flask" in wren.inventory:
                wren.inventory.remove("oil_flask")
                self.world.player.inventory.append("oil_flask")
                moved += 1
            self.world.events.record(
                "quest_start", "Wren asked you to relight the three lamps and gave you oil."
            )
            self.set_toast(f"New quest: Relight the Lamps  (+{moved} oil)")
        self.dialogue = DialogueBox(
            self.world, self.rooms, self.known, npc_id, self.memory_for(npc_id)
        )
        self.scene = "dialogue"

    # --- loop -------------------------------------------------------------
    def run(self):
        while self.running:
            dt = self.clock.tick(T.FPS) / 1000.0
            self.handle_events()
            self.update(dt)
            self.draw()
            pygame.display.flip()
        self.do_save(self.save_name)   # safety autosave on exit
        pygame.quit()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif self.menu_open:
                cmd = self.menu.handle_event(event)
                if cmd:
                    self.handle_menu_command(cmd)
            elif self.scene == "intro":
                if event.type == pygame.KEYDOWN:
                    self.scene = "overworld"
                    if self.loaded_save:
                        self.set_toast("Progress restored.")
            elif self.scene == "dialogue":
                if self.dialogue:
                    self.dialogue.handle_event(event)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.journal_open:
                        self.journal_open = False
                    else:
                        self.menu.open()
                        self.menu_open = True
                elif event.key == pygame.K_j:
                    self.journal_open = not self.journal_open
                elif not self.journal_open and event.key in INTERACT_KEYS:
                    self.interact()

    def update(self, dt):
        if self.toast_timer > 0:
            self.toast_timer -= dt

        if self.menu_open:
            self.menu.update(dt)
            return

        if self.scene == "overworld" and not self.journal_open:
            self.move_timer -= dt
            if self.move_timer <= 0:
                keys = pygame.key.get_pressed()
                for key, (dx, dy) in DIRS.items():
                    if keys[key]:
                        self.try_move(dx, dy)
                        self.move_timer = MOVE_DELAY
                        break
        elif self.scene == "dialogue" and self.dialogue:
            self.dialogue.update(dt)
            if self.dialogue.finished:
                self.dialogue = None
                self.scene = "overworld"
                refresh_and_complete(self.world)

    # --- draw -------------------------------------------------------------
    def draw(self):
        self.screen.fill(T.BG)
        draw_overworld(self.screen, self.world, self.rooms)
        hint = self.interaction_hint() if self.scene == "overworld" else ""
        draw_hud(self.screen, self.world, self.rooms, hint)

        if self.scene == "intro":
            self.draw_intro()
        elif self.scene == "dialogue" and self.dialogue:
            self.dialogue.draw(self.screen)

        if self.journal_open:
            draw_journal(self.screen, self.world)
        if self.menu_open:
            self.menu.draw(self.screen, self.save_name)

        if self.toast_timer > 0:
            self.draw_toast()

    def draw_toast(self):
        img = T.font(17, bold=True).render(self.toast, True, T.TEXT)
        rect = img.get_rect(center=(T.SCREEN_W // 2, 24))
        bg = rect.inflate(20, 12)
        s = pygame.Surface(bg.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 190))
        self.screen.blit(s, bg.topleft)
        self.screen.blit(img, rect)

    def draw_intro(self):
        overlay = pygame.Surface((T.SCREEN_W, T.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 205))
        self.screen.blit(overlay, (0, 0))
        cx = T.SCREEN_W // 2
        draw_text(self.screen, "THE LAST LAMPLIGHTER", (cx, 120),
                  T.font(34, bold=True), T.HEARTH, center=True)
        lines = [
            "Emberhold is dying in permanent dusk.",
            "The great Hearthlight is failing, and something on the ridge",
            "is eating the light. The townsfolk still linger — each with",
            "their own fears, needs, and stories to tell.",
            "",
            "Talk to them. Help them, or don't. Find your way to the ridge.",
            "",
            "Move: Arrows / WASD    Interact: E    Journal: J    Menu: Esc",
            "",
            "Press any key to begin.",
        ]
        y = 190
        for ln in lines:
            draw_text(self.screen, ln, (cx, y), T.font(18), T.TEXT, center=True)
            y += 30


def main():
    fresh = "--fresh" in sys.argv
    Game(fresh=fresh).run()


if __name__ == "__main__":
    main()
