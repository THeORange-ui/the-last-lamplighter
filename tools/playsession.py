"""A headless session that actually PLAYS the game, for checking pacing.

Run:
    .venv/bin/python tools/playsession.py                # 26 turns, seed 7
    .venv/bin/python tools/playsession.py --turns 40 --seed 3 --talk 2

**Why this exists.** Anything about pacing has to be judged from real play. Testing the
night by resting in a loop with neglect flags forced high produced a cadence nothing like
a session's, and pronounced it healthy while one character was quietly dominating the
real game. This walks the door graph, holds real conversations, follows the same
waypoints the player follows, picks things up, and rests only when the plate is clear —
then reports the numbers that actually matter: **quests per giver, agenda beats closed
per character, and how many nights had anything in them.**

It makes real LLM calls (roughly one per conversation, plus two or three per night), so a
default run is a few dozen. Pair it with `--log-llm` reading if you want the prompts.

Safe to run against a live install: saves and runtime memory are redirected to a
temporary directory, so your own slots are never read, written or wiped.
"""
from __future__ import annotations

import argparse
import collections
import os
import pathlib
import random
import sys
import tempfile
import time

# Headless by default, so the tool needs no env vars and opens no window.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Redirect all persistence BEFORE importing anything that touches it. Without this the
# harness would run against — and `fresh=True` would delete — the player's own saves.
_SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="lamplighter-playsession-"))
import engine.save as _save                                   # noqa: E402
import npc.memory as _memory                                  # noqa: E402
_save.SAVE_DIR = _SANDBOX / "save"
_memory.MEMORY_DIR = _SANDBOX / "runtime_memory"
_save.SAVE_DIR.mkdir(parents=True, exist_ok=True)
_memory.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

import pygame                                                 # noqa: E402
import main                                                   # noqa: E402
from engine import cartography, initiative, pacing            # noqa: E402
from engine.quests import refresh_and_complete                # noqa: E402
from npc.agent import APPROACH, npc_respond                   # noqa: E402

FOLLOWUPS = [
    "Tell me more about that.",
    "What do you need from me?",
    "I'll see what I can do.",
    "Anything else on your mind?",
    "Who else should I be speaking to?",
]
REVISIT_SKIP = 0.4        # chance of walking past someone you've already spoken to


