"""Server-side, provider-neutral language-model client (PHASE2_SPEC §9).

* Providers are tried in the order of URBANOVA_LLM_PROVIDERS (default groq,openrouter,ollama); each uses the
  OpenAI-compatible chat API at URBANOVA_LLM_<NAME>_BASE_URL with URBANOVA_LLM_<NAME>_MODEL and, except a local
  Ollama, URBANOVA_LLM_<NAME>_API_KEY. Keys live only in the server environment: they are never written to the
  database, the cache, logs or any response.
* A provider that fails or times out is skipped for the rest of the UTC day; one that is rate-limited (HTTP 429) is paused
  until its stated reset time (1 minute when it gives none), so a per-minute limit does not cost the whole day.
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
    # A model pool: Groq's free tier limits each model separately (about 1,000 requests a day and 8,000 tokens a minute,
    # checked 10 Nov 2026), so a rate-limited model hands the request to the next one at once instead of waiting.
    "groq": {"base_url": "https://api.groq.com/openai/v1", "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"], "needs_key": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free", "needs_key": True},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1:8b", "needs_key": False},
    # Paid (prepaid credit); opt-in only. GPT-5 models accept only the default temperature, so none is sent.
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5.4-mini", "needs_key": True, "temperature": None},
}
CHAIN_DEFAULT = "groq,openrouter,ollama"
_lock = threading.Lock()
MAX_RETRY_WAIT_S = 30.0


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds to wait from a 429 response (Retry-After, or Groq's x-ratelimit-reset-tokens such as '7.66s')."""
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = (r.headers.get(h) or "").strip().lower()
        if not v:
            continue
        try:
            if v.endswith("ms"):
                return float(v[:-2]) / 1000
            if v.endswith("s") and "m" not in v:
                return float(v[:-1])
            if "m" in v:  # e.g. "1m2.5s"
                mins, _, secs = v.partition("m")
                return float(mins) * 60 + float(secs.rstrip("s") or 0)
            return float(v)
        except ValueError:
            continue
    return None


class ProviderFailure(Exception):
    """The provider did not give a usable response (network, timeout, limit, server error)."""


class RateLimited(ProviderFailure):
    """HTTP 429 after the one short wait: pause the provider until `wait_s` from now (not the whole day)."""

    def __init__(self, message: str, wait_s: float | None):
        super().__init__(message)
        self.wait_s = wait_s


class RequestTooLarge(ProviderFailure):
    """HTTP 413: the request exceeds the provider's per-request size limit. Not an outage: the provider stays usable."""


