"""Player preferences — how the game presents itself, not what happens in it.

Kept apart from `settings.json` on purpose: that file holds the endpoint and an API
key, and preferences have no business sitting next to a credential. Kept out of the
save bundle too, because these belong to the person playing rather than to a
playthrough — you should not have to turn the typewriter back off after loading.

Unknown keys in the file are ignored and missing ones fall back to DEFAULTS, so an
older prefs.json keeps working when a new option is added.
"""
from __future__ import annotations

import json
from pathlib import Path

PREFS_PATH = Path(__file__).resolve().parent.parent / "prefs.json"

# Paged replies are off by default. Breaking a reply into beats reads well when the
# prose was *written* for it; ours is written in one breath by a model, and cutting it
# against its own rhythm made warm characters sound clipped. It stays available for
# anyone who prefers the Undertale cadence.
DEFAULTS: dict = {
    "paged_dialogue": False,
    "text_speed": 55,          # characters per second for the typewriter
}
TEXT_SPEEDS = (25, 40, 55, 80, 120, 0)      # 0 = no typewriter, show it at once

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = dict(DEFAULTS)
        try:
            data = json.loads(PREFS_PATH.read_text())
            if isinstance(data, dict):
                _cache.update({k: v for k, v in data.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass                # no prefs yet, or a corrupt one: the defaults will do
    return _cache


def get(key: str):
    return _load().get(key, DEFAULTS.get(key))


def set(key: str, value) -> None:      # noqa: A001 — reads better than set_pref()
    if key not in DEFAULTS:
        return
    _load()[key] = value
    try:
        PREFS_PATH.write_text(json.dumps(_cache, indent=2))
    except OSError:
        pass                    # an unwritable directory shouldn't stop the game


def speed_label(cps: int) -> str:
    return "instant" if not cps else f"{cps} cps"
