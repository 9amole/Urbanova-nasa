"""Regional climate and heat baseline (FR-17). Primary component of Heat Analysis."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean

from .. import catalog
from .. import values as V
from ..providers.base import ProviderError
from ..providers import weather
from ..providers.weather import LABELS, fetch_daily
from ..settings import load_config
from .common import COMPLETED, PARTIAL, UNAVAILABLE, Context, result

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_MISSING_FRACTION = 0.10


def default_period(today: date | None = None) -> dict:
    today = today or date.today()
    end = today.year - 1
    return {"start_year": end - 4, "end_year": end}


def _cmp(v: float, op: str, t: float) -> bool:
    return v > t if op == ">" else v >= t


def complete_years(dates: list[str], vals: list) -> dict[str, list[tuple[str, float]]]:
    """(date, value) pairs per year, for years with at most MAX_MISSING_FRACTION of days missing."""
    by_year: dict[str, list] = defaultdict(list)
    for d, v in zip(dates, vals):
        by_year[d[:4]].append((d, v))
    return {y: [(d, v) for d, v in days if v is not None] for y, days in by_year.items()
            if sum(v is not None for _, v in days) >= len(days) * (1 - MAX_MISSING_FRACTION)}


def summarise(data: dict, indices: list[dict]) -> dict:
    dates = data["dates"]
    s = data["series"]
    years = sorted({d[:4] for d in dates})
    monthly: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    yearly_precip: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for i, d in enumerate(dates):
        m = int(d[5:7])
        for var, vals in s.items():
            if vals[i] is not None:
                monthly[var][m].append(vals[i])
                if var == "precip_mm":
                    yearly_precip[d[:4]][m] += vals[i]
    clim = {}
    for var in s:
        clim[var] = [round(mean(monthly[var][m]), 2) if monthly[var][m] else None for m in range(1, 13)]
    if "precip_mm" in s:  # monthly totals averaged over years
        clim["precip_mm"] = [round(mean([yearly_precip[y][m] for y in years]), 1) for m in range(1, 13)]
    idx_out = []
    for ix in indices:
        vals = s.get(ix["variable"])
        if not vals:
            continue
        per_year = {y: sum(1 for _, v in obs if _cmp(v, ix["op"], ix["threshold"])) for y, obs in sorted(complete_years(dates, vals).items())}
        idx_out.append({**ix, "per_year": per_year, "mean_per_year": round(mean(per_year.values()), 1) if per_year else None, "years_complete": len(per_year)})
    tmax = [v for v in s.get("tmax_c", []) if v is not None]
    tmax_sorted = sorted(tmax)

    def q(p):
        if not tmax_sorted:
            return None
        k = (len(tmax_sorted) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(tmax_sorted) - 1)
        return round(tmax_sorted[lo] + (tmax_sorted[hi] - tmax_sorted[lo]) * (k - lo), 2)

    tmax_clim = clim.get("tmax_c") or [None] * 12
    ranked = sorted([(v, m) for m, v in enumerate(tmax_clim, start=1) if v is not None], reverse=True)
    hottest = sorted(m for _, m in ranked[:3])
    annual = {}
    for y in years:
        yv = [v for d, v in zip(dates, s.get("tmax_c", [])) if d.startswith(y) and v is not None]
        if yv:
            annual[y] = {"mean_tmax_c": round(mean(yv), 2), "max_tmax_c": round(max(yv), 1)}
    return {
        "monthly_climatology": clim, "indices": idx_out, "tmax_p90_c": q(0.90), "tmax_p95_c": q(0.95),
        "tmax_record_c": round(max(tmax), 1) if tmax else None, "hottest_months": hottest,
        "hottest_month_names": [MONTHS[m - 1] for m in hottest], "annual": annual, "years": years,
    }


def _calendar_day(d: str) -> int:
    """Day index 0–364 on a 365-day calendar; 29 February shares 28 February's threshold."""
    m, dd = int(d[5:7]), int(d[8:10])
    if m == 2 and dd == 29:
        dd = 28
    return date(2001, m, dd).timetuple().tm_yday - 1


