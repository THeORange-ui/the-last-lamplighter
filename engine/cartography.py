"""Where things are, and where the game wants you to go.

Rooms don't carry map coordinates — the map is a set of rooms joined by doors, and a
door's position on the wall is the only thing that says which way it leads. So the
layout is *derived*: walk the graph from the town square, and each door pushes its
target one cell in the direction that door sits on (a door on the right wall means
the room beyond is east). Add a room and the map draws itself.

`waypoints()` answers the other half — for each active quest, which room is it asking
the player to be in? That turns an objective into a marker, which is the difference
between a quest log and actually knowing where to go.
"""
from __future__ import annotations

from engine.world import GRID_H, GRID_W

START = "square"


def _direction(room, door) -> tuple[int, int]:
    """Which way this door leads, from where it sits on the room's wall."""
    if door.x <= 0:
        return (-1, 0)
    if door.x >= GRID_W - 1:
        return (1, 0)
    if door.y <= 0:
        return (0, -1)
    if door.y >= GRID_H - 1:
        return (0, 1)
    return (0, 0)


def layout(rooms) -> dict[str, tuple[int, int]]:
    """room_id -> (col, row) on the map grid, laid out from the square outward.

    Breadth-first so the shortest route from town decides a room's place; a cell
    already taken is nudged outward rather than overwritten, since a hand-built map
    won't always be geometrically consistent.
    """
    pos: dict[str, tuple[int, int]] = {START: (0, 0)}
    taken = {(0, 0): START}
    queue = [START]
    while queue:
        rid = queue.pop(0)
        cx, cy = pos[rid]
        for door in rooms[rid].doors:
            if door.to_room in pos or door.to_room not in rooms:
                continue
            dx, dy = _direction(rooms[rid], door)
            spot = (cx + dx, cy + dy)
            for _ in range(6):                 # nudge along if that cell is spoken for
                if spot not in taken:
                    break
                spot = (spot[0] + dx, spot[1] + dy) if (dx or dy) else (spot[0] + 1, spot[1])
            pos[door.to_room] = spot
            taken[spot] = door.to_room
            queue.append(door.to_room)
    # Anything unreachable from the square still deserves a place on the map.
    spare_y = max((p[1] for p in pos.values()), default=0) + 2
    for i, rid in enumerate(r for r in rooms if r not in pos):
        pos[rid] = (i, spare_y)
    return pos


def links(rooms) -> list[tuple[str, str]]:
    """Unique room-to-room connections, for drawing the lines between them."""
    seen: set[tuple[str, str]] = set()
    for rid, room in rooms.items():
        for door in room.doors:
            if door.to_room in rooms:
                seen.add(tuple(sorted((rid, door.to_room))))
    return sorted(seen)


def quest_target_room(state, rooms, quest) -> str | None:
    """The room this objective wants the player in, if that can be pinned down."""
    o = quest.objective
    if o.type == "reach":
        return o.target if o.target in rooms else None
    if o.type in ("talk_to", "check_back"):
        npc = state.npcs.get(o.target if o.type == "talk_to" else quest.giver)
        return npc.room if npc else None
    if o.type == "deliver":
        npc = state.npcs.get(o.npc)
        return npc.room if npc else None
    if o.type == "judged":
        npc = state.npcs.get(quest.giver)
        return npc.room if npc else None
    if o.type == "fetch":
        # Wherever it's lying, if it's lying anywhere the player has a route to.
        g = next((g for g in state.ground_items if g.item == o.target), None)
        return g.room if g else None
    if o.type == "interact":
        # The nearest unfinished one of that kind — lamps you haven't lit yet.
        from engine.interact import is_live
        for rid, room in rooms.items():
            for i in room.interactables:
                if i.kind == o.target and not i.hidden and is_live(state, i):
                    return rid
    return None


def waypoints(state, rooms) -> dict[str, list[str]]:
    """room_id -> titles of the active quests pointing there."""
    out: dict[str, list[str]] = {}
    for q in state.active_quests():
        room = quest_target_room(state, rooms, q)
        if room:
            out.setdefault(room, []).append(q.title)
    return out


def visited(state) -> set[str]:
    """Rooms the player has actually been in — the map fills in as you explore."""
    seen = set(state.flags.get("visited") or [])
    seen.add(state.player.room)
    return seen


def mark_visited(state, room_id: str) -> None:
    seen = list(state.flags.get("visited") or [])
    if room_id not in seen:
        seen.append(room_id)
        state.flags["visited"] = seen
