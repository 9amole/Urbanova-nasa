"""Long-term climate trends at the site (A1b, NASA Space Apps 2026: "Be An Earth System Trend Detective").

Is the site's climate changing, how fast, and is the change significant? NASA POWER monthly data (MERRA-2, with
satellite products for sunshine) from 1981 to the last complete year, one request for all variables. Each variable goes
through the same validated path as the Trend Detective (urbanova.detective.analyse_variable): integrity checks,
deseasonalised yearly averages, Sen's slope with a 95 % range, and a verdict that needs both the persistence-corrected
Mann-Kendall test (Yue & Wang 2004) and the effective-sample-size t-test (Santer et al. 2000). Validation:
docs/VALIDATION_SPACEAPPS.md.
"""

from __future__ import annotations

from .. import catalog
from .. import values as V
from ..detective import analyse_variable
from ..providers import power_monthly as pm
from ..providers.base import ProviderError
from .common import COMPLETED, PARTIAL, UNAVAILABLE, Context, result

# The variables that matter for urban heat and comfort, most telling first.
VARIABLES = ("t2m_max", "t2m", "t2m_min", "precip", "rh", "sun")
START_YEAR = 1981
LEVEL_TEXT = {"significant": "significant", "borderline": "borderline (one test only)", "not_significant": "not significant"}


def run(ctx: Context) -> dict:
    end_year = pm.last_complete_year()
    try:
        data, meta = pm.fetch(ctx.lat, ctx.lon)
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"NASA POWER monthly data could not be retrieved ({exc.detail or exc.kind}).", failures=[exc.as_dict()])
    src_info = pm.catalog()["source"]
    ds_id = catalog.register(
        ctx.project["id"], "nasa_power_monthly", "weather_monthly", acquisition_method="api",
        request_params={"latitude": round(ctx.lat, 3), "longitude": round(ctx.lon, 3), "start": START_YEAR, "end": end_year},
        cache_key=meta.get("cache_key"), raw_path=meta.get("raw_path"),
        spatial_coverage={"type": "Point", "coordinates": [ctx.lon, ctx.lat]}, time_coverage={"start": f"{START_YEAR}-01", "end": f"{end_year}-12"},
        spatial_resolution="0.5° × 0.625° (about 55 km)", temporal_resolution="monthly", variables=[pm.variable(v)["power"] for v in VARIABLES],
        crs="EPSG:4326", product_id="MERRA-2 / CERES / GEWEX SRB / GPCP (NASA POWER)", data_nature="reanalysis",
    )
    metrics, rows, warnings = [], [], []
    for vid in VARIABLES:
        v = pm.variable(vid)
        name = f"trend_{vid}_per_decade"
        label = f"Trend in {v['name'].lower()} since {START_YEAR}"
        res = analyse_variable(data, v, START_YEAR, end_year)
        if not res.get("ok"):
            why = res.get("reason") or "Not enough complete years to test a trend."
            metrics.append(V.missing(name, label, f"{v['unit']}/decade", V.UNKNOWN, why, sources=[ds_id]))
            warnings.append(f"{v['name']}: {why}")
            continue
        rate, verdict = res["rate"]["per_decade"], res["verdict"]
        metrics.append(V.metric(
            name, label, round(rate["slope"], 4), f"{v['unit']}/decade", output_kind=V.CALCULATED, sources=[ds_id],
            method=(f"Sen's slope of deseasonalised yearly averages, {res['period']['first_year']}–{res['period']['last_year']}; verdict "
                    f"{LEVEL_TEXT[verdict['level']]} (needs both corrected tests at p < 0.05): {verdict['tests']}"),
            uncertainty={"ci95_low": round(rate["ci_low"], 4), "ci95_high": round(rate["ci_high"], 4), "method": "Sen's slope 95 % range from the corrected Mann-Kendall variance"},
            quality={"verdict": verdict["level"], "p_mann_kendall_corrected": res["mann_kendall"]["p_value"], "p_t_test_corrected": res["ols"]["p_value"],
                     "effective_years": round(res["ols"]["n_eff"], 1), "complete_pct": res["integrity"]["complete_pct"]},
            extra={"verdict": verdict["level"], "direction": verdict["direction"], "total_change": round(res["rate"]["total_change"], 3)},
        ))
        rows.append({"id": vid, "name": v["name"], "unit": v["unit"], "definition": v.get("definition"), "verdict": verdict,
                     "per_decade": rate, "total_change": res["rate"]["total_change"], "period": res["period"],
                     "p_mann_kendall": res["mann_kendall"]["p_value"], "p_mann_kendall_plain": res["mann_kendall"]["p_value_plain"],
                     "p_t_test": res["ols"]["p_value"], "p_t_test_plain": res["ols"]["p_value_plain"], "effective_years": res["ols"]["n_eff"],
                     "change_point": res["change_point"], "series": res["series"], "deseasonalised": res["deseasonalised"], "annual": res["annual"], "trend_line": res["trend_line"], "notes": res["notes"],
                     "integrity": res["integrity"], "metric_name": name})
    if not rows:
        return result(UNAVAILABLE, metrics=metrics, datasets=[ds_id], warnings=warnings, reason="No variable had a long enough record to test a trend.")
    ctx.shared["climate_trends"] = {r["id"]: {"level": r["verdict"]["level"], "per_decade": r["per_decade"]["slope"]} for r in rows}
    details = {"rows": rows, "point": {"lat": round(ctx.lat, 2), "lon": round(ctx.lon, 2)}, "start_year": START_YEAR, "end_year": end_year,
               "provider_name": src_info.get("name", "NASA POWER"), "attribution": src_info.get("attribution"),
               "retrieved_at": meta.get("retrieved_at"), "cache_hit": meta.get("cache_hit")}
    limitations = [
        "One NASA POWER grid cell of about 55 km around the site (MERRA-2 reanalysis): the regional climate trend, not the "
        "microclimate inside the development.",
        "Rainfall trends are the least robust: checked against ERA5, the two datasets agreed on the direction only about half the time.",
        "A trend describes the past; it is not a forecast of future climate.",
    ]
    return result(PARTIAL if warnings else COMPLETED, metrics=metrics, datasets=[ds_id], warnings=warnings, limitations=limitations, details=details)