def tx90p(dates: list[str], tmax: list, base_dates: list[str], base_tmax: list, window: int = 5) -> dict | None:
    """ETCCDI TX90p: % of days with Tmax above the calendar-day 90th percentile of the base period.

    The threshold for each calendar day is the 90th percentile (Hyndman–Fan type 8, as in the
    ETCCDI software) of base-period Tmax in a `window`-day window centred on that day. The
    analysis years lie outside the base period, so no bootstrap is needed (Zhang et al. 2011).
    Returns None when the base period is less than 90 % complete or a calendar window has no data."""
    import numpy as np

    by_day: list[list[float]] = [[] for _ in range(365)]
    valid = 0
    for d, v in zip(base_dates, base_tmax):
        if v is not None:
            by_day[_calendar_day(d)].append(v)
            valid += 1
    if not base_dates or valid < len(base_dates) * (1 - MAX_MISSING_FRACTION):
        return None
    half = window // 2
    thresholds = []
    for k in range(365):
        sample = [v for j in range(k - half, k + half + 1) for v in by_day[j % 365]]
        if not sample:
            return None
        thresholds.append(float(np.percentile(sample, 90, method="median_unbiased")))
    per_year = {y: round(100 * sum(1 for d, v in obs if v > thresholds[_calendar_day(d)]) / len(obs), 2)
                for y, obs in sorted(complete_years(dates, tmax).items()) if obs}
    return {"per_year": per_year, "mean": round(mean(per_year.values()), 1) if per_year else None, "thresholds_c": thresholds}


def _tx90p_metric(ctx: Context, data: dict, src: list[str]) -> dict:
    """The TX90p metric, or a missing value with the reason (a base-period failure does not affect the rest of the module)."""
    name, label = "climate_mean_annual_TX90p_pct", "Share of days hotter than the local 1961–1990 90th percentile (ETCCDI TX90p)"
    if data["provider"] != "open_meteo_archive":
        return V.missing(name, label, "% of days", V.UNAVAILABLE, "TX90p needs the 1961–1990 ERA5 base period; the fallback provider in use starts in 1981.", sources=src)
    try:
        base = weather.fetch_tmax_base(ctx.lat, ctx.lon)
    except ProviderError as exc:
        return V.missing(name, label, "% of days", V.UNAVAILABLE, f"The 1961–1990 base period could not be retrieved ({exc.kind}).", sources=src)
    out = tx90p(data["dates"], data["series"].get("tmax_c") or [], base["dates"], base["series"].get("tmax_c") or [])
    if not out or out["mean"] is None:
        return V.missing(name, label, "% of days", V.UNKNOWN, "The base period or the analysis years are less than 90 % complete.", sources=src)
    return V.metric(name, label + " — mean per year", out["mean"], "% of days",
                    method="Days with Tmax above the calendar-day 90th percentile of 1961–1990 ERA5 Tmax (5-day window, Hyndman–Fan type 8), "
                           "as a percentage of valid days per complete year, averaged; about 10 % means no change from the base climate",
                    sources=src, quality={"provider": data["provider"], "base_period": "1961–1990", "basis": "climate.TX90p", "per_year": out["per_year"],
                                          "base_cache_key": base["meta"].get("cache_key")})


