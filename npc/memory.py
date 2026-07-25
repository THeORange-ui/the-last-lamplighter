"""Per-NPC memory: pinned state of mind + a running summary + an append log.

When the verbatim log grows past COMPACT_THRESHOLD, the oldest entries are folded
into a natural-language `summary` (via an LLM summarizer passed in by the caller)
and dropped from the verbatim tail. The prompt then shows the summary plus the
most recent entries, keeping context small and cheap as play goes on.

`pinned` holds what the character *carries around* rather than what happened with
the player: the seed memories from their character file (their inner state at the
start of play) and anything later pinned as arc-critical. Compaction never touches
it, so with PROMPT_ENTRIES = 12 and witnessing writing memories every few beats,
the things that matter can't be buried. Pinned lines deliberately do NOT count
toward `has_met()` — a character with a full head of their own worries has still
never laid eyes on the player.

File format: {"summary": str, "pinned": [str], "entries": [str], "seeded": bool}.
Older formats (a bare JSON list, or a dict without pinned/seeded) still read.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "runtime_memory"

PROMPT_ENTRIES = 12       # how many recent verbatim entries go into the prompt
COMPACT_THRESHOLD = 16    # compact once the verbatim log exceeds this
KEEP_RECENT = 8           # entries kept verbatim after a compaction


class NPCMemory:
    # Live instances by npc_id, so a memory written from elsewhere (e.g. one NPC
    # telling another something) lands on the same object the game is already
    # holding, not a stale copy that would later overwrite it on disk.
    _LIVE: dict[str, "NPCMemory"] = {}

    def __init__(self, npc_id: str):
        self.npc_id = npc_id
        self.path = MEMORY_DIR / f"{npc_id}.json"
        self.summary: str = ""
        self.pinned: list[str] = []
        self.entries: list[str] = []
        self.seeded: bool = False
        self._load()
        self._seed_from_character()
        NPCMemory._LIVE[npc_id] = self

    @classmethod
    def remember_for(cls, npc_id: str, note: str) -> "NPCMemory":
        """Append a note to another NPC's memory (used by the `tell` action).
        Reuses the live instance if there is one, else loads/creates it."""
        inst = cls._LIVE.get(npc_id) or cls(npc_id)
        inst.remember(note)
        return inst

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(data, list):            # legacy format: bare list of entries
            self.entries = data
        elif isinstance(data, dict):
            self.summary = data.get("summary", "")
            self.pinned = data.get("pinned", [])
            self.entries = data.get("entries", [])
            self.seeded = bool(data.get("seeded", False))

    def _seed_from_character(self) -> None:
        """Pin the character file's `seed_memories` — their state of mind at the start
        of play. Idempotent: the `seeded` marker means this runs once per game."""
        if self.seeded:
            return
        self.seeded = True
        try:
            from npc.roster import load_character   # local: keeps the import cheap
            lines = load_character(self.npc_id).get("seed_memories", []) or []
        except KeyError:
            lines = []
        fresh = [str(ln).strip() for ln in lines if str(ln).strip()]
        if fresh:
            self.pinned = fresh + self.pinned
            self._save()

    def _save(self) -> None:
        MEMORY_DIR.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(
            {"summary": self.summary, "pinned": self.pinned,
             "entries": self.entries, "seeded": self.seeded}, indent=2))

    def remember(self, note: str) -> None:
        note = note.strip()
        if note:
            self.entries.append(note)
            self._save()

    def pin(self, note: str) -> None:
        """Keep something permanently in mind — compaction can never drop it."""
        note = note.strip()
        if note and note not in self.pinned:
            self.pinned.append(note)
            self._save()

    def recent(self, n: int = PROMPT_ENTRIES) -> list[str]:
        return self.entries[-n:]

    def has_met(self) -> bool:
        """Pinned lines are the character's own state of mind, not shared history, so
        they must not make a first meeting look like a reunion."""
        return bool(self.entries or self.summary)

    def mind_as_prompt(self) -> str:
        """The pinned block: what this character is carrying around, unprompted."""
        if not self.pinned:
            return ""
        return "\n".join(f"- {p}" for p in self.pinned)

    def maybe_compact(self, summarizer) -> bool:
        """If the log is long, summarize the oldest entries into `summary`.

        `summarizer(prior_summary, old_entries) -> str | None`. Returning None
        (e.g. on an LLM error) skips this compaction so nothing is lost.
        """
        if len(self.entries) <= COMPACT_THRESHOLD:
            return False
        old = self.entries[:-KEEP_RECENT]
        new_summary = summarizer(self.summary, old)
        if not new_summary:
            return False
        self.summary = new_summary.strip()
        self.entries = self.entries[-KEEP_RECENT:]
        self._save()
        return True

    def as_prompt(self, n: int = PROMPT_ENTRIES) -> str:
        parts: list[str] = []
        if self.summary:
            parts.append(f"In summary, what you remember of this person so far:\n{self.summary}")
        recent = self.recent(n)
        if recent:
            parts.append("More recently, between you:\n" + "\n".join(f"- {e}" for e in recent))
        if not parts:
            return "(You have not met this person before.)"
        return "\n\n".join(parts)

    @staticmethod
    def wipe_all() -> None:
        """Delete all runtime memory (used by a fresh-game reset). Also drops the
        live-instance registry so stale objects can't write over restored memory."""
        NPCMemory._LIVE.clear()
        if MEMORY_DIR.exists():
            for f in MEMORY_DIR.glob("*.json"):
                f.unlink()

    @staticmethod
    def snapshot_all() -> dict:
        """Read every NPC's current memory into {npc_id: {summary, entries}}.

        Reflects the on-disk working memory (write-through per turn), so this is a
        faithful capture for bundling into a save.
        """
        out: dict[str, dict] = {}
        if not MEMORY_DIR.exists():
            return out
        for f in MEMORY_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, list):
                data = {"summary": "", "entries": data}
            out[f.stem] = {"summary": data.get("summary", ""),
                           "pinned": data.get("pinned", []),
                           "entries": data.get("entries", []),
                           "seeded": bool(data.get("seeded", False))}
        return out

    @staticmethod
    def restore_all(memories: dict) -> None:
        """Replace working memory with the given snapshot (used on load)."""
        NPCMemory.wipe_all()
        MEMORY_DIR.mkdir(exist_ok=True)
        for npc_id, data in (memories or {}).items():
            payload = {"summary": data.get("summary", ""),
                       "pinned": data.get("pinned", []),
                       "entries": data.get("entries", []),
                       "seeded": bool(data.get("seeded", False))}
            (MEMORY_DIR / f"{npc_id}.json").write_text(json.dumps(payload, indent=2))
