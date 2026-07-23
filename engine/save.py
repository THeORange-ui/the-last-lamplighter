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
AUTOSAVE = "autosave"        # default slot for new games / quick continue
SAVE_VERSION = 2


def _sanitize(name: str) -> str:
    keep = [c if (c.isalnum() or c in " -_") else "_" for c in name.strip()]
    return ("".join(keep).strip() or "save")[:40]


def slot_path(name: str) -> Path:
    return SAVE_DIR / f"{_sanitize(name)}.json"


def save_exists(name: str = AUTOSAVE) -> bool:
    return slot_path(name).exists()


def list_saves() -> list[str]:
    if not SAVE_DIR.exists():
        return []
    return sorted(p.stem for p in SAVE_DIR.glob("*.json"))


def latest_save() -> str | None:
    """Most recently modified slot name, for 'continue' on startup."""
    if not SAVE_DIR.exists():
        return None
    files = list(SAVE_DIR.glob("*.json"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime).stem


def delete_save(name: str) -> None:
    p = slot_path(name)
    if p.exists():
        p.unlink()


def wipe_all_saves() -> None:
    if SAVE_DIR.exists():
        for p in SAVE_DIR.glob("*.json"):
            p.unlink()


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


def _world_to_dict(state: WorldState) -> dict:
    return {
        "player": {"room": state.player.room, "x": state.player.x, "y": state.player.y,
                   "inventory": list(state.player.inventory)},
        "npcs": {nid: {"room": n.room, "x": n.x, "y": n.y, "affinity": n.affinity,
                       "talked_to": n.talked_to, "inventory": list(n.inventory),
                       "flags": n.flags}
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


def _world_from_dict(data: dict) -> WorldState:
    p = data["player"]
    player = PlayerState(room=p["room"], x=p["x"], y=p["y"],
                         inventory=list(p.get("inventory", [])))

    npcs: dict[str, NPCRuntime] = {}
    for nid, n in data.get("npcs", {}).items():
        npcs[nid] = NPCRuntime(npc_id=nid, room=n["room"], x=n["x"], y=n["y"],
                               affinity=n.get("affinity", 0),
                               talked_to=n.get("talked_to", False),
                               inventory=list(n.get("inventory", [])),
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


def save_bundle(state: WorldState, memories: dict, name: str = AUTOSAVE) -> None:
    """Write a save slot: full world state + all NPC memories."""
    bundle = {"version": SAVE_VERSION, "world": _world_to_dict(state),
              "memories": memories or {}}
    SAVE_DIR.mkdir(exist_ok=True)
    slot_path(name).write_text(json.dumps(bundle, indent=2))


def load_bundle(name: str = AUTOSAVE) -> tuple[WorldState, dict]:
    """Load a save slot, returning (WorldState, memories). Reads v1 world-only
    files too (memories come back empty)."""
    data = json.loads(slot_path(name).read_text())
    if "world" in data:                       # v2 bundle
        return _world_from_dict(data["world"]), data.get("memories", {})
    return _world_from_dict(data), {}         # legacy v1: bare world dict
