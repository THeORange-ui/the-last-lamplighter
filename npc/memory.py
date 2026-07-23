"""Per-NPC memory: an append-only log of salient events, persisted per NPC.

Kept deliberately simple for M1. When a log grows past MAX_ENTRIES we keep the
most recent ones for the prompt; a summarization pass can be added later (the
`older` entries are where that would hook in).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "runtime_memory"

MAX_ENTRIES = 40          # hard cap kept on disk
PROMPT_ENTRIES = 12       # how many recent entries go into the prompt


class NPCMemory:
    def __init__(self, npc_id: str):
        self.npc_id = npc_id
        self.path = MEMORY_DIR / f"{npc_id}.json"
        self.entries: list[str] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.entries = json.loads(self.path.read_text())
            except (json.JSONDecodeError, ValueError):
                self.entries = []

    def _save(self) -> None:
        MEMORY_DIR.mkdir(exist_ok=True)
        self.entries = self.entries[-MAX_ENTRIES:]
        self.path.write_text(json.dumps(self.entries, indent=2))

    def remember(self, note: str) -> None:
        note = note.strip()
        if note:
            self.entries.append(note)
            self._save()

    def recent(self, n: int = PROMPT_ENTRIES) -> list[str]:
        return self.entries[-n:]

    def as_prompt(self, n: int = PROMPT_ENTRIES) -> str:
        recent = self.recent(n)
        if not recent:
            return "(You have not met this person before.)"
        return "\n".join(f"- {e}" for e in recent)

    @staticmethod
    def wipe_all() -> None:
        """Delete all runtime memory (used by a fresh-game reset)."""
        if MEMORY_DIR.exists():
            for f in MEMORY_DIR.glob("*.json"):
                f.unlink()
