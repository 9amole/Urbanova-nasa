"""NASA POWER monthly time series for the Trend Detective (Space Apps 2026). No API key.

One request returns every catalog variable for a point from 1981 to the last complete year, so switching variables is
instant (and the response is cached). POWER marks missing months with -999: they become None, never 0.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from ..settings import load_config
from .base import http_json

URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
FILL = -999.0


@lru_cache(maxsize=1)
def catalog() -> dict:
    return load_config("variables.json")


def variable(var_id: str) -> dict | None:
    return next((v for v in catalog()["variables"] if v["id"] == var_id), None)


def last_complete_year() -> int:
    return date.today().year - 1


def fetch(lat: float, lon: float, start_year: int = 1981, end_year: int | None = None) -> tuple[dict[str, list[tuple[date, float | None]]], dict]:
    """Monthly series per POWER parameter: {"T2M": [(date, value | None), ...], ...}, plus the request metadata."""
    end_year = end_year or last_complete_year()
    params = {"parameters": ",".join(v["power"] for v in catalog()["variables"]), "community": "AG", "longitude": round(lon, 3),
              "latitude": round(lat, 3), "start": start_year, "end": end_year, "format": "JSON"}
    body, meta = http_json("nasa_power_monthly", "GET", URL, params=params, cache=True, cache_params=params)
    out = {}
    for name, values in (body.get("properties") or {}).get("parameter", {}).items():
        rows = []
        for key, v in sorted(values.items()):
            y, m = int(key[:4]), int(key[4:])
            if m == 13:  # POWER's annual value
                continue
            rows.append((date(y, m, 1), None if v is None or v <= FILL + 1 else float(v)))
        out[name] = rows
    return out, meta


REGION_URL = "https://power.larc.nasa.gov/api/temporal/monthly/regional"
REGION_HALF_DEG = 2.5  # a 5° × 5° window: about 10 × 8 NASA POWER cells (the API allows up to 10°)


def fetch_region(lat: float, lon: float, parameter: str, half: float = REGION_HALF_DEG) -> tuple[list[dict], dict]:
    """Every POWER cell in a window around (lat, lon) for one parameter: [{"lat", "lon", "series": [(date, value | None)]}]."""
    lat0, lat1 = max(-90.0, lat - half), min(90.0, lat + half)
    lon0, lon1 = max(-180.0, lon - half), min(180.0, lon + half)
    params = {"parameters": parameter, "community": "AG", "latitude-min": round(lat0, 2), "latitude-max": round(lat1, 2),
              "longitude-min": round(lon0, 2), "longitude-max": round(lon1, 2), "start": 1981, "end": last_complete_year(), "format": "JSON"}
    body, meta = http_json("nasa_power_monthly", "GET", REGION_URL, params=params, cache=True, cache_params=params)
    cells = []
    for f in body.get("features") or []:
        lon_c, lat_c = f["geometry"]["coordinates"][:2]
        rows = []
        for key, v in sorted(((f.get("properties") or {}).get("parameter") or {}).get(parameter, {}).items()):
            y, m = int(key[:4]), int(key[4:])
            if m == 13:
                continue
            rows.append((date(y, m, 1), None if v is None or v <= FILL + 1 else float(v)))
        cells.append({"lat": float(lat_c), "lon": float(lon_c), "series": rows})
    return cells, meta