def run(ctx: Context) -> dict:
    period = ctx.project.get("analysis_period") or ctx.config.get("climate_period") or default_period()
    start, end = date(period["start_year"], 1, 1), date(period["end_year"], 12, 31)
    try:
        data, failures = fetch_daily(ctx.lat, ctx.lon, start, end)
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"No historical weather data could be retrieved ({exc.detail or exc.kind}).", failures=[exc.as_dict()])
    rules = load_config("rules.json")
    summary = summarise(data, rules["climate_indices"])
    ds_id = catalog.register(
        ctx.project["id"], data["provider"], "weather_daily", acquisition_method="api",
        request_params=data["request_params"], cache_key=data["meta"].get("cache_key"), raw_path=data["meta"].get("raw_path"),
        spatial_coverage={"type": "Point", "coordinates": [data["grid_point"]["longitude"], data["grid_point"]["latitude"]], "grid_point": data["grid_point"]},
        time_coverage={"start": data["dates"][0], "end": data["dates"][-1], "days": len(data["dates"])},
        spatial_resolution=data["spatial_resolution"], temporal_resolution="daily", variables=list(data["series"]),
        units=data["units"], crs="EPSG:4326", quality=data["qc"], provenance=data["transformations"],
        product_id=data["model"], data_nature="reanalysis",
    )
    status = COMPLETED
    warnings, limitations = [], [
        f"Values come from {data['provider_name']} gridded reanalysis at {data['spatial_resolution']}. They describe regional conditions, not the microclimate inside the development.",
        "Air temperature is not land surface temperature and neither alone measures pedestrian thermal comfort.",
        # From the D3 station check (validation/climate_ghcn_results.json, docs/VALIDATION.md).
        "Checked against two Egyptian weather stations, ERA5 daily maximum temperature was within about 0.7 °C on average, but daily "
        "minimum temperature was 1.5–2.2 °C warmer; counts of tropical nights (TR) can therefore be overstated by tens of nights per "
        "year, and threshold counts such as TX35 shift noticeably with small biases.",
    ]
    if failures:
        status = PARTIAL
        warnings.append(
            f"The primary provider failed ({failures[0]['kind']}); {data['provider_name']} was used as a compatible fallback. "
            "Its grid and model differ, so values are not directly comparable with runs that used the primary provider."
        )
    for ex in data["excluded_variables"]:
        warnings.append(ex["reason"])
    worst_missing = max((n / max(data["qc"]["days"], 1) for n in data["qc"]["missing"].values()), default=0)
    if worst_missing > MAX_MISSING_FRACTION:
        status = PARTIAL
        warnings.append(f"Up to {worst_missing:.0%} of daily values are missing for some variables; affected statistics may be biased.")
    src = [ds_id]
    q = {"provider": data["provider"], "period": f"{period['start_year']}–{period['end_year']}", "missing_values": data["qc"]["missing"]}
    m = []
    tmax_clim = summary["monthly_climatology"].get("tmax_c")
    if summary["hottest_months"] and tmax_clim:
        hot_mean = mean(tmax_clim[mm - 1] for mm in summary["hottest_months"])
        m.append(V.metric("climate_hot_season_mean_tmax_c", f"Mean daily maximum air temperature, hottest 3 months ({', '.join(summary['hottest_month_names'])})", round(hot_mean, 2), "°C",
                          method="Mean of daily Tmax over the three calendar months with the highest mean Tmax across the period", sources=src, quality=q))
    for key, label in (("tmax_p90_c", "90th percentile of daily maximum air temperature"), ("tmax_p95_c", "95th percentile of daily maximum air temperature"), ("tmax_record_c", "Highest daily maximum air temperature in period")):
        if summary[key] is not None:
            m.append(V.metric(f"climate_{key}", label, summary[key], "°C", method="Empirical percentile / maximum over all days in the period", sources=src, quality=q))
    for ix in summary["indices"]:
        name = f"climate_mean_annual_{ix['id']}_days"
        if ix["mean_per_year"] is None:
            m.append(V.missing(name, ix["label"] + " per year", "days/year", V.UNKNOWN, "No year had at least 90% complete data.", sources=src))
        else:
            m.append(V.metric(name, ix["label"] + " — mean per year", ix["mean_per_year"], "days/year",
                              method=f"Count of days with {ix['variable']} {ix['op']} {ix['threshold']} per complete year, averaged over {ix['years_complete']} years",
                              sources=src, quality={**q, "rule_status": ix["status"], "justification": ix["justification"]}))
    tx = _tx90p_metric(ctx, data, src)
    m.append(tx)
    if tx["state"] != V.AVAILABLE:
        warnings.append(f"ETCCDI TX90p was not calculated: {tx['reason']}")
    rh = summary["monthly_climatology"].get("rh_mean_pct")
    sw = summary["monthly_climatology"].get("sw_mj_m2")
    if summary["hottest_months"] and rh and all(rh[mm - 1] is not None for mm in summary["hottest_months"]):
        m.append(V.metric("climate_hot_season_mean_rh_pct", "Mean relative humidity, hottest 3 months", round(mean(rh[mm - 1] for mm in summary["hottest_months"]), 1), "%", method="Mean of daily mean RH", sources=src, quality=q))
    if sw and all(v is not None for v in sw):
        m.append(V.metric("climate_annual_shortwave_mj_m2_day", "Mean daily global shortwave radiation (regional solar resource)", round(mean(sw), 2), "MJ/m²/day",
                          method="Mean of daily shortwave radiation sums", sources=src, quality=q))
    ctx.shared["hottest_months"] = summary["hottest_months"]
    ctx.shared["climate_provider"] = data["provider"]
    details = {
        "provider": data["provider"], "provider_name": data["provider_name"], "model": data["model"], "grid_point": data["grid_point"],
        "timezone": data["timezone"], "period": period, "attribution": data["attribution"], "license": data["license"],
        "summary": summary, "variable_labels": {k: LABELS[k] for k in data["series"]}, "units": data["units"],
        "retrieved_at": data["meta"].get("retrieved_at"), "cache_hit": data["meta"].get("cache_hit"),
        "licence_tier": data["meta"].get("licence_tier"),
    }
    if data["provider"] == "open_meteo_archive" and data["meta"].get("licence_tier", "").startswith("free"):
        limitations.append("Retrieved on Open-Meteo's free tier, which permits non-commercial use only. Commercial use of URBANOVA requires an Open-Meteo subscription or another source.")
    return result(status, metrics=m, datasets=[ds_id], warnings=warnings, limitations=limitations, details=details, failures=failures)