def enabled() -> bool:
    return not settings.offline and os.environ.get("URBANOVA_LLM_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def timeout_s() -> float:
    return float(os.environ.get("URBANOVA_LLM_TIMEOUT_S", "30"))


def max_calls_per_day(budget: str = "calls") -> int:
    """Daily call cap of a budget: "calls" (analysis AI) or "support" (the help assistant, its own counter)."""
    if budget == "support":
        return int(os.environ.get("URBANOVA_SUPPORT_MAX_CALLS_PER_DAY", "1000"))
    return int(os.environ.get("URBANOVA_LLM_MAX_CALLS_PER_DAY", "500"))


def _env(name: str, key: str) -> str:
    return os.environ.get(f"URBANOVA_LLM_{name.upper()}_{key}", "").strip()


def _models(name: str, d: dict, models: dict | None) -> list[str]:
    """The provider's model pool: a caller's own order, else URBANOVA_LLM_<NAME>_MODELS (comma-separated), else a single
    URBANOVA_LLM_<NAME>_MODEL, else the defaults."""
    if models and models.get(name):
        return list(models[name])
    pool = [m.strip() for m in _env(name, "MODELS").split(",") if m.strip()]
    if pool:
        return pool
    if _env(name, "MODEL"):
        return [_env(name, "MODEL")]
    return list(d.get("models") or [d["model"]])


def configured(chain: str | None = None, models: dict | None = None) -> list[dict]:
    """Provider entries in chain order, one per model of each provider's pool; `ready` is False without a key or approval.
    Contains the key (for the call only) — never return this to a client; use status() for that. `chain` overrides
    URBANOVA_LLM_PROVIDERS and `models` a provider's pool order (e.g. the help assistant's own lists). Each entry has an
    `id` ("groq:openai/gpt-oss-20b" in a pool, else the provider name) under which its rate limits are tracked."""
    out = []
    names = chain if chain is not None else os.environ.get("URBANOVA_LLM_PROVIDERS", CHAIN_DEFAULT)
    for name in [n.strip().lower() for n in names.split(",") if n.strip()]:
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
        pool = _models(name, d, models)
        for model in pool:
            out.append({"name": name, "id": f"{name}:{model}" if len(pool) > 1 else name, "base_url": base.rstrip("/"), "model": model, "key": key,
                        "ready": ready, "reason": reason, "temperature": d.get("temperature", 0)})
    return out


def status() -> dict:
    """What a client may see: enabled, and per provider its name, model, and whether it is configured. Never keys."""
    st = _state()
    from .support import chain as support_chain, models as support_models

    return {"enabled": enabled(), "calls_today": st["calls"], "max_calls_per_day": max_calls_per_day(),
            "support": {"calls_today": st.get("support", 0), "max_calls_per_day": max_calls_per_day("support"),
                        "providers": [{"name": p["name"], "model": p["model"], "configured": p["ready"]} for p in configured(support_chain(), support_models())]},
            "providers": [{"name": p["name"], "model": p["model"], "configured": p["ready"], "skipped_today": _skipped(st, p),
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


def is_local(p: dict) -> bool:
    """A provider on this machine (loopback address), e.g. Ollama: nothing leaves the server."""
    from urllib.parse import urlparse

    return (urlparse(p["base_url"]).hostname or "") in ("127.0.0.1", "localhost", "::1")


def cloud_allowed_for_real_data() -> bool:
    """Data decision of 2026-11-10 (docs/decisions/SUPERVISOR_DECISIONS.md, item 5b), replacing the one of 8 Nov: cloud
    models may receive real project data. URBANOVA_AI_CLOUD_FOR_REAL_DATA=0 restores the local-only rule."""
    return os.environ.get("URBANOVA_AI_CLOUD_FOR_REAL_DATA", "1") != "0"


LOCAL_ONLY_REASON = ("Real project data is sent only to a local model on this server (URBANOVA_AI_CLOUD_FOR_REAL_DATA=0), and no local model is available. "
                     "Mark the project as synthetic for cloud AI, or configure Ollama (URBANOVA_LLM_OLLAMA_MODEL).")


def available(local_only: bool = False, chain: str | None = None, budget: str = "calls", models: dict | None = None) -> list[dict]:
    """Ready provider entries not paused, in chain order (empty when AI is switched off or the budget is used up).
    `local_only` keeps only providers on this machine (real project data); `chain` and `models` as in configured().
    Each entry knows whether another one follows (`more_after`), so a rate limit moves on at once instead of waiting."""
    if not enabled():
        return []
    st = _state()
    if st.get(budget, 0) >= max_calls_per_day(budget):
        return []
    out = [p for p in configured(chain, models) if p["ready"] and not _skipped(st, p) and (not local_only or is_local(p))]
    for i, p in enumerate(out):
        p["more_after"] = i < len(out) - 1
    return out


def _paused(v) -> bool:
    if isinstance(v, dict):
        return time.time() < v.get("until", 0)
    return v is not None


def _skipped(st: dict, p: dict | str) -> bool:
    """True while the entry is paused: for the day (a plain reason) or until a time (rate limit). Pausing a provider's name
    pauses every model of its pool; pausing an entry's id pauses that model only."""
    if isinstance(p, str):
        return _paused(st["skipped"].get(p))
    return _paused(st["skipped"].get(p["name"])) or _paused(st["skipped"].get(p.get("id") or p["name"]))


def pause(provider: dict | str, exc: ProviderFailure) -> None:
    """Record a failure of a provider entry (or a whole provider, by name): a rate limit pauses it briefly, anything else
    (except a too-large request) for today."""
    name = provider if isinstance(provider, str) else (provider.get("id") or provider["name"])
    if isinstance(exc, RequestTooLarge):
        return
    if isinstance(exc, RateLimited):
        with _lock:
            st = _state()
            st["skipped"][name] = {"until": time.time() + min(max(exc.wait_s or 60.0, 1.0), 3600.0), "why": str(exc)[:200]}
            _save(st)
        return
    skip_for_today(name, str(exc))


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


def chat_json(provider: dict, messages: list[dict], budget: str = "calls") -> str:
    """One chat completion asking for a JSON object; returns the message content (text). Raises ProviderFailure."""
    with _lock:
        st = _state()
        if st.get(budget, 0) >= max_calls_per_day(budget):
            raise ProviderFailure("daily call budget used up")
        st[budget] = st.get(budget, 0) + 1
        _save(st)
    url = f"{provider['base_url']}/chat/completions"
    headers = {"Content-Type": "application/json", "User-Agent": settings.user_agent}
    if provider["key"]:
        headers["Authorization"] = f"Bearer {provider['key']}"
    body = {"model": provider["model"], "messages": messages, "response_format": {"type": "json_object"}}
    if provider.get("temperature", 0) is not None:
        body["temperature"] = provider.get("temperature", 0)
    t0 = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout_s())
        if r.status_code == 429:
            # Per-minute limits clear quickly: wait as asked (up to MAX_RETRY_WAIT_S) and try once more.
            wait = _retry_after(r)
            if wait is not None and wait <= MAX_RETRY_WAIT_S and not provider.get("more_after"):  # with another model ready, move on
                _log(provider["name"], url, 429, int((time.monotonic() - t0) * 1000), "rate_limited_wait", 1, False, f"retry after {wait:.0f}s")
                time.sleep(wait)
                r = httpx.post(url, json=body, headers=headers, timeout=timeout_s())
    except httpx.TimeoutException:
        _log(provider["name"], url, None, int((time.monotonic() - t0) * 1000), "timeout", 1, False)
        raise ProviderFailure("timeout")
    except httpx.HTTPError as exc:
        _log(provider["name"], url, None, int((time.monotonic() - t0) * 1000), "network_error", 1, False, type(exc).__name__)
        raise ProviderFailure("network error")
    ms = int((time.monotonic() - t0) * 1000)
    if r.status_code == 413:
        _log(provider["name"], url, 413, ms, "request_too_large", 1, False)
        raise RequestTooLarge("HTTP 413 (request too large for this model's limit)")
    if r.status_code != 200:
        _log(provider["name"], url, r.status_code, ms, "rate_limited" if r.status_code == 429 else "http_error", 1, False)
        if r.status_code == 429:
            raise RateLimited("HTTP 429 (rate limit)", _retry_after(r))
        raise ProviderFailure(f"HTTP {r.status_code}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        _log(provider["name"], url, 200, ms, "invalid_response", 1, False)
        raise ProviderFailure("invalid response")
    _log(provider["name"], url, 200, ms, "ok", 1, False, f"model {provider['model']}")
    return content or ""
