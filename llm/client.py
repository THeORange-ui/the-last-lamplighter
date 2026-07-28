"""Thin OpenAI-compatible chat client that returns parsed JSON.

The backend is an arbitrary user-supplied proxy, so we do NOT rely on the
function-calling API being present. Instead we ask the model for a single JSON
object and parse it defensively out of the response content.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import OpenAI

from . import log
from .config import Settings, SettingsError, load_settings

_client: OpenAI | None = None
_settings: Settings | None = None


def get_settings() -> Settings:
    """The endpoint settings, loaded once.

    A missing or malformed settings.json is raised as an LLMError, not a bare
    SettingsError: every caller already degrades gracefully on LLMError, and a fresh
    clone with no settings.json used to take the whole game down the first time the
    player tried to speak to anyone.
    """
    global _settings
    if _settings is None:
        try:
            _settings = load_settings()
        except SettingsError as e:
            raise LLMError(str(e)) from e
    return _settings


def settings_problem() -> str:
    """"" if the endpoint is configured, else a message saying what to do about it.
    Used at startup so the player is told before they walk up to somebody."""
    try:
        get_settings()
    except LLMError as e:
        return str(e)
    return ""


def _client_for(settings: Settings) -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=settings.base_url, api_key=settings.api_key)
    return _client


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a possibly-noisy completion."""
    text = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} span.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"Could not parse JSON from model output: {text[:200]!r}")


def complete_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.8,
    max_tokens: int = 700,
    log_group: str = "misc",
) -> dict[str, Any]:
    """Send one system+user turn, return the parsed JSON object.

    Raises LLMError on network/parse failure; callers decide how to degrade.
    `log_group` names the transcript file this call belongs to when the game was
    started with --log-llm (see llm/log.py); it does nothing otherwise.
    """
    settings = get_settings()
    client = _client_for(settings)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    def _log(**fields):
        if log.is_on():
            log.record(log_group, system, user, model=settings.model,
                       ms=int((time.monotonic() - started) * 1000), **fields)

    started = time.monotonic()
    # Prefer strict JSON mode, but many proxies reject the param — retry without.
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, **kwargs
        )
    except Exception:
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # network, auth, bad model, etc.
            _log(error=str(e))
            raise LLMError(str(e)) from e

    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError) as e:
        _log(error=f"Malformed response object: {e}")
        raise LLMError(f"Malformed response object: {e}") from e

    usage = getattr(resp, "usage", None)
    try:
        parsed = _extract_json(content)
    except LLMError as e:
        # Log the raw text too — an unparseable reply is exactly what you want to read.
        _log(raw=content, usage=usage, error=str(e))
        raise
    _log(raw=content, parsed=parsed, usage=usage)
    return parsed


def ping() -> tuple[bool, str]:
    """Quick connectivity check. Returns (ok, message)."""
    try:
        out = complete_json(
            'You are a JSON echo. Reply with exactly {"ok": true}.',
            "ping",
            temperature=0,
            max_tokens=20,
        )
        return (bool(out.get("ok", True)), "reachable")
    except Exception as e:  # noqa: BLE001 - surface any failure to caller
        return (False, str(e))
