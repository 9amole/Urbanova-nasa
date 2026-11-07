"""Address search with OpenStreetMap Nominatim (PHASE2_SPEC §2.1, approved 2026-11-07).

Nominatim's usage policy: at most one request per second, no autocomplete (search only when the
user asks), results may be cached, and "© OpenStreetMap contributors" must be shown. Requests go
through base.http_json, so approval, caching, retries and request logging apply as for every source.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager, nullcontext

from .base import ProviderError, _cache_path, cache_key, http_json, provider

PROVIDER = "nominatim"
URL = "https://nominatim.openstreetmap.org/search"
MIN_INTERVAL_S = 1.0
MAX_RESULTS = 5

_lock = threading.Lock()
_last_request = 0.0
_clock, _sleep = time.monotonic, time.sleep  # module-level so tests can replace them


class GeocodeError(ValueError):
    pass


@contextmanager
def _throttle():
    """Uncached requests one at a time, at least MIN_INTERVAL_S apart (Nominatim usage policy)."""
    global _last_request
    with _lock:
        _sleep(max(0.0, MIN_INTERVAL_S - (_clock() - _last_request)))
        try:
            yield
        finally:
            _last_request = _clock()


def search(query: str) -> dict:
    """Up to five places matching `query`: {"results": [...], "attribution", "cache_hit"}. Raises ProviderError."""
    q = " ".join((query or "").split())
    if not 3 <= len(q) <= 200:
        raise GeocodeError("Enter an address or place name of 3 to 200 characters.")
    p = provider(PROVIDER)
    params = {"q": q, "format": "jsonv2", "limit": MAX_RESULTS, "accept-language": "en"}
    cached = _cache_path(PROVIDER, cache_key(PROVIDER, params)).exists()  # cached answers never wait
    with nullcontext() if cached else _throttle():
        body, meta = http_json(PROVIDER, "GET", URL, params=params, cache=True, cache_params=params)
    if not isinstance(body, list):
        raise ProviderError(PROVIDER, "invalid_response", "Expected a list of places.")
    results = []
    for r in body[:MAX_RESULTS]:
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
            bbox = [float(x) for x in r.get("boundingbox") or []]  # south, north, west, east
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            results.append({"name": str(r.get("display_name") or "")[:300], "latitude": lat, "longitude": lon,
                            "bbox": bbox if len(bbox) == 4 else None, "type": r.get("type")})
    return {"results": results, "attribution": p["attribution"], "cache_hit": bool(meta.get("cache_hit"))}
