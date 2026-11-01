"""Shared HTTP behaviour for provider adapters (FR-07, FR-08, FR-40, FR-41, FR-42).

* bounded retries with exponential backoff, honouring Retry-After
* explicit failure kinds (a failed request is not "no data")
* response caching for historical requests, keyed by request parameters
* operational logging with secrets/query tokens redacted
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from .. import db
from ..settings import load_config, settings

FAILURE_KINDS = {
    "offline": "External data access is disabled in this environment (URBANOVA_OFFLINE).",
    "disabled": "The provider is not enabled or not implemented in this build.",
    "not_approved": "The provider or access route has not been approved for use.",
    "license_restricted": "The provider's licence does not permit this use.",
    "timeout": "The provider did not respond in time.",
    "rate_limited": "The provider rejected the request because of rate limits.",
    "auth_failed": "The provider rejected the credentials or token.",
    "http_error": "The provider returned an error.",
    "network_error": "The provider could not be reached.",
    "invalid_response": "The provider's response did not match the expected schema.",
    "no_data": "The request succeeded but returned no usable data.",
    "not_covered": "The provider does not cover the requested place or period.",
}


class ProviderError(Exception):
    def __init__(self, provider: str, kind: str, detail: str = ""):
        self.provider, self.kind, self.detail = provider, kind, detail
        super().__init__(f"{provider}: {FAILURE_KINDS.get(kind, kind)} {detail}".strip())

    def as_dict(self) -> dict:
        return {"provider": self.provider, "kind": self.kind, "message": FAILURE_KINDS.get(self.kind, self.kind), "detail": self.detail}


_registry: dict[str, dict] | None = None


def registry() -> dict[str, dict]:
    global _registry
    if _registry is None:
        _registry = {p["id"]: p for p in load_config("providers.json")["providers"]}
    return _registry


# Approval states that allow a provider to be called. "fr_recommended" = named in the
# Functional Requirements as a recommended integration and used here for non-commercial
# evaluation; "approved" = confirmed by the supervisor (record approved_by/approved_on).
USABLE_APPROVAL = {"fr_recommended", "fr_required", "approved"}


def availability(provider_id: str) -> tuple[bool, str | None, str]:
    """(usable, failure kind, user-facing reason) — never calls the network."""
    p = registry().get(provider_id)
    if not p or p.get("implementation_status") != "implemented" or not p.get("enabled"):
        return False, "disabled", f"{(p or {}).get('name', provider_id)} is not enabled in this build."
    if p.get("approval_status") not in USABLE_APPROVAL:
        return False, "not_approved", f"{p['name']} is awaiting supervisor approval ({p.get('approval_note') or 'access route not named in the Functional Requirements'}). It is not used until approved in config/providers.json."
    if settings.usage_context == "commercial" and p.get("commercial_use") == "requires_subscription" and not _has_commercial_key(p):
        return False, "license_restricted", f"{p['name']} may not be used commercially on its free tier. Configure a commercial subscription key or use another source."
    return True, None, ""


def _has_commercial_key(p: dict) -> bool:
    env = p.get("commercial_key_env")
    return bool(env and os.environ.get(env))


def provider(provider_id: str) -> dict:
    ok, kind, reason = availability(provider_id)
    if not ok:
        raise ProviderError(provider_id, kind, reason)
    return registry()[provider_id]


_SECRET_PARAMS = re.compile(r"([?&](?:sig|se|sv|sp|sr|st|skoid|sktid|skt|ske|sks|skv|token|key|apikey|api_key|access_token)=)[^&]*", re.I)


def redact(url: str) -> str:
    return _SECRET_PARAMS.sub(r"\1REDACTED", url)


def _log(provider_id: str, url: str, status: int | None, ms: int, outcome: str, attempt: int, cache_hit: bool, message: str = "") -> None:
    db.insert("provider_logs", {
        "provider": provider_id, "url": redact(url)[:500], "status_code": status, "duration_ms": ms,
        "outcome": outcome, "attempt": attempt, "cache_hit": int(cache_hit), "message": message[:500], "at": db.now(),
    })


def cache_key(provider_id: str, params: Any) -> str:
    blob = json.dumps({"p": provider_id, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _cache_path(provider_id: str, key: str):
    d = settings.cache_dir / provider_id
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def read_cache(provider_id: str, key: str) -> dict | None:
    path = _cache_path(provider_id, key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink(missing_ok=True)
    return None


def write_cache(provider_id: str, key: str, params: Any, body: Any) -> str:
    path = _cache_path(provider_id, key)
    path.write_text(json.dumps({"retrieved_at": db.now(), "params": params, "body": body}, default=str), encoding="utf-8")
    return str(path)


_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def http_json(provider_id: str, method: str, url: str, *, params: dict | None = None, data: dict | None = None,
              json_body: Any = None, cache: bool = False, cache_params: Any = None) -> tuple[Any, dict]:
    """Serialise identical cacheable requests so parallel scenario runs reuse one response (FR-40)."""
    if not cache:
        return _http_json(provider_id, method, url, params=params, data=data, json_body=json_body, cache=False, cache_params=cache_params)
    key = cache_key(provider_id, cache_params if cache_params is not None else {"url": url, "params": params, "data": data, "json": json_body})
    with _key_locks_guard:
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:
        return _http_json(provider_id, method, url, params=params, data=data, json_body=json_body, cache=True, cache_params=cache_params)


def _http_json(provider_id: str, method: str, url: str, *, params: dict | None = None, data: dict | None = None,
               json_body: Any = None, cache: bool = False, cache_params: Any = None) -> tuple[Any, dict]:
    """Perform a request and return (parsed JSON, meta). Raises ProviderError."""
    # Only cache where the provider's terms permit storing responses (FR-40, B.7).
    prov = provider(provider_id)  # always enforce enablement, approval and licence before any request
    cache = cache and bool(prov.get("caching_permitted"))
    key = cache_key(provider_id, cache_params if cache_params is not None else {"url": url, "params": params, "data": data, "json": json_body})
    if cache:
        hit = read_cache(provider_id, key)
        if hit is not None:
            _log(provider_id, url, None, 0, "cache_hit", 0, True)
            return hit["body"], {"cache_hit": True, "cache_key": key, "retrieved_at": hit["retrieved_at"], "raw_path": str(_cache_path(provider_id, key))}
    if settings.offline:
        _log(provider_id, url, None, 0, "offline", 0, False)
        raise ProviderError(provider_id, "offline")
    last_err: ProviderError | None = None
    for attempt in range(1, settings.http_max_retries + 1):
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=settings.http_timeout_s, headers={"User-Agent": settings.user_agent}, follow_redirects=True) as client:
                resp = client.request(method, url, params=params, data=data, json=json_body)
        except httpx.TimeoutException as exc:
            ms = int((time.monotonic() - t0) * 1000)
            _log(provider_id, url, None, ms, "timeout", attempt, False, str(exc))
            last_err = ProviderError(provider_id, "timeout", str(exc))
            _backoff(attempt, None)
            continue
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            _log(provider_id, url, None, ms, "network_error", attempt, False, str(exc))
            last_err = ProviderError(provider_id, "network_error", str(exc))
            _backoff(attempt, None)
            continue
        ms = int((time.monotonic() - t0) * 1000)
        full_url = str(resp.request.url)
        if resp.status_code in (401, 403):
            _log(provider_id, full_url, resp.status_code, ms, "auth_failed", attempt, False, resp.text[:200])
            raise ProviderError(provider_id, "auth_failed", f"HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            kind = "rate_limited" if resp.status_code == 429 else "http_error"
            _log(provider_id, full_url, resp.status_code, ms, kind, attempt, False, resp.text[:200])
            last_err = ProviderError(provider_id, kind, f"HTTP {resp.status_code}")
            _backoff(attempt, resp.headers.get("Retry-After"))
            continue
        if resp.status_code >= 400:
            # Client errors are not retried; the provider's message is kept for diagnosis.
            msg = _error_message(resp)
            _log(provider_id, full_url, resp.status_code, ms, "http_error", attempt, False, msg)
            raise ProviderError(provider_id, "http_error", f"HTTP {resp.status_code}: {msg}")
        try:
            body = resp.json()
        except ValueError:
            _log(provider_id, full_url, resp.status_code, ms, "invalid_response", attempt, False, "non-JSON body")
            raise ProviderError(provider_id, "invalid_response", "Response was not JSON.")
        _log(provider_id, full_url, resp.status_code, ms, "success", attempt, False)
        raw_path = write_cache(provider_id, key, cache_params or params, body) if cache else None
        return body, {"cache_hit": False, "cache_key": key, "retrieved_at": db.now(), "raw_path": raw_path}
    assert last_err is not None
    raise last_err


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("reason") or body.get("message") or body.get("messages") or body.get("detail") or body)[:300]
    except ValueError:
        pass
    return resp.text[:300]


def _backoff(attempt: int, retry_after: str | None) -> None:
    if attempt >= settings.http_max_retries:
        return
    delay = min(2 ** (attempt - 1), 8)
    if retry_after:
        try:
            delay = min(float(retry_after), 30)
        except ValueError:
            pass
    time.sleep(delay)


def utc_today() -> datetime:
    return datetime.now(timezone.utc)
