"""Per-NPC memory: a running summary + an append log of recent salient events.

When the verbatim log grows past COMPACT_THRESHOLD, the oldest entries are folded
into a natural-language `summary` (via an LLM summarizer passed in by the caller)
and dropped from the verbatim tail. The prompt then shows the summary plus the
most recent entries, keeping context small and cheap as play goes on.

File format: {"summary": str, "entries": [str, ...]}. Older saves that were a bare
JSON list of entries are still read correctly.
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
    def __init__(self, npc_id: str):
        self.npc_id = npc_id
        self.path = MEMORY_DIR / f"{npc_id}.json"
        self.summary: str = ""
        self.entries: list[str] = []
        self._load()

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
            self.entries = data.get("entries", [])

    def _save(self) -> None:
        MEMORY_DIR.mkdir(exist_ok=True)
        self.path.write_text(
            json.dumps({"summary": self.summary, "entries": self.entries}, indent=2)
        )

    def remember(self, note: str) -> None:
        note = note.strip()
        if note:
            self.entries.append(note)
            self._save()

    def recent(self, n: int = PROMPT_ENTRIES) -> list[str]:
        return self.entries[-n:]

    def has_met(self) -> bool:
        return bool(self.entries or self.summary)

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
        """Delete all runtime memory (used by a fresh-game reset)."""
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
                           "entries": data.get("entries", [])}
        return out

    @staticmethod
    def restore_all(memories: dict) -> None:
        """Replace working memory with the given snapshot (used on load)."""
        NPCMemory.wipe_all()
        MEMORY_DIR.mkdir(exist_ok=True)
        for npc_id, data in (memories or {}).items():
            payload = {"summary": data.get("summary", ""),
                       "entries": data.get("entries", [])}
            (MEMORY_DIR / f"{npc_id}.json").write_text(json.dumps(payload, indent=2))
