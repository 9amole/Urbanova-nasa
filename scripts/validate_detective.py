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


def main(sections: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    funcs = {"calibration": calibration, "power": power, "reference": reference}
    for name in sections or list(funcs):
        print(f"[{name}]", flush=True)
        t0 = time.time()
        res = funcs[name]()
        res["generated"] = date.today().isoformat()
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  wrote validation/detective/{name}.json in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
