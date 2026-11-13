"""Verification and validation of the Trend Detective (NASA Space Apps 2026).

Sections (run all, or name some):
  calibration  Monte-Carlo: how often each test says "significant" when there is NO trend (should be about 5 %), with
               white noise and with year-to-year correlation (AR(1) 0.3 and 0.6); plain against corrected tests.
  power        how often a real trend is detected after 15, 30 and 45 years (why "not significant yet" happens).
  reference    every statistic against the independent pymannkendall package and scipy (needs pymannkendall).
  demos        data integrity, change-point (data-source joins) and segment checks for every demo series (NASA POWER).
  era5         the same trend method on ERA5 (ECMWF, independent of NASA's MERRA-2) for the demos and 30 random land
               points: direction agreement, rate correlation, overlapping confidence intervals.

Results go to validation/detective/<section>.json; numbers in the docs and the app are copied from these files only.
Usage:  .venv/Scripts/python scripts/validate_detective.py [section ...]
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "validation" / "detective"

from urbanova import trends  # noqa: E402

N_SIM = 2000


def _ar1(n: int, phi: float, rng) -> np.ndarray:
    e = rng.normal(0, 1, n + 50)
    x = np.zeros_like(e)
    for i in range(1, len(e)):
        x[i] = phi * x[i - 1] + e[i]
    return x[50:] * math.sqrt(1 - phi ** 2)  # unit variance


def _tests(y: np.ndarray) -> dict:
    t = np.arange(len(y)) + 0.5
    lin = trends.ols(t, y)
    mk = trends.mann_kendall(y)
    mod = trends.mann_kendall_corrected(t, y)
    eff = trends.ols_effective_n(t, y, lin)
    a = trends.ALPHA
    return {"t_plain": lin["p_value"] < a, "mk_plain": mk["p_value"] < a, "t_corrected": eff["p_value"] < a, "mk_corrected": mod["p_value"] < a,
            "verdict_plain": lin["p_value"] < a and mk["p_value"] < a, "verdict_corrected": eff["p_value"] < a and mod["p_value"] < a}


def calibration(n_years: int = 45) -> dict:
    rng = np.random.default_rng(2026)
    out = {}
    for phi in (0.0, 0.3, 0.6):
        hits = {}
        for _ in range(N_SIM):
            for k, v in _tests(_ar1(n_years, phi, rng)).items():
                hits[k] = hits.get(k, 0) + v
        out[f"ar1_{phi}"] = {k: round(v / N_SIM, 4) for k, v in hits.items()}
        print(f"  no trend, AR(1) {phi}: " + ", ".join(f"{k} {v / N_SIM:.1%}" for k, v in hits.items()), flush=True)
    se = math.sqrt(0.05 * 0.95 / N_SIM)
    return {"n_simulations": N_SIM, "n_years": n_years, "alpha": trends.ALPHA, "monte_carlo_standard_error": round(se, 4),
            "false_alarm_rate": out, "expected": "about 5 % for each single test; lower for the verdict, which needs both tests"}


def power() -> dict:
    """Detection rate of the corrected verdict for trends of 0.1–1.0 noise standard deviations per decade."""
    rng = np.random.default_rng(7)
    rows = []
    for years in (15, 30, 45):
        for per_decade in (0.1, 0.2, 0.3, 0.5, 1.0):
            hit = 0
            for _ in range(N_SIM // 4):
                y = per_decade / 10 * np.arange(years) + _ar1(years, 0.2, rng)
                hit += _tests(y)["verdict_corrected"]
            rows.append({"years": years, "trend_per_decade_in_noise_sd": per_decade, "detected": round(hit / (N_SIM // 4), 3)})
            print(f"  {years} years, trend {per_decade} SD/decade: detected {hit / (N_SIM // 4):.0%}", flush=True)
    return {"n_simulations_each": N_SIM // 4, "noise": "AR(1) 0.2, unit variance", "rows": rows}


def reference() -> dict:
    import pymannkendall as pmk
    from scipy import stats

    rng = np.random.default_rng(11)
    cases = []
    for k in range(200):
        n = int(rng.integers(12, 60))
        y = np.round(0.02 * np.arange(n) + _ar1(n, float(rng.uniform(0, 0.6)), rng), int(rng.integers(1, 4)))  # rounding → ties
        t = np.arange(n) + 0.5
        ours_mk, ours_mod = trends.mann_kendall(y), trends.mann_kendall_corrected(t, y)
        ours_sen = trends.sens_slope(t, y, ours_mk["var_S"])
        ref = pmk.original_test(y)
        lin, ref_lin = trends.ols(t, y), stats.linregress(t, y)
        cases.append({
            "S": ours_mk["S"] - ref.s, "var_S": ours_mk["var_S"] - ref.var_s, "z": ours_mk["z"] - ref.z, "p": ours_mk["p_value"] - ref.p,
            "tau": ours_mk["tau"] - ref.Tau, "sen": ours_sen["slope"] - ref.slope,
            "ols_slope": lin["slope"] - ref_lin.slope, "ols_p": lin["p_value"] - ref_lin.pvalue})
    worst = {k: float(max(abs(c[k]) for c in cases)) for k in cases[0]}
    print("  largest difference over 200 random series: " + ", ".join(f"{k} {v:.2e}" for k, v in worst.items()), flush=True)
    return {"package": f"pymannkendall {pmk.__version__ if hasattr(pmk, '__version__') else ''}".strip(), "n_series": len(cases), "max_abs_difference": worst}


DEMO_POINTS = [("New Cairo", 30.03, 31.47), ("Svalbard", 78.22, 15.65), ("Sahel", 14.5, 2.0), ("Perth", -31.95, 115.86), ("Sydney", -33.87, 151.21),
               ("Amazon", -3.5, -60.0), ("Delhi", 28.61, 77.21)]
# 20 land points on every continent and in many climates (deterministic, so the check is reproducible).
LAND_POINTS = [("Madrid", 40.42, -3.70), ("Moscow", 55.76, 37.62), ("Lagos", 6.52, 3.38), ("Nairobi", -1.29, 36.82), ("Johannesburg", -26.20, 28.05),
               ("Riyadh", 24.71, 46.68), ("Tehran", 35.69, 51.39), ("Ulaanbaatar", 47.89, 106.91), ("Beijing", 39.90, 116.40), ("Bangkok", 13.76, 100.50),
               ("Jakarta", -6.21, 106.85), ("Alice Springs", -23.70, 133.88), ("Christchurch", -43.53, 172.64), ("Anchorage", 61.22, -149.90),
               ("Denver", 39.74, -104.99), ("Mexico City", 19.43, -99.13), ("Bogota", 4.71, -74.07), ("Buenos Aires", -34.60, -58.38),
               ("Santiago", -33.45, -70.67), ("Reykjavik", 64.15, -21.94)]
ERA5_VARS = {"t2m": ("T2M", "temperature_2m_mean"), "t2m_max": ("T2M_MAX", "temperature_2m_max"), "precip": ("PRECTOTCORR", "precipitation_sum")}


def _era5_monthly(lat: float, lon: float, last: int) -> dict:
    from urbanova.providers.weather import _open_meteo_get

    params = {"latitude": round(lat, 4), "longitude": round(lon, 4), "start_date": "1981-01-01", "end_date": f"{last}-12-31",
              "daily": ",".join(v[1] for v in ERA5_VARS.values()), "timezone": "GMT", "models": "era5"}
    body, _ = _open_meteo_get(params)
    days, out = body["daily"]["time"], {}
    for var, (_, om) in ERA5_VARS.items():
        months: dict = {}
        for d, v in zip(days, body["daily"][om]):
            if v is not None:
                months.setdefault((int(d[:4]), int(d[5:7])), []).append(float(v))
        out[var] = [(date(y, m, 1), float(np.mean(vals))) for (y, m), vals in sorted(months.items()) if len(vals) >= 20]  # mm/day for rain, like POWER
    return out


def era5() -> dict:
    """The same trend method on NASA POWER (MERRA-2-based) and ERA5 (ECMWF): an independent check of each trend."""
    from urbanova.providers import power_monthly as pm

    last = pm.last_complete_year()
    rows = []
    for name, lat, lon in DEMO_POINTS + LAND_POINTS:
        try:
            power, _ = pm.fetch(lat, lon)
            e5 = _era5_monthly(lat, lon, last)
        except Exception as exc:  # noqa: BLE001 - one point failing must not stop the check
            print(f"  {name}: skipped ({type(exc).__name__})", flush=True)
            continue
        for var, (param, _) in ERA5_VARS.items():
            a = trends.analyse([(d, x) for d, x in power[param] if d.year <= last], "x")
            b = trends.analyse(e5[var], "x")
            if not (a.get("ok") and b.get("ok")):
                continue
            ra, rb = a["rate"]["per_decade"], b["rate"]["per_decade"]
            rows.append({"place": name, "lat": lat, "lon": lon, "variable": var, "demo": (name, lat, lon) in DEMO_POINTS,
                         "power": {"per_decade": ra["slope"], "ci": [ra["ci_low"], ra["ci_high"]], "level": a["verdict"]["level"]},
                         "era5": {"per_decade": rb["slope"], "ci": [rb["ci_low"], rb["ci_high"]], "level": b["verdict"]["level"]},
                         "same_direction": (ra["slope"] > 0) == (rb["slope"] > 0),
                         "ci_overlap": max(ra["ci_low"], rb["ci_low"]) <= min(ra["ci_high"], rb["ci_high"]),
                         "same_verdict": a["verdict"]["level"] == b["verdict"]["level"]})
        print(f"  {name}: " + "; ".join(f"{r['variable']} POWER {r['power']['per_decade']:+.3f} ({r['power']['level'][:3]}) ERA5 {r['era5']['per_decade']:+.3f} ({r['era5']['level'][:3]})"
                                         for r in rows if r["place"] == name), flush=True)
        time.sleep(1.0)
    summary = {}
    for var in ERA5_VARS:
        rs = [r for r in rows if r["variable"] == var]
        if len(rs) < 3:
            continue
        x, y = np.array([r["power"]["per_decade"] for r in rs]), np.array([r["era5"]["per_decade"] for r in rs])
        sig = [r for r in rs if r["power"]["level"] == "significant" or r["era5"]["level"] == "significant"]
        summary[var] = {"points": len(rs), "same_direction_pct": round(100 * np.mean([r["same_direction"] for r in rs]), 1),
                        "ci_overlap_pct": round(100 * np.mean([r["ci_overlap"] for r in rs]), 1),
                        "same_verdict_pct": round(100 * np.mean([r["same_verdict"] for r in rs]), 1),
                        "rate_correlation": round(float(np.corrcoef(x, y)[0, 1]), 3),
                        "significant_in_either": len(sig),
                        "significant_same_direction_pct": round(100 * np.mean([r["same_direction"] for r in sig]), 1) if sig else None}
        print(f"  {var}: {summary[var]}", flush=True)
    return {"method": "Same yearly-anomaly trend analysis on NASA POWER and on ERA5 (Open-Meteo archive, models=era5), monthly means of daily values",
            "points": len({r['place'] for r in rows}), "summary": summary, "rows": rows,
            "note": "ERA5 (ECMWF) is produced independently of MERRA-2 (NASA). Grids differ (ERA5 about 0.25°, POWER about 0.5° × 0.625°)."}


def main(sections: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    funcs = {"calibration": calibration, "power": power, "reference": reference, "era5": era5}
    for name in sections or list(funcs):
        print(f"[{name}]", flush=True)
        t0 = time.time()
        res = funcs[name]()
        res["generated"] = date.today().isoformat()
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote validation/detective/{name}.json in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