class Session:
    def __init__(self, seed: int, talk_lines: int):
        self.rng = random.Random(seed)
        self.talk_lines = talk_lines
        pygame.init()
        self.game = main.Game(fresh=True)      # safe: persistence is sandboxed above
        self.game.scene = "overworld"
        self.world = self.game.world
        self.rooms = self.game.rooms
        self.spoken: collections.Counter = collections.Counter()
        self.nights: list[dict] = []

    # --- getting about ----------------------------------------------------
    def _path(self, start: str, goal: str) -> list[str]:
        """BFS over the door graph, avoiding the ridge until it is actually open."""
        if start == goal:
            return []
        shut = set() if initiative.ridge_open(self.world) else {
            r for r, room in self.rooms.items() if room.biome == "snow"}
        seen, queue = {start}, collections.deque([(start, [])])
        while queue:
            cur, route = queue.popleft()
            for door in self.rooms[cur].doors:
                nxt = door.to_room
                if nxt not in self.rooms or nxt in seen:
                    continue
                if nxt in shut and nxt != goal:
                    continue
                seen.add(nxt)
                if nxt == goal:
                    return route + [nxt]
                queue.append((nxt, route + [nxt]))
        return []

    def walk_to(self, goal: str) -> None:
        """Room by room through real doors, so arrivals and `reach` objectives fire."""
        for step in self._path(self.world.player.room, goal):
            door = next((d for d in self.rooms[self.world.player.room].doors
                         if d.to_room == step), None)
            self.game._travel_to(step, *(door.spawn if door else (9, 6)))

    # --- doing things -----------------------------------------------------
    def talk(self, npc_id: str) -> None:
        memory = self.game.memory_for(npc_id)
        npc_respond(self.world, self.rooms, self.game.known, npc_id, APPROACH, memory)
        for _ in range(self.talk_lines):
            npc_respond(self.world, self.rooms, self.game.known, npc_id,
                        self.rng.choice(FOLLOWUPS), memory)
        self.spoken[npc_id] += 1
        self.game.on_quests_completed(refresh_and_complete(self.world, self.game.known))

    def people_here(self) -> list[str]:
        return [nid for nid, n in self.world.npcs.items()
                if n.room == self.world.player.room and nid != "gloam"
                and not self.world.in_party(nid)]

    def somewhere_to_go(self) -> str:
        """A waypoint if the player has one, else somewhere they haven't been."""
        marks = [r for r in cartography.waypoints(self.world, self.rooms)
                 if r in self.rooms and r != self.world.player.room]
        if marks:
            return self.rng.choice(marks)
        seen = set(self.world.flags.get("visited") or [])
        fresh = [r for r, room in self.rooms.items()
                 if r not in seen and (initiative.ridge_open(self.world)
                                       or room.biome != "snow")]
        return self.rng.choice(fresh or list(self.rooms))

    def rest(self) -> None:
        before = {q.id for q in self.world.quests}
        day = self.world.day
        self.walk_to("camp")
        self.world.player.x, self.world.player.y = 9, 8
        fire = next(i for i in self.rooms["camp"].interactables if i.kind == "campfire")
        self.game.use_interactable(fire)
        for _ in range(1500):                  # the night runs on a worker thread
            if self.game.night.ready:
                break
            time.sleep(0.05)
        reports = list(self.game.night.reports)
        narration = self.game.night.line
        self.game.night.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
        self.game.update(0.016)
        new = [q for q in self.world.quests if q.id not in before]
        self.nights.append({"day": day, "reports": reports, "new": new})
        print(f"\n=== night of day {day} ===")
        print(f"    {narration[:150]}")
        for line in reports:
            print("   ", line)
        print("    new threads:",
              [(q.title, q.objective.type) for q in new] or "none")
        print("    holding:", [q.title for q in self.world.active_quests()])

    def needs_rest(self, turn: int) -> bool:
        """Rest when the day's work is done — not on a timer."""
        active = self.world.active_quests()
        return (not active
                or all(q.objective.type == "check_back" for q in active)
                or turn % 7 == 6)

    # --- the loop ---------------------------------------------------------
    def run(self, turns: int) -> None:
        for turn in range(turns):
            self.walk_to(self.somewhere_to_go())
            for npc_id in self.people_here():
                if self.spoken[npc_id] and self.rng.random() < REVISIT_SKIP:
                    continue
                self.talk(npc_id)
            self.game.try_pickup()
            if self.needs_rest(turn):
                self.rest()
        self.report()

    def report(self) -> None:
        w = self.world
        print("\n" + "=" * 72)
        print("SESSION SUMMARY")
        print(f"  days {w.day} · tick {pacing.tick(w)} · "
              f"{len(w.flags.get('visited') or [])}/{len(self.rooms)} rooms seen")
        print(f"  {sum(self.spoken.values())} conversations across "
              f"{len(self.spoken)} characters: {dict(self.spoken)}")

        print("\n  quests by giver (watch for one character running away with it):")
        givers = collections.Counter(q.giver for q in w.quests if q.giver)
        for who, n in givers.most_common():
            print(f"     {who:<9} {'#' * n} {n}")

        print("\n  agenda beats closed (should not be lopsided either):")
        for nid, npc in w.npcs.items():
            if npc.agenda:
                done = sum(1 for a in npc.agenda if a.get("status") == "done")
                print(f"     {nid:<9} {'#' * done}{'' if done else '-'} {done}")

        acted = {nid: n.flags.get("nights_acted", 0)
                 for nid, n in w.npcs.items() if n.flags.get("nights_acted")}
        with_content = sum(1 for n in self.nights if n["reports"])
        print(f"\n  nights: {len(self.nights)}, of which {with_content} held something")
        print(f"  acted at night: {acted or 'nobody'}")

        print("\n  every quest, in order:")
        for q in w.quests:
            print(f"     [{q.status:<8}] {q.title[:34]:<36} {q.giver:<8} "
                  f"{q.objective.type:<10} {str(q.objective.target)[:40]}")
        print(f"\n  sandbox (saves + memory, safe to delete): {_SANDBOX}")


def main_cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--turns", type=int, default=26, help="play turns (default 26)")
    ap.add_argument("--seed", type=int, default=7, help="RNG seed (default 7)")
    ap.add_argument("--talk", type=int, default=1,
                    help="follow-up lines per conversation (default 1)")
    args = ap.parse_args()
    Session(args.seed, args.talk).run(args.turns)


if __name__ == "__main__":
    main_cli()
