"""Historical daily weather adapters: Open-Meteo (ERA5) primary, NASA POWER fallback.

Both are normalised to one internal series model. Variables that are not
equivalent between providers (wind at 10 m max vs 2 m mean) are dropped from the
fallback rather than silently mixed (FR-08, A.5).
"""

from __future__ import annotations

import math
import os
from datetime import date

from .base import ProviderError, http_json, provider

# Internal variable → (unit, physical range) used for validation (FR-11 step 5)
VARIABLES = {
    "tmax_c": ("°C", (-90, 60)),
    "tmin_c": ("°C", (-90, 60)),
    "tmean_c": ("°C", (-90, 60)),
    "rh_mean_pct": ("%", (0, 100)),
    "wind_max_10m_kmh": ("km/h", (0, 400)),
    "precip_mm": ("mm", (0, 2000)),
    "sw_mj_m2": ("MJ/m²", (0, 45)),
}
LABELS = {
    "tmax_c": "Daily maximum air temperature (2 m)",
    "tmin_c": "Daily minimum air temperature (2 m)",
    "tmean_c": "Daily mean air temperature (2 m)",
    "rh_mean_pct": "Daily mean relative humidity (2 m)",
    "wind_max_10m_kmh": "Daily maximum wind speed (10 m)",
    "precip_mm": "Daily precipitation",
    "sw_mj_m2": "Daily shortwave radiation sum",
}

OPEN_METEO_MAP = {
    "temperature_2m_max": "tmax_c", "temperature_2m_min": "tmin_c", "temperature_2m_mean": "tmean_c",
    "relative_humidity_2m_mean": "rh_mean_pct", "wind_speed_10m_max": "wind_max_10m_kmh",
    "precipitation_sum": "precip_mm", "shortwave_radiation_sum": "sw_mj_m2",
}
OPEN_METEO_UNITS = {
    "temperature_2m_max": "°C", "temperature_2m_min": "°C", "temperature_2m_mean": "°C",
    "relative_humidity_2m_mean": "%", "wind_speed_10m_max": "km/h", "precipitation_sum": "mm",
    "shortwave_radiation_sum": "MJ/m²",
}
POWER_MAP = {"T2M_MAX": "tmax_c", "T2M_MIN": "tmin_c", "T2M": "tmean_c", "RH2M": "rh_mean_pct", "PRECTOTCORR": "precip_mm", "ALLSKY_SFC_SW_DWN": "sw_mj_m2"}
POWER_UNITS = {"T2M_MAX": "C", "T2M_MIN": "C", "T2M": "C", "RH2M": "%", "PRECTOTCORR": "mm/day", "ALLSKY_SFC_SW_DWN": "kW-hr/m^2/day"}


def _check_dates(provider_id: str, dates: list[str], start: date, end: date) -> None:
    """Reject responses with timestamps outside the requested period or out of order (FR-07 step 7)."""
    if dates != sorted(dates) or dates[0] < start.isoformat() or dates[-1] > end.isoformat():
        raise ProviderError(provider_id, "invalid_response", "Returned dates are outside the requested period or not in order.")


def _validate_series(series: dict[str, list], dates: list[str]) -> dict:
    """Range-check values; out-of-range values become missing (recorded, never zero)."""
    qc = {"days": len(dates), "missing": {}, "out_of_range": {}}
    for var, values in series.items():
        lo, hi = VARIABLES[var][1]
        bad = 0
        for i, v in enumerate(values):
            if v is None:
                continue
            if isinstance(v, float) and math.isnan(v):
                values[i] = None
            elif not lo <= v <= hi:
                values[i] = None
                bad += 1
        qc["out_of_range"][var] = bad
        qc["missing"][var] = sum(1 for v in values if v is None)
    if len(set(dates)) != len(dates):
        raise ProviderError("weather", "invalid_response", "Duplicate dates in the response.")
    return qc


# ETCCDI base period for percentile indices such as TX90p (Zhang et al. 2011, R-1).
BASE_PERIOD = (date(1961, 1, 1), date(1990, 12, 31))


def fetch_open_meteo(lat: float, lon: float, start: date, end: date) -> dict:
    return _open_meteo(lat, lon, start, end, list(OPEN_METEO_MAP))


def fetch_tmax_base(lat: float, lon: float) -> dict:
    """Daily ERA5 maximum temperature for the 1961–1990 base period (one cached request)."""
    return _open_meteo(lat, lon, *BASE_PERIOD, ["temperature_2m_max"])


