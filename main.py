"""The Last Lamplighter — entry point and overworld loop.

Run:  .venv/bin/python main.py             (persists NPC memory across runs)
      .venv/bin/python main.py --fresh     (wipes runtime memory first)
      .venv/bin/python main.py --log-llm   (writes every prompt + reply to logs/)
"""
from __future__ import annotations

import random
import sys
import threading

import pygame

from engine.combat import combatant_from_npc, enemies_from_ids, make_combat
from engine.interact import apply_interaction, is_live
from engine.items import CURRENCY, display_name, use_item
from engine.quests import CHECK_BACK, find_check_back, refresh_and_complete
from engine.save import (AUTOSAVE, latest_save, load_bundle, save_bundle,
                         wipe_all_saves)
from engine.state import GroundItem
from engine.trade import is_vendor
from engine.cartography import mark_visited
from engine.pacing import bump_tick, heartbeat
from llm import log as llm_log
from llm.client import settings_problem
from engine.witness import (AMBIENT, BEAT, MAJOR, NOTE, record_experience,
                            witnesses)
from npc.agenda import note_quest_done
from npc.bonds import bond_for
from npc.interject import choose_interjector, interject
from engine.world import NPC_SPAWNS, PRIVATE_ROOMS, RIDGE_ROOMS
from engine.world import ensure_world_complete, new_world
from npc.memory import NPCMemory
from npc.roster import character_name
from ui import theme as T
from ui.dialogue import DialogueBox
from ui.combat import CombatScene
from ui.epilogue import Epilogue
from ui.inventory import InventoryPanel
from ui.journal import draw_journal
from ui.mapview import draw_full_map, draw_minimap
from ui.night import NightScene, mark_rested, night_facts
from ui.menu import Menu
from ui.party import PartyPanel
from ui.storage import StoragePanel
from ui.render import draw_hud, draw_overworld, draw_text

MOVE_DELAY = 0.12   # seconds between grid steps while a direction is held
TOAST_TIME = 2.6
BARK_TIME = 5.0     # how long a companion's unprompted remark stays on screen

# --- ambient NPC movement ---
# Each idle NPC alternates between standing still and wandering: after every step
# it decides whether to take another or settle. Everyone starts standing still, so
# the world is calm until someone chooses to move.
STEP_DELAY = 0.55           # seconds between steps while wandering
STILL_MIN, STILL_MAX = 4.0, 12.0    # how long a standing NPC stays put
P_KEEP_WANDERING = 0.55     # after a step, chance of taking another rather than stopping
P_HOP_ROOM = 0.18           # chance a wander step goes through a door instead
P_RETURN_HOME = 0.6         # chance an away NPC's room-hop heads back home
ROAM_DEFAULT = 1            # doors from home a character will drift, unless
                            # their file says otherwise (0 = stays put)
# Townsfolk never wander onto the ridge.
AMBIENT_BLOCKED_ROOMS = RIDGE_ROOMS

DIRS = {
    pygame.K_LEFT: (-1, 0), pygame.K_a: (-1, 0),
    pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
    pygame.K_UP: (0, -1), pygame.K_w: (0, -1),
    pygame.K_DOWN: (0, 1), pygame.K_s: (0, 1),
}
INTERACT_KEYS = {pygame.K_e, pygame.K_SPACE, pygame.K_RETURN}

