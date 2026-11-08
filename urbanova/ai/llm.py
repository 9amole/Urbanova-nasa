"""Server-side, provider-neutral language-model client (PHASE2_SPEC §9).

* Providers are tried in the order of URBANOVA_LLM_PROVIDERS (default groq,openrouter,ollama); each uses the
  OpenAI-compatible chat API at URBANOVA_LLM_<NAME>_BASE_URL with URBANOVA_LLM_<NAME>_MODEL and, except a local
  Ollama, URBANOVA_LLM_<NAME>_API_KEY. Keys live only in the server environment: they are never written to the
  database, the cache, logs or any response.
* A provider that fails, times out or is over its limit is skipped for the rest of the UTC day.
* URBANOVA_LLM_MAX_CALLS_PER_DAY stops calls before a free tier's daily limit.
* Every call is recorded (provider, model, outcome, duration) without the key or the prompt.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import httpx

from ..providers.base import _log, availability
from ..settings import settings

DEFAULTS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "needs_key": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free", "needs_key": True},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1:8b", "needs_key": False},
}
_lock = threading.Lock()


class ProviderFailure(Exception):
    """The provider did not give a usable response (network, timeout, limit, server error)."""


def enabled() -> bool:
    return not settings.offline and os.environ.get("URBANOVA_LLM_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def timeout_s() -> float:
    return float(os.environ.get("URBANOVA_LLM_TIMEOUT_S", "30"))


def max_calls_per_day() -> int:
    return int(os.environ.get("URBANOVA_LLM_MAX_CALLS_PER_DAY", "500"))


def _env(name: str, key: str) -> str:
    return os.environ.get(f"URBANOVA_LLM_{name.upper()}_{key}", "").strip()


def configured() -> list[dict]:
    """Providers in chain order with their settings; `ready` is False without a key or approval. Contains the key
    (for the call only) — never return this to a client; use status() for that."""
    out = []
    for name in [n.strip().lower() for n in os.environ.get("URBANOVA_LLM_PROVIDERS", "groq,openrouter,ollama").split(",") if n.strip()]:
        d = DEFAULTS.get(name, {"base_url": "", "model": "", "needs_key": True})
        key = _env(name, "API_KEY")
        usable, _, why = availability(name)
        base = _env(name, "BASE_URL") or d["base_url"]
        ready, reason = True, ""
        if not usable:
            ready, reason = False, why
        elif d["needs_key"] and not key:
            ready, reason = False, f"No API key set (URBANOVA_LLM_{name.upper()}_API_KEY)."
        elif name == "ollama" and not _env(name, "MODEL") and not _env(name, "BASE_URL"):
            ready, reason = False, "Local model not configured (set URBANOVA_LLM_OLLAMA_MODEL)."
        out.append({"name": name, "base_url": base.rstrip("/"), "model": _env(name, "MODEL") or d["model"], "key": key, "ready": ready, "reason": reason})
    return out


def status() -> dict:
    """What a client may see: enabled, and per provider its name, model, and whether it is configured. Never keys."""
    st = _state()
    return {"enabled": enabled(), "calls_today": st["calls"], "max_calls_per_day": max_calls_per_day(),
            "providers": [{"name": p["name"], "model": p["model"], "configured": p["ready"], "skipped_today": p["name"] in st["skipped"],
                           "reason": p["reason"] or None} for p in configured()]}


# ---------------------------------------------------------------- per-day state (skips and budget)
def _state_path():
    day = datetime.now(timezone.utc).date().isoformat()
    d = settings.cache_dir / "llm"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"state-{day}.json"


def _state() -> dict:
    p = _state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"calls": 0, "skipped": {}}


def _save(st: dict) -> None:
    _state_path().write_text(json.dumps(st), encoding="utf-8")


def available() -> list[dict]:
    """Ready providers not skipped today, in chain order (empty when AI is switched off or the budget is used up)."""
    if not enabled():
        return []
    st = _state()
    if st["calls"] >= max_calls_per_day():
        return []
    return [p for p in configured() if p["ready"] and p["name"] not in st["skipped"]]


def skip_for_today(name: str, why: str) -> None:
    with _lock:
        st = _state()
        st["skipped"][name] = why[:200]
        _save(st)


def _cache_file(key: str):
    import hashlib

    d = settings.cache_dir / "llm"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


def cache_get(key: str) -> dict | None:
    """A stored output for (input hash, model, prompt version); a corrupt entry is removed."""
    p = _cache_file(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        p.unlink(missing_ok=True)
        return None


def cache_put(key: str, value: dict) -> None:
    _cache_file(key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def chat_json(provider: dict, messages: list[dict]) -> str:
    """One chat completion asking for a JSON object; returns the message content (text). Raises ProviderFailure."""
    with _lock:
        st = _state()
        if st["calls"] >= max_calls_per_day():
            raise ProviderFailure("daily call budget used up")
        st["calls"] += 1
        _save(st)
    url = f"{provider['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json", "User-Agent": settings.user_agent}
    if provider["key"]:
        headers["Authorization"] = f"Bearer {provider['key']}"
    body = {"model": provider["model"], "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}}
    t0 = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout_s())
    except httpx.TimeoutException:
        _log(provider["name"], url, None, int((time.monotonic() - t0) * 1000), "timeout", 1, False)
        raise ProviderFailure("timeout")
    except httpx.HTTPError as exc:
        _log(provider["name"], url, None, int((time.monotonic() - t0) * 1000), "network_error", 1, False, type(exc).__name__)
        raise ProviderFailure("network error")
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code != 200:
        _log(provider["name"], url, r.status_code, ms, "rate_limited" if r.status_code == 429 else "http_error", 1, False)
        raise ProviderFailure(f"HTTP {r.status_code}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        _log(provider["name"], url, 200, ms, "invalid_response", 1, False)
        raise ProviderFailure("invalid response")
    _log(provider["name"], url, 200, ms, "ok", 1, False, f"model {provider['model']}")
    return content or ""
