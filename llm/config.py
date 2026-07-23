"""Load LLM connection settings from settings.json (gitignored).

settings.json holds the user's own OpenAI-compatible endpoint:
    {"base_url": "...", "api_key": "...", "model": "..."}

The api_key is secret — never log it or commit it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "settings.json"


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str

    def redacted(self) -> dict:
        """Safe-to-log view of the settings (key masked)."""
        key = self.api_key or ""
        masked = (key[:6] + "…" + key[-4:]) if len(key) > 12 else "…"
        return {"base_url": self.base_url, "api_key": masked, "model": self.model}


class SettingsError(RuntimeError):
    pass


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if not path.exists():
        raise SettingsError(
            f"Missing {path.name}. Copy settings.example.json to settings.json and fill "
            f"in your base_url, api_key and model."
        )
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SettingsError(f"{path.name} is not valid JSON: {e}") from e

    missing = [k for k in ("base_url", "api_key", "model") if not raw.get(k)]
    if missing:
        raise SettingsError(f"{path.name} is missing required keys: {', '.join(missing)}")

    return Settings(
        base_url=str(raw["base_url"]).rstrip("/"),
        api_key=str(raw["api_key"]),
        model=str(raw["model"]),
    )
