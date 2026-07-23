"""Save/load the full WorldState to save/game.json.

Per-NPC memory is persisted separately (runtime_memory/*.json), so a save file
plus the memory files together restore a complete session. Static data (the map,
KnownEntities) is regenerated from code, not saved.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.journal import Event, EventLog
from engine.quests import Objective, Quest, Reward
from engine.state import NPCRuntime, PlayerState, WorldState

ROOT = Path(__file__).resolve().parent.parent
SAVE_DIR = ROOT / "save"
SAVE_PATH = SAVE_DIR / "game.json"
SAVE_VERSION = 1


def save_exists(path: Path = SAVE_PATH) -> bool:
    return path.exists()


def wipe_save(path: Path = SAVE_PATH) -> None:
    if path.exists():
        path.unlink()


# --- serialization -----------------------------------------------------------
def _quest_to_dict(q: Quest) -> dict:
    return {
        "id": q.id, "title": q.title, "description": q.description, "giver": q.giver,
        "objective": {"type": q.objective.type, "target": q.objective.target,
                      "count": q.objective.count, "npc": q.objective.npc},
        "reward": {"type": q.reward.type, "value": q.reward.value},
        "status": q.status, "progress": q.progress,
    }


def _quest_from_dict(d: dict) -> Quest:
    o, r = d["objective"], d["reward"]
    return Quest(
        id=d["id"], title=d["title"], description=d["description"], giver=d["giver"],
        objective=Objective(type=o["type"], target=o["target"],
                            count=o.get("count", 1), npc=o.get("npc")),
        reward=Reward(type=r["type"], value=r["value"]),
        status=d.get("status", "active"), progress=d.get("progress", 0),
    )


def save_game(state: WorldState, path: Path = SAVE_PATH) -> None:
    data = {
        "version": SAVE_VERSION,
        "player": {"room": state.player.room, "x": state.player.x, "y": state.player.y,
                   "inventory": list(state.player.inventory)},
        "npcs": {nid: {"room": n.room, "x": n.x, "y": n.y, "affinity": n.affinity,
                       "talked_to": n.talked_to, "flags": n.flags}
                 for nid, n in state.npcs.items()},
        "lamps": dict(state.lamps),
        "quests": [_quest_to_dict(q) for q in state.quests],
        "flags": dict(state.flags),
        "world_facts": list(state.world_facts),
        "hearthlight": state.hearthlight,
        "events": {"seq": state.events._seq,
                   "list": [{"seq": e.seq, "kind": e.kind, "text": e.text,
                             "public": e.public} for e in state.events.events]},
    }
    SAVE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_game(path: Path = SAVE_PATH) -> WorldState:
    data = json.loads(path.read_text())
    p = data["player"]
    player = PlayerState(room=p["room"], x=p["x"], y=p["y"],
                         inventory=list(p.get("inventory", [])))

    npcs: dict[str, NPCRuntime] = {}
    for nid, n in data.get("npcs", {}).items():
        npcs[nid] = NPCRuntime(npc_id=nid, room=n["room"], x=n["x"], y=n["y"],
                               affinity=n.get("affinity", 0),
                               talked_to=n.get("talked_to", False),
                               flags=n.get("flags", {}))

    log = EventLog()
    ev = data.get("events", {})
    log._seq = ev.get("seq", 0)
    log.events = [Event(seq=e["seq"], kind=e["kind"], text=e["text"],
                        public=e.get("public", True)) for e in ev.get("list", [])]

    return WorldState(
        player=player,
        npcs=npcs,
        lamps=data.get("lamps", {}),
        quests=[_quest_from_dict(q) for q in data.get("quests", [])],
        flags=data.get("flags", {}),
        world_facts=data.get("world_facts", []),
        hearthlight=data.get("hearthlight", 60),
        events=log,
    )
