"""Transcript logging: every LLM call, in full, when the game is started with --log-llm.

The prompts *are* the game — behaviour lives in them far more than in the engine — so
being able to read exactly what a character was told, and exactly what came back, is the
main debugging tool this project has. Off by default: a session's transcripts run to
hundreds of kilobytes and nobody wants them written during normal play.

Calls are grouped into one file each, so a conversation reads top to bottom instead of
being scattered across a single interleaved log. A group is named by the caller
(`log_group="dialogue:wren"`); dialogue groups additionally carry a conversation number,
bumped by `begin_conversation()` when a new dialogue box opens, so "everything I said to
Wren the second time I spoke to her" is one file.

Nothing here is on the hot path when logging is off — `is_on()` is a bare bool check.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_ROOT = ROOT / "logs"

_dir: Path | None = None
_lock = threading.Lock()
_turns: dict[str, int] = {}          # group -> how many calls it has seen
_conversations: dict[str, int] = {}  # npc_id -> which conversation we're in

# The endpoint is an arbitrary proxy and its errors can quote request details back at
# us. A key must never reach a log file that the user might paste into an issue.
_SECRET = re.compile(r"\b(sk-[A-Za-z0-9_\-]{6,})")


def enable(label: str = "") -> Path:
    """Start logging into a fresh directory. Returns it."""
    global _dir
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _dir = LOG_ROOT / (f"{stamp}-{label}" if label else stamp)
    _dir.mkdir(parents=True, exist_ok=True)
    return _dir


def is_on() -> bool:
    return _dir is not None


def directory() -> Path | None:
    return _dir


def begin_conversation(npc_id: str) -> None:
    """A new dialogue box opened — later turns with this character go in a new file."""
    if not is_on():
        return
    with _lock:
        _conversations[npc_id] = _conversations.get(npc_id, 0) + 1


def scrub(text: str) -> str:
    return _SECRET.sub("sk-***REDACTED***", text or "")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "call"


def _filename(group: str) -> str:
    """`dialogue:wren` -> `dialogue-wren-02.md`; anything else -> `<slug>.md`."""
    kind, _, label = group.partition(":")
    if kind == "dialogue" and label:
        return f"dialogue-{_slug(label)}-{_conversations.get(label, 1):02d}.md"
    return f"{_slug(group)}.md"


def record(group: str, system: str, user: str, *, raw: str = "", parsed=None,
           usage=None, ms: int = 0, model: str = "", error: str = "") -> None:
    """Append one call to its group's file, and one line to the index."""
    if not is_on():
        return
    with _lock:
        n = _turns[group] = _turns.get(group, 0) + 1
        path = _dir / _filename(group)
        tokens = _usage_dict(usage)
        head = f"turn {n} · {datetime.now():%H:%M:%S} · {ms} ms"
        if tokens:
            head += (f" · {tokens.get('prompt_tokens','?')} in / "
                     f"{tokens.get('completion_tokens','?')} out")
        if error:
            head += " · FAILED"
        body = [
            f"\n\n## {head}\n",
            f"*model:* `{model}`\n" if model else "",
            "\n### system\n\n```\n", scrub(system), "\n```\n",
            "\n### user\n\n```\n", scrub(user), "\n```\n",
        ]
        if error:
            body += ["\n### error\n\n```\n", scrub(error), "\n```\n"]
        else:
            body += ["\n### response (raw)\n\n```\n", scrub(raw), "\n```\n"]
            if parsed is not None:
                body += ["\n### parsed\n\n```json\n",
                         json.dumps(parsed, indent=2, ensure_ascii=False), "\n```\n"]
        try:
            new = not path.exists()
            with path.open("a", encoding="utf-8") as fh:
                if new:
                    fh.write(f"# {group}\n")
                fh.write("".join(body))
            with (_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": time.time(), "group": group, "file": path.name, "turn": n,
                    "ms": ms, "model": model, "ok": not error, **(tokens or {}),
                }) + "\n")
        except OSError:
            pass          # a logging failure must never take a turn down with it


def _usage_dict(usage) -> dict | None:
    if usage is None:
        return None
    out = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            out[key] = value
    return out or None
