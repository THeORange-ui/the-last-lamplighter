"""Load static NPC character definitions from npc/characters/*.json."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CHARACTERS_DIR = Path(__file__).resolve().parent / "characters"


@lru_cache(maxsize=None)
def load_character(npc_id: str) -> dict:
    path = CHARACTERS_DIR / f"{npc_id}.json"
    if not path.exists():
        raise KeyError(f"No character file for {npc_id!r} at {path}")
    return json.loads(path.read_text())


def character_name(npc_id: str) -> str:
    try:
        return load_character(npc_id).get("name", npc_id.title())
    except KeyError:
        return npc_id.title()