def _open_meteo(lat: float, lon: float, start: date, end: date, daily_vars: list[str]) -> dict:
    p = provider("open_meteo_archive")
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4), "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": ",".join(daily_vars), "timezone": "auto", "models": "era5",
    }
    # Commercial use requires Open-Meteo's paid API: same parameters on the customer host plus
    # an `apikey` (server-side only; excluded from the cache key and redacted in logs).
    key = os.environ.get(p.get("commercial_key_env") or "", "")
    url = "https://customer-archive-api.open-meteo.com/v1/archive" if key else "https://archive-api.open-meteo.com/v1/archive"
    req = {**params, "apikey": key} if key else params
    body, meta = http_json("open_meteo_archive", "GET", url, params=req, cache=True, cache_params=params)
    meta["licence_tier"] = "commercial subscription" if key else "free tier (non-commercial use only)"
    if not isinstance(body, dict) or body.get("error"):
        raise ProviderError("open_meteo_archive", "invalid_response", str(body.get("reason") if isinstance(body, dict) else body)[:200])
    daily = body.get("daily") or {}
    units = body.get("daily_units") or {}
    dates = daily.get("time") or []
    if not dates:
        raise ProviderError("open_meteo_archive", "no_data", "No daily records returned.")
    _check_dates("open_meteo_archive", dates, start, end)
    series = {}
    for src, var in OPEN_METEO_MAP.items():
        if src not in daily:
            continue
        if units.get(src) != OPEN_METEO_UNITS[src]:
            raise ProviderError("open_meteo_archive", "invalid_response", f"Unexpected unit {units.get(src)!r} for {src}.")
        vals = daily[src]
        if len(vals) != len(dates):
            raise ProviderError("open_meteo_archive", "invalid_response", f"Length mismatch for {src}.")
        series[var] = [None if v is None else float(v) for v in vals]
    qc = _validate_series(series, dates)
    return {
        "provider": "open_meteo_archive", "provider_name": p["name"], "model": "ERA5", "dates": dates, "series": series,
        "units": {v: VARIABLES[v][0] for v in series}, "grid_point": {"latitude": body.get("latitude"), "longitude": body.get("longitude"), "elevation_m": body.get("elevation")},
        "timezone": body.get("timezone"), "spatial_resolution": p["spatial_resolution"], "qc": qc, "meta": meta,
        "request_params": params, "attribution": p["attribution"], "license": p["license"],
        "transformations": ["Provider units checked against expected units", "Out-of-range values set to missing"],
        "excluded_variables": [],
    }


def fetch_nasa_power(lat: float, lon: float, start: date, end: date) -> dict:
    p = provider("nasa_power_daily")
    params = {
        "parameters": ",".join(POWER_MAP), "community": "RE", "longitude": round(lon, 4), "latitude": round(lat, 4),
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON", "time-standard": "LST",
    }
    body, meta = http_json("nasa_power_daily", "GET", "https://power.larc.nasa.gov/api/temporal/daily/point", params=params, cache=True, cache_params=params)
    try:
        param_block = body["properties"]["parameter"]
        units_block = body.get("parameters") or {}
        fill = body.get("header", {}).get("fill_value", -999.0)
    except (KeyError, TypeError):
        raise ProviderError("nasa_power_daily", "invalid_response", "Missing properties.parameter block.")
    keys = sorted(next(iter(param_block.values())).keys()) if param_block else []
    if not keys:
        raise ProviderError("nasa_power_daily", "no_data")
    dates = [f"{k[:4]}-{k[4:6]}-{k[6:]}" for k in keys]
    _check_dates("nasa_power_daily", dates, start, end)
    series = {}
    transformations = ["Fill value %s converted to missing" % fill, "Out-of-range values set to missing"]
    for src, var in POWER_MAP.items():
        if src not in param_block:
            continue
        unit = (units_block.get(src) or {}).get("units")
        if unit and unit != POWER_UNITS[src]:
            raise ProviderError("nasa_power_daily", "invalid_response", f"Unexpected unit {unit!r} for {src}.")
        vals = []
        for k in keys:
            v = param_block[src].get(k)
            vals.append(None if v is None or v == fill else float(v))
        if src == "ALLSKY_SFC_SW_DWN":
            vals = [None if v is None else v * 3.6 for v in vals]  # kWh/m²/day → MJ/m²/day
            transformations.append("ALLSKY_SFC_SW_DWN converted kWh/m²/day → MJ/m² (×3.6)")
        series[var] = vals
    qc = _validate_series(series, dates)
    geom = body.get("geometry", {}).get("coordinates", [None, None, None])
    return {
        "provider": "nasa_power_daily", "provider_name": p["name"], "model": "MERRA-2 / CERES SYN1deg", "dates": dates,
        "series": series, "units": {v: VARIABLES[v][0] for v in series},
        "grid_point": {"latitude": geom[1], "longitude": geom[0], "elevation_m": geom[2] if len(geom) > 2 else None},
        "timezone": "local solar time", "spatial_resolution": p["spatial_resolution"], "qc": qc, "meta": meta,
        "request_params": params, "attribution": p["attribution"], "license": p["license"], "transformations": transformations,
        "excluded_variables": [{"variable": "wind_max_10m_kmh", "reason": "NASA POWER provides 2 m wind, which is not comparable with the primary provider's 10 m maximum wind; excluded."}],
    }


def fetch_daily(lat: float, lon: float, start: date, end: date) -> tuple[dict, list[dict]]:
    """Primary then fallback. Returns (standardised series, list of failed attempts)."""
    failures = []
    for fn in (fetch_open_meteo, fetch_nasa_power):
        try:
            return fn(lat, lon, start, end), failures
        except ProviderError as exc:
            failures.append(exc.as_dict())
    raise ProviderError("weather", "no_data", "All weather providers failed: " + "; ".join(f"{f['provider']}: {f['kind']}" for f in failures))