CAMP_ROOM = "camp"
CAMP_SPOT = (9, 8)          # beside the fire, not on it


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
        self.map_open = False
        self.epilogue: Epilogue | None = None
        self.menu_open = False
        self.inventory_open = False
        self.inv_panel: InventoryPanel | None = None
        self.combat_scene: CombatScene | None = None
        self.combat_context: dict | None = None
        self.move_timer = 0.0
        self.toast = ""
        self.toast_timer = 0.0
        self.party_open = False
        self.party_panel: PartyPanel | None = None
        self.storage_open = False
        self.storage_panel: StoragePanel | None = None
        self.night: NightScene | None = None
        self.camp_prompt: str | None = None       # confirm text while R is pending
        self._trail: list[tuple[int, int]] = []   # player's recent tiles (for followers)
        self._ambient: dict[str, dict] = {}       # per-NPC {mode, timer}; runtime only
        self._beat_no = 0                         # notable beats, for bark cooldowns
        self._bark: dict | None = None            # {npc, text, timer} currently showing
        self._bark_job: dict | None = None        # an interjection being written
        # Checked once at boot so the title screen can say so, rather than the
        # player finding out by walking up to somebody and getting nothing.
        self.llm_problem = settings_problem()
        self.running = True
        mark_visited(self.world, self.world.player.room)
        self._gather_party()

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
        self._gather_party()          # place restored companions beside you
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

    def handle_inventory_command(self, cmd: dict) -> None:
        action = cmd.get("cmd")
        if action == "close":
            self.inventory_open = False
        elif action == "use":
            item = cmd["item"]
            result = use_item(self.world, item)
            if result.consumed:
                self.world.consume_item(item)
            if self.inv_panel:
                self.inv_panel.message = result.message
        elif action == "drop":
            self.drop_item(cmd["item"])
            if self.inv_panel:
                self.inv_panel.message = f"Dropped {display_name(cmd['item'])}."

    def handle_party_command(self, cmd: dict) -> None:
        action = cmd.get("cmd")
        if action == "close":
            self.party_open = False
        elif action == "talk":
            # Talk to a companion in a normal conversation. If you ask them to part
            # ways, they decide to leave (the leave_party action), and no other way.
            self.party_open = False
            self.open_dialogue(cmd["npc"])

    def set_toast(self, text: str):
        self.toast = text
        self.toast_timer = TOAST_TIME

    # --- companions speaking up -------------------------------------------
    def beat(self, kind: str, text: str, *, once_key: str, items=(), npcs=()):
        """Something notable just happened. If it touches somebody standing here hard
        enough, they get one line about it — decided by a free rule check first, so
        most beats cost nothing at all (npc/interject.py)."""
        self._beat_no += 1
        if self._bark_job is not None or self._bark is not None:
            return                          # one voice at a time
        b = {"kind": kind, "text": text, "once_key": once_key,
             "items": tuple(items), "npcs": tuple(npcs),
             "room": self.world.player.room, "n": self._beat_no}
        npc_id = choose_interjector(self.world, b)
        if npc_id is None:
            return
        job = {"done": False, "npc": npc_id, "text": ""}

        def run():
            try:
                job["text"] = interject(self.world, npc_id, b, self.memory_for(npc_id))
            except Exception:               # a bark must never take the game down
                job["text"] = ""
            job["done"] = True

        threading.Thread(target=run, daemon=True).start()
        self._bark_job = job

    def _update_bark(self, dt):
        job = self._bark_job
        if job is not None and job["done"]:
            self._bark_job = None
            if job["text"]:
                self._bark = {"npc": job["npc"], "text": job["text"], "timer": BARK_TIME}
        if self._bark is not None:
            self._bark["timer"] -= dt
            if self._bark["timer"] <= 0:
                self._bark = None

    def on_quests_completed(self, completed):
        """Announce completions and give each quest's giver a personal memory."""
        for q in completed:
            self.set_toast(f"Quest complete: {q.title}!")
            if q.giver in self.world.npcs:
                self.memory_for(q.giver).remember(
                    f'The player completed the quest you gave them: "{q.title}".'
                )
                # Their own goal just moved — make sure they notice next time you talk.
                note_quest_done(self.world, q.giver, q.title)
        # Going to see somebody because a note told you to is not progress. Counting
        # it as such made the notes self-perpetuating: clear one, earn a tick, get
        # handed the next.
        if any(q.objective.type != CHECK_BACK for q in completed):
            self.progress("quest")
        if completed:
            self._auto_commission()

    def progress(self, kind: str):
        """One unit of progress happened — a quest finished, a night passed, a new room
        seen. The world may answer by nudging somebody the player has been neglecting
        (engine/pacing.py); it stays quiet if their plate is already full."""
        bump_tick(self.world, kind)
        q = heartbeat(self.world)
        if q is not None:
            self.set_toast(f"New note: {q.title}")

    def _auto_commission(self):
        """A 'check back with X' breadcrumb makes no sense when X is walking beside you.
        They were there; they'd speak up. So open the conversation instead of sending
        the player off to find someone who never left."""
        if self.scene != "overworld" or self.dialogue is not None:
            return
        for nid in self.world.party:
            if find_check_back(self.world, nid):
                self.open_dialogue(nid)
                return

    # --- helpers ----------------------------------------------------------
    def occupied(self, x, y) -> bool:
        """A tile is blocked by an NPC — but companions in your party step aside
        (they trail you), so they never wall you in."""
        room = self.world.player.room
        return any(n.room == room and (n.x, n.y) == (x, y)
                   and n.npc_id not in self.world.party
                   for n in self.world.npcs.values())

    def adjacent_targets(self):
        """Return (kind, id) for whatever the player can interact with nearby."""
        px, py = self.world.player.x, self.world.player.y
        room = self.rooms[self.world.player.room]
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tx, ty = px + dx, py + dy
            for n in self.world.npcs.values():
                # Companions trail right behind you — don't let E perpetually snag them;
                # you talk to party members from the party view (P) instead. At camp they
                # are sitting still around the fire, so E is exactly the right way to
                # reach them, and the P route still works everywhere.
                if n.npc_id in self.world.party and room.id != CAMP_ROOM:
                    continue
                if n.room == room.id and (n.x, n.y) == (tx, ty):
                    return ("npc", n.npc_id)
            inter = room.interactable_at(tx, ty)
            if inter is not None and not inter.hidden:
                return ("use", inter)
            door = room.door_at(tx, ty)
            if door and not door.passable(self.world):
                return ("locked", door.locked_msg)
        return (None, None)

    def interaction_hint(self) -> str:
        kind, ident = self.adjacent_targets()
        if kind == "npc":
            return f"E: talk to {character_name(ident)}"
        if kind == "use":
            # A used-up thing shows its spent hint, or nothing at all (a lit lamp).
            return ident.hint if is_live(self.world, ident) else ident.spent_hint
        if kind == "locked":
            return ident or "it will not open"
        return ""

    # --- overworld actions ------------------------------------------------
    def try_move(self, dx, dy):
        p = self.world.player
        room = self.rooms[p.room]
        tx, ty = p.x + dx, p.y + dy
        door = room.door_at(tx, ty)
        if door:
            if door.to_room == "ridge_foot" and not self._ridge_open():
                self.set_toast(self._ridge_locked_msg())
                return
            if not door.passable(self.world):
                self.set_toast(door.locked_msg or "It will not open.")
                return
            # Walking out of camp on your own feet means you're not coming back to
            # wherever R plucked you from — the return note only survives a return by R.
            if p.room == CAMP_ROOM:
                self.world.flags.pop("camp_return", None)
            p.room = door.to_room
            p.x, p.y = door.spawn
            self._gather_party()          # companions follow you through the door
            self._record_arrival()
            self.try_pickup()
            self.on_quests_completed(refresh_and_complete(self.world, self.known))  # "reach"
            self.check_encounters()
            return
        if (tx, ty) in room.blocked() or self.occupied(tx, ty):
            return
        old = (p.x, p.y)
        p.x, p.y = tx, ty
        self._advance_trail(old)
        self._place_followers()
        self.try_pickup()

    def _record_arrival(self):
        """Walking into a room is something your companions *did*, not something the
        people already standing there did — so only the party remembers it, and only
        the first time, or memory fills up with forty identical arrivals."""
        room = self.rooms[self.world.player.room]
        first_time = room.id not in (self.world.flags.get("visited") or [])
        mark_visited(self.world, room.id)
        if first_time:
            self.progress("room")
        desc = f" {room.desc}" if room.desc else ""
        record_experience(
            self.world, "arrive", f"You entered {room.name}.",
            room=room.id, public=False, salience=NOTE,
            first_person=f"You went with the player into {room.name}.{desc}",
            targets=list(self.world.party),
            once_key=f"room:{room.id}",
        )
        self.beat("arrive", f"you walked into {room.name}", once_key=f"room:{room.id}")

    # --- party movement ---------------------------------------------------
    def _reset_trail(self):
        self._trail = []

    def _advance_trail(self, old_tile):
        self._trail.insert(0, old_tile)
        del self._trail[len(self.world.party) + 2:]

    def _walkable(self, room_id, tile) -> bool:
        from engine.world import GRID_H, GRID_W
        tx, ty = tile
        # keep companions off the border/doorway ring
        if tx <= 0 or ty <= 0 or tx >= GRID_W - 1 or ty >= GRID_H - 1:
            return False
        if tile in self.rooms[room_id].blocked():
            return False
        for n in self.world.npcs.values():
            if n.npc_id in self.world.party:
                continue
            if n.room == room_id and (n.x, n.y) == tile:
                return False
        return True

    def _nearest_free(self, room_id, cx, cy, taken):
        from engine.world import GRID_H, GRID_W
        for r in range(1, max(GRID_W, GRID_H)):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    t = (cx + dx, cy + dy)
                    if (0 <= t[0] < GRID_W and 0 <= t[1] < GRID_H
                            and t not in taken and self._walkable(room_id, t)):
                        return t
        return None

    def _place_followers(self):
        """Trail the party behind the player within the current room.

        Not at camp. The waystation is where the night is spent and where companions are
        meant to be talked *to*, so they settle around the fire and stay put — a
        companion glued to your shoulder is scenery, not somebody you go and sit with.
        """
        p = self.world.player
        if p.room == CAMP_ROOM:
            return
        taken = {(p.x, p.y)}
        for i, nid in enumerate(self.world.party):
            npc = self.world.npcs.get(nid)
            if npc is None:
                continue
            npc.room = p.room
            pref = self._trail[i] if i < len(self._trail) else None
            if pref and pref not in taken and self._walkable(p.room, pref):
                npc.x, npc.y = pref
            else:
                spot = self._nearest_free(p.room, p.x, p.y, taken)
                if spot:
                    npc.x, npc.y = spot
            taken.add((npc.x, npc.y))

    def _gather_party(self):
        """Snap every party member into free tiles around the player (on room
        change, recruit, or load) and reset the follow trail."""
        p = self.world.player
        taken = {(p.x, p.y)}
        for nid in self.world.party:
            npc = self.world.npcs.get(nid)
            if npc is None:
                continue
            npc.room = p.room
            spot = self._nearest_free(p.room, p.x, p.y, taken)
            if spot:
                npc.x, npc.y = spot
                taken.add(spot)
        self._reset_trail()

    # --- ambient NPC movement ---------------------------------------------
    def _still_time(self):
        return random.uniform(STILL_MIN, STILL_MAX)

    def _ambient_state(self, nid):
        """Per-NPC {mode, timer}. Everyone starts standing still."""
        st = self._ambient.get(nid)
        if st is None:
            st = {"mode": "still", "timer": self._still_time()}
            self._ambient[nid] = st
        return st

    def _ambient_step(self, dt):
        """Idle NPCs alternate between standing still and wandering: after each step
        they choose to keep going or settle. Party members, vendors and the ridge are
        left alone."""
        pr = self.world.player.room
        ppos = (self.world.player.x, self.world.player.y)
        for nid, npc in self.world.npcs.items():
            if nid in self.world.party or is_vendor(nid):
                continue
            st = self._ambient_state(nid)
            st["timer"] -= dt
            if st["timer"] > 0:
                continue
            if st["mode"] == "still":
                # Done standing about — set off wandering, starting with a step.
                st["mode"] = "wander"
            self._npc_wander_step(nid, npc, pr, ppos)
            # After moving, decide: another step, or stand still a while?
            if random.random() < P_KEEP_WANDERING:
                st["timer"] = STEP_DELAY
            else:
                st["mode"] = "still"
                st["timer"] = self._still_time()

    def _home_dist(self, home: str) -> dict:
        """Doors between `home` and everywhere else, computed once and kept."""
        cache = getattr(self, "_dist_cache", None)
        if cache is None:
            cache = self._dist_cache = {}
        if home not in cache:
            import collections
            seen, dq = {home: 0}, collections.deque([home])
            while dq:
                r = dq.popleft()
                for d in self.rooms[r].doors:
                    if d.to_room not in seen:
                        seen[d.to_room] = seen[r] + 1
                        dq.append(d.to_room)
            cache[home] = seen
        return cache[home]

    def _roam(self, nid: str) -> int:
        """How far from home this character will drift, in doors.

        Measured over 45-minute runs, an unleashed Tilda ended up an average of 3.1
        doors from the outfarm — the one character whose entire situation is that she
        cannot leave it. A character's premise should survive the idle animation.
        """
        try:
            from npc.roster import load_character
            return max(0, int(load_character(nid).get("roam", ROAM_DEFAULT)))
        except (KeyError, TypeError, ValueError):
            return ROAM_DEFAULT

    def _npc_wander_step(self, nid, npc, player_room, ppos):
        """One wander step: usually a tile within the room, sometimes through a door
        (biased back toward home when away)."""
        home = NPC_SPAWNS.get(nid, (npc.room,))[0]
        room = self.rooms[npc.room]
        if random.random() < P_HOP_ROOM:
            if npc.room != home and random.random() < P_RETURN_HOME:
                back = next((d for d in room.doors if d.to_room == home), None)
                if back:
                    self._npc_through_door(npc, back, player_room, ppos)
                    return
            dist = self._home_dist(home)
            here = dist.get(npc.room, 0)
            limit = self._roam(nid)
            doors = [d for d in room.doors
                     if d.passable(self.world)
                     and d.to_room not in AMBIENT_BLOCKED_ROOMS
                     # you don't wander into someone's house or a sealed store
                     and (d.to_room not in PRIVATE_ROOMS or d.to_room == home)
                     # and you stay within your own patch — or head back toward it
                     and (dist.get(d.to_room, 99) <= limit
                          or dist.get(d.to_room, 99) < here)]
            if doors:
                self._npc_through_door(npc, random.choice(doors), player_room, ppos)
                return
        self._npc_pace(npc, player_room, ppos)

    def _npc_pace(self, npc, player_room, ppos):
        dirs = [(0, -1), (0, 1), (-1, 0), (1, 0)]
        random.shuffle(dirs)
        for dx, dy in dirs:
            t = (npc.x + dx, npc.y + dy)
            if npc.room == player_room and t == ppos:
                continue
            if self._walkable(npc.room, t):
                npc.x, npc.y = t
                return

    def _npc_through_door(self, npc, door, player_room, ppos):
        npc.room = door.to_room
        taken = {ppos} if door.to_room == player_room else set()
        spot = self._nearest_free(door.to_room, door.spawn[0], door.spawn[1], taken)
        npc.x, npc.y = spot if spot else door.spawn

    def try_pickup(self):
        p = self.world.player
        g = self.world.ground_item_at(p.room, p.x, p.y)
        if g:
            self.world.ground_items.remove(g)
            p.inventory.append(g.item)
            label = display_name(g.item)
            self.set_toast(f"Picked up: {label}.")
            # Only a pickup somebody present actually cares about is worth a memory —
            # otherwise a loaf of bread crowds real conversations out of the 12-entry
            # prompt window. A bonded object (Ansel's staff, to Wren) also gets pinned.
            cared = any(bond_for(nid, "item", g.item)
                        for nid in witnesses(self.world, p.room))
            record_experience(
                self.world, "item_get", f"You picked up the {label}.",
                room=p.room, public=False, salience=BEAT if cared else AMBIENT,
                first_person=f"You watched the player pick up the {label}, "
                             f"right here in {self.rooms[p.room].name}.",
                bond_items=(g.item,),
            )
            self.beat("pickup", f"the player picked up the {label}",
                      once_key=f"item:{g.item}", items=(g.item,))

    def drop_item(self, item: str):
        """Drop one of an item onto a free tile near the player."""
        p = self.world.player
        if item not in p.inventory:
            return
        room = self.rooms[p.room]
        blocked = room.blocked()
        spot = None
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0), (0, 0)):
            tx, ty = p.x + dx, p.y + dy
            if (tx, ty) in blocked or self.occupied(tx, ty):
                continue
            if self.world.ground_item_at(p.room, tx, ty):
                continue
            spot = (tx, ty)
            break
        if spot is None:
            self.set_toast("No room to drop that here.")
            return
        p.inventory.remove(item)
        self.world.ground_items.append(GroundItem(p.room, spot[0], spot[1], item))
        self.set_toast(f"Dropped: {display_name(item)}.")

    # --- ridge / combat ---------------------------------------------------
    def _ridge_open(self) -> bool:
        w = self.world
        if w.flags.get("gloam_resolved"):
            return True
        return bool(w.flags.get("map_read")) and w.lit_lamp_count() == len(w.lamps)

    def _ridge_locked_msg(self) -> str:
        w = self.world
        need = []
        if w.lit_lamp_count() != len(w.lamps):
            need.append("the lamps lit behind you")
        if not w.flags.get("map_read"):
            need.append("to know the way (read Ansel's ridge map)")
        return "The dark swallows the path. You need " + " and ".join(need) + "."

    def _pledged_allies(self, exclude=None):
        """Party members auto-join every fight at your side."""
        return [combatant_from_npc(self.world.npcs[nid], "ally")
                for nid in self.world.party
                if nid != exclude and nid in self.world.npcs]

    def begin_combat(self, enemies, allies, context):
        w = self.world
        w.player.hp = max(1, w.player.hp)
        combat = make_combat(w.player.hp, w.player.max_hp, enemies, allies)
        self.combat_scene = CombatScene(w, combat)
        self.combat_context = context
        self.scene = "combat"

    def start_npc_combat(self, npc_id):
        npc = self.world.npcs[npc_id]
        enemy = combatant_from_npc(npc, "enemy")
        self.begin_combat([enemy], self._pledged_allies(exclude=npc_id),
                          {"type": "npc", "npc_id": npc_id})

    def check_encounters(self):
        """Ridge encounters: creatures on the way up, the Gloam at the summit."""
        w = self.world
        room = w.player.room
        if w.flags.get("gloam_resolved"):
            return
        if room == "ridge_summit":
            self.begin_combat(enemies_from_ids(["gloam"]), self._pledged_allies(),
                              {"type": "gloam"})
        elif room in ("ridge_foot", "ridge_pass"):
            flag = f"{room}_cleared"
            if not w.flags.get(flag):
                ids = ["gloamling"] if room == "ridge_foot" else ["gloamling", "gloamling"]
                self.set_toast("Something moves in the snow...")
                self.begin_combat(enemies_from_ids(ids), self._pledged_allies(),
                                  {"type": "creature", "room": room})

    def on_combat_end(self, outcome):
        w = self.world
        ctx = self.combat_context or {"type": "gloam"}
        self.combat_scene = None
        self.combat_context = None
        self.scene = "overworld"

        if outcome == "lost":
            w.player.room, w.player.x, w.player.y = "square", 9, 8
            w.player.hp = max(1, w.player.max_hp // 2)
            lost = 0
            while lost < 3 and CURRENCY in w.player.inventory:
                w.player.inventory.remove(CURRENCY)
                lost += 1
            if ctx["type"] == "npc":
                w.npcs[ctx["npc_id"]].flags["hostile"] = False
            record_experience(
                w, "knockout", "You were beaten down, and woke later in the square.",
                room=w.player.room, public=False, salience=BEAT,
                first_person="You were there when the player was beaten down. You got "
                             "them back to the square.",
                targets=list(w.party))
            self.set_toast("You wake in the square, aching."
                           + (f" {lost} coins slipped away." if lost else ""))
            return
        if outcome == "fled":
            if ctx["type"] in ("creature", "gloam"):
                w.player.room, w.player.x, w.player.y = "camp", 9, 6
                self._gather_party()
                self.set_toast("You scramble back down the ridge.")
            else:
                if ctx["type"] == "npc":
                    w.npcs[ctx["npc_id"]].flags["hostile"] = False
                self.set_toast("You break off the fight and get clear.")
            return

        # won or spared
        if ctx["type"] == "gloam":
            w.flags["gloam_resolved"] = True
            w.hearthlight = 100
            verb = ("You lay the Gloam to rest." if outcome == "won"
                    else "You reach the Gloam, and it yields.")
            seen = ("You stood on the summit with the player and watched them put the "
                    "Gloam down." if outcome == "won" else
                    "You stood on the summit with the player and watched the Gloam yield "
                    "to them. It was not killed. It simply stopped pulling.")
            record_experience(w, "gloam",
                              f"{verb} The Hearthlight steadies and the dusk lifts.",
                              room=w.player.room, salience=MAJOR,
                              first_person=seen, targets=list(w.party))
            self.set_toast("The dusk lifts. Emberhold will hold.")
            self.epilogue = Epilogue(w, outcome)      # arcs land, then free play
            self.scene = "epilogue"
        elif ctx["type"] == "creature":
            w.flags[f"{ctx.get('room', 'ridge')}_cleared"] = True
            verb = "drive off" if outcome == "won" else "quiet"
            record_experience(w, "fight", f"You {verb} the gloamlings on the ridge.",
                              room=w.player.room, salience=BEAT,
                              first_person=f"You fought alongside the player on the ridge "
                                           f"and helped {verb} the gloamlings.",
                              targets=list(w.party))
            self.set_toast("The snow settles. The way is clear.")
        else:
            npc_id = ctx["npc_id"]
            npc = w.npcs[npc_id]
            npc.flags["hostile"] = False
            name = character_name(npc_id)
            if outcome == "spared":
                npc.flags["reconciled"] = True
                npc.affinity = max(npc.affinity, -5)
                self.memory_for(npc_id).remember(
                    "You attacked the player, but they spared you instead of striking back.")
                w.events.record("fight", f"{name} stood down. An uneasy peace holds.")
                self.set_toast(f"{name} stands down.")
            else:
                npc.flags["subdued"] = True
                self.memory_for(npc_id).remember(
                    "You attacked the player and were beaten. You owe them your life.")
                w.events.record("fight", f"You bested {name} in the fight they started.")
                self.set_toast(f"You best {name}.")

    def interact(self):
        kind, ident = self.adjacent_targets()
        if kind == "npc":
            self.open_dialogue(ident)
        elif kind == "use":
            self.use_interactable(ident)
        elif kind == "locked":
            self.set_toast(ident)

    def use_interactable(self, inter):
        """Every lamp, fire, chest and puzzle goes through here (engine/interact.py)."""
        room = self.rooms[self.world.player.room]
        # Taken before the interaction so the rest's own events aren't reported back to
        # the player as news about the day they just had.
        pre_seq = self.world.events._seq
        result = apply_interaction(self.world, inter, room.name)
        if result.message:
            self.set_toast(result.message)
        if not result.ok:
            return
        # Whoever is standing here saw it happen (engine/witness.py).
        events = result.events or (
            [("interact", f"You used the {inter.label}.")] if inter.witness_msg else [])
        for i, (kind, text) in enumerate(events):
            record_experience(self.world, kind, text, room=room.id, salience=NOTE,
                              first_person=inter.witness_msg if i == 0 else None)
        self.beat("use", f"the player used the {inter.label} in {room.name}",
                  once_key=f"used:{inter.id}")
        if result.panel == "storage":
            self.storage_panel = StoragePanel(self.world)
            self.storage_open = True
        if any(k == "rest" for k, _ in events):
            self.progress("rest")
            self.begin_night(pre_seq)
        if result.quests_dirty:
            self.on_quests_completed(refresh_and_complete(self.world, self.known))

    def begin_night(self, upto_seq: int):
        """The world's turn. Resting is the only cut this game has — see ui/night.py."""
        facts = night_facts(self.world, upto_seq)
        mark_rested(self.world, upto_seq)
        self.night = NightScene(self.world, facts)
        self.scene = "night"

    # --- making camp -------------------------------------------------------
    def camp_action(self):
        """R. Travel to the waystation, or go back to where you left off.

        The camp is where every night of the game is spent, and a camp you have to hike
        back to is a camp nobody uses — if resting costs a round trip from the ridge,
        players stop resting and the world stops taking its turn.
        """
        if self.world.player.room == CAMP_ROOM:
            back = self.world.flags.get("camp_return")
            if not back:
                self.set_toast("You are already at the waystation.")
                return
            where = self.rooms[back["room"]].name if back["room"] in self.rooms else "where you were"
            self.camp_prompt = f"Break camp and head back to {where}?"
        else:
            self.camp_prompt = "Make camp? You will travel to the Waystation."

    def confirm_camp(self):
        """Act on a pending R. Travelling out and back is the same move both ways."""
        self.camp_prompt = None
        w = self.world
        if w.player.room == CAMP_ROOM:
            back = w.flags.pop("camp_return", None)
            if not back:
                return
            self._travel_to(back["room"], back["x"], back["y"])
            self.set_toast("You break camp.")
        else:
            w.flags["camp_return"] = {"room": w.player.room,
                                      "x": w.player.x, "y": w.player.y}
            self._travel_to(CAMP_ROOM, *CAMP_SPOT)
            self.set_toast("You make camp at the waystation.")

    def _travel_to(self, room_id: str, x: int, y: int):
        """Put the player (and the party) somewhere without walking there. Everything a
        door does except the door — so arrivals still register and 'reach' still fires."""
        p = self.world.player
        p.room, p.x, p.y = room_id, x, y
        self._trail.clear()
        self._gather_party()
        self._record_arrival()
        self.try_pickup()
        self.on_quests_completed(refresh_and_complete(self.world, self.known))

    def open_dialogue(self, npc_id):
        # No scripted onboarding. The only authored quest is "find Wren"; the lamps
        # come from Wren's own agenda, and the oil from her offering it, from Sella's
        # stock, or from the cellar cache.
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
            elif self.inventory_open:
                cmd = self.inv_panel.handle_event(event)
                if cmd:
                    self.handle_inventory_command(cmd)
            elif self.party_open:
                cmd = self.party_panel.handle_event(event)
                if cmd:
                    self.handle_party_command(cmd)
            elif self.storage_open:
                cmd = self.storage_panel.handle_event(event)
                if cmd and cmd.get("cmd") == "close":
                    self.storage_open = False
            elif self.scene == "intro":
                if event.type == pygame.KEYDOWN:
                    self.scene = "overworld"
                    if self.loaded_save:
                        self.set_toast("Progress restored.")
            elif self.scene == "dialogue":
                if self.dialogue:
                    self.dialogue.handle_event(event)
            elif self.scene == "epilogue":
                if self.epilogue:
                    self.epilogue.handle_event(event)
            elif self.scene == "combat":
                if self.combat_scene:
                    if self.combat_scene.phase == "ended":
                        if event.type == pygame.KEYDOWN and event.key in INTERACT_KEYS:
                            self.on_combat_end(self.combat_scene.outcome)
                    else:
                        self.combat_scene.handle_event(event)
            elif self.scene == "night":
                if self.night:
                    self.night.handle_event(event)
            elif self.camp_prompt is not None and event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_y, pygame.K_r) or event.key in INTERACT_KEYS:
                    self.confirm_camp()
                elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                    self.camp_prompt = None
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.map_open:
                        self.map_open = False
                    elif self.journal_open:
                        self.journal_open = False
                    else:
                        self.menu.open()
                        self.menu_open = True
                elif event.key == pygame.K_m:
                    self.map_open = not self.map_open
                elif event.key == pygame.K_j:
                    self.journal_open = not self.journal_open
                elif event.key == pygame.K_i and not (self.journal_open or self.map_open):
                    self.inv_panel = InventoryPanel(self.world)
                    self.inventory_open = True
                elif event.key == pygame.K_p and not (self.journal_open or self.map_open):
                    self.party_panel = PartyPanel(self.world)
                    self.party_open = True
                elif event.key == pygame.K_r and not (self.journal_open or self.map_open):
                    self.camp_action()
                elif not (self.journal_open or self.map_open) and event.key in INTERACT_KEYS:
                    self.interact()

    def update(self, dt):
        if self.toast_timer > 0:
            self.toast_timer -= dt
        self._update_bark(dt)

        if self.menu_open:
            self.menu.update(dt)
            return
        if self.inventory_open or self.party_open or self.storage_open:
            return

        if self.scene == "overworld" and not (self.journal_open or self.map_open
                                              or self.camp_prompt):
            self.move_timer -= dt
            if self.move_timer <= 0:
                keys = pygame.key.get_pressed()
                for key, (dx, dy) in DIRS.items():
                    if keys[key]:
                        self.try_move(dx, dy)
                        self.move_timer = MOVE_DELAY
                        break
            self._ambient_step(dt)
        elif self.scene == "dialogue" and self.dialogue:
            self.dialogue.update(dt)
            if self.dialogue.finished:
                combat_req = self.dialogue.combat_request
                self.dialogue = None
                self.scene = "overworld"
                refresh_and_complete(self.world, self.known)
                self._gather_party()      # snap a new companion in / close ranks
                if combat_req:
                    self.start_npc_combat(combat_req)
        elif self.scene == "combat" and self.combat_scene:
            self.combat_scene.update(dt)
        elif self.scene == "epilogue" and self.epilogue and self.epilogue.finished:
            self.epilogue = None
            self.scene = "overworld"       # a lit Emberhold, still yours to walk
        elif self.scene == "night" and self.night and self.night.finished:
            self.night = None
            self.scene = "overworld"       # morning, at the fire you slept by

    # --- draw -------------------------------------------------------------
    def draw(self):
        if self.scene == "combat" and self.combat_scene:
            self.combat_scene.draw(self.screen)
            if self.toast_timer > 0:
                self.draw_toast()
            return

        self.screen.fill(T.BG)
        draw_overworld(self.screen, self.world, self.rooms)
        hint = self.interaction_hint() if self.scene == "overworld" else ""
        draw_hud(self.screen, self.world, self.rooms, hint)

        if self.scene == "intro":
            self.draw_intro()
        elif self.scene == "epilogue" and self.epilogue:
            self.epilogue.draw(self.screen)
        elif self.scene == "night" and self.night:
            self.night.draw(self.screen)
        elif self.scene == "dialogue" and self.dialogue:
            self.dialogue.draw(self.screen)

        if self.scene == "overworld" and not self.map_open:
            draw_minimap(self.screen, self.world, self.rooms)
        if self.map_open:
            draw_full_map(self.screen, self.world, self.rooms)
        if self.journal_open:
            draw_journal(self.screen, self.world)
        if self.inventory_open and self.inv_panel:
            self.inv_panel.draw(self.screen)
        if self.party_open and self.party_panel:
            self.party_panel.draw(self.screen)
        if self.storage_open and self.storage_panel:
            self.storage_panel.draw(self.screen)
        if self.menu_open:
            self.menu.draw(self.screen, self.save_name)

        if self.camp_prompt and self.scene == "overworld":
            self.draw_camp_prompt()
        if self._bark and self.scene == "overworld":
            self.draw_bark()
        if self.toast_timer > 0:
            self.draw_toast()

    def draw_camp_prompt(self):
        lines = [self.camp_prompt, "Enter / R — yes      Esc — no"]
        font, small = T.font(18, bold=True), T.font(14)
        w = max(font.size(lines[0])[0], small.size(lines[1])[0]) + 40
        box = pygame.Rect(0, 0, w, 78)
        box.center = (T.SCREEN_W // 2, T.SCREEN_H // 2)
        s = pygame.Surface(box.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 225))
        self.screen.blit(s, box.topleft)
        pygame.draw.rect(self.screen, T.HEARTH, box, 2)
        draw_text(self.screen, lines[0], (box.centerx, box.y + 18), font, T.TEXT,
                  center=True)
        draw_text(self.screen, lines[1], (box.centerx, box.y + 46), small, T.TEXT_DIM,
                  center=True)

    def draw_toast(self):
        img = T.font(17, bold=True).render(self.toast, True, T.TEXT)
        rect = img.get_rect(center=(T.SCREEN_W // 2, 24))
        bg = rect.inflate(20, 12)
        s = pygame.Surface(bg.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 190))
        self.screen.blit(s, bg.topleft)
        self.screen.blit(img, rect)

    def draw_bark(self):
        """A companion's unprompted remark, in their own colour, above the HUD."""
        from ui.render import wrap_text
        name = character_name(self._bark["npc"])
        fnt = T.font(17)
        lines = wrap_text(f"{name}: “{self._bark['text']}”", fnt, T.SCREEN_W - 80)[:3]
        h = 14 + 22 * len(lines)
        box = pygame.Rect(24, T.PLAY_H - h - 16, T.SCREEN_W - 48, h)
        s = pygame.Surface(box.size, pygame.SRCALPHA)
        s.fill((*T.BOX_BG, 225))
        self.screen.blit(s, box.topleft)
        pygame.draw.rect(self.screen, T.npc_color(self._bark["npc"]), box, 1,
                         border_radius=5)
        y = box.top + 8
        for ln in lines:
            draw_text(self.screen, ln, (box.left + 12, y), fnt, T.TEXT)
            y += 22

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
            "Move: WASD  Interact: E  Items: I  Party: P  Map: M  Journal: J  Menu: Esc",
            "",
            "Press any key to begin.",
        ]
        y = 190
        for ln in lines:
            draw_text(self.screen, ln, (cx, y), T.font(18), T.TEXT, center=True)
            y += 30

        if self.llm_problem:
            # No endpoint configured: the world works, but nobody can speak. Say so
            # here rather than letting a first-time player discover it at Wren's feet.
            from ui.render import wrap_text
            warn = pygame.Rect(60, T.SCREEN_H - 78, T.SCREEN_W - 120, 70)
            s = pygame.Surface(warn.size, pygame.SRCALPHA)
            s.fill((60, 20, 20, 220))
            self.screen.blit(s, warn.topleft)
            pygame.draw.rect(self.screen, (180, 90, 90), warn, 1, border_radius=4)
            draw_text(self.screen, "No LLM endpoint configured — nobody will talk yet",
                      (cx, warn.top + 8), T.font(15, bold=True), (255, 190, 190),
                      center=True)
            wy = warn.top + 30
            for ln in wrap_text(self.llm_problem, T.font(13), warn.width - 24)[:2]:
                draw_text(self.screen, ln, (cx, wy), T.font(13), T.TEXT_DIM, center=True)
                wy += 18


def main():
    fresh = "--fresh" in sys.argv
    if "--log-llm" in sys.argv:
        where = llm_log.enable()
        print(f"Logging every LLM call to {where}")
    Game(fresh=fresh).run()


if __name__ == "__main__":
    main()
