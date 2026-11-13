"""Trend detection for monthly Earth-science time series (NASA Space Apps 2026, "Earth System Trend Detective").

Given a monthly series with gaps, it reports:
- the series without its seasonal cycle (each calendar month's mean removed: "anomalies");
- a least-squares slope with its t-test p-value and 95 % confidence interval;
- the Mann-Kendall test (no distribution assumption; ties handled), with Sen's slope and its 95 % CI;
- the lag-1 autocorrelation of the residuals, which, when strong, makes both tests over-confident;
- a plain-language verdict for students.

Missing months are skipped, never treated as 0. A trend needs at least MIN_YEARS of data.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
from scipy import stats

MIN_YEARS = 10
ALPHA = 0.05


def decimal_year(d: date) -> float:
    """Mid-month as a decimal year (e.g. 2001-07 → 2001.54)."""
    return d.year + (d.month - 0.5) / 12


def deseasonalise(series: list[tuple[date, float]]) -> tuple[list[tuple[date, float]], dict[int, float]]:
    """Subtract each calendar month's mean over the record: the anomaly series and the 12 monthly means."""
    by_month: dict[int, list[float]] = {}
    for d, v in series:
        by_month.setdefault(d.month, []).append(v)
    clim = {m: float(np.mean(v)) for m, v in by_month.items()}
    return [(d, v - clim[d.month]) for d, v in series], clim


def annual_means(series: list[tuple[date, float]], min_months: int = 10) -> list[tuple[int, float]]:
    """Calendar-year means of years with at least `min_months` values (a partial year would bias the mean)."""
    years: dict[int, list[float]] = {}
    for d, v in series:
        years.setdefault(d.year, []).append(v)
    return [(y, float(np.mean(v))) for y, v in sorted(years.items()) if len(v) >= min_months]


def mann_kendall(y: np.ndarray) -> dict:
    """Mann-Kendall trend test (normal approximation with the tie correction). S > 0 means an upward trend."""
    n = len(y)
    s = 0
    for k in range(n - 1):
        s += int(np.sum(np.sign(y[k + 1:] - y[k])))
    _, counts = np.unique(y, return_counts=True)
    ties = counts[counts > 1]
    var_s = (n * (n - 1) * (2 * n + 5) - float(np.sum(ties * (ties - 1) * (2 * ties + 5)))) / 18.0
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    tau = s / (0.5 * n * (n - 1))
    return {"S": int(s), "var_S": var_s, "z": float(z), "p_value": float(p), "tau": float(tau)}


def sens_slope(t: np.ndarray, y: np.ndarray, var_s: float, alpha: float = ALPHA) -> dict:
    """Theil-Sen slope (median of all pairwise slopes) with its confidence interval from the Mann-Kendall variance."""
    i, j = np.triu_indices(len(t), k=1)
    dt = t[j] - t[i]
    ok = dt != 0
    slopes = np.sort((y[j] - y[i])[ok] / dt[ok])
    m = len(slopes)
    c = stats.norm.ppf(1 - alpha / 2) * math.sqrt(var_s)
    lo_i, hi_i = int(round((m - c) / 2)) - 1, int(round((m + c) / 2))
    lo_i, hi_i = max(0, min(m - 1, lo_i)), max(0, min(m - 1, hi_i))
    return {"slope": float(np.median(slopes)), "ci_low": float(slopes[lo_i]), "ci_high": float(slopes[hi_i])}


def ols(t: np.ndarray, y: np.ndarray, alpha: float = ALPHA) -> dict:
    """Least-squares slope, intercept, t-test p-value and confidence interval of the slope."""
    r = stats.linregress(t, y)
    df = len(t) - 2
    tcrit = stats.t.ppf(1 - alpha / 2, df)
    resid = y - (r.intercept + r.slope * t)
    lag1 = float(np.corrcoef(resid[:-1], resid[1:])[0, 1]) if len(resid) > 2 and np.std(resid) > 0 else 0.0
    return {"slope": float(r.slope), "intercept": float(r.intercept), "p_value": float(r.pvalue), "r2": float(r.rvalue ** 2),
            "ci_low": float(r.slope - tcrit * r.stderr), "ci_high": float(r.slope + tcrit * r.stderr), "stderr": float(r.stderr),
            "lag1_autocorrelation": lag1}


def analyse(series: list[tuple[date, float | None]], unit: str, *, deseason: bool = True, label: str = "the value",
            up_word: str = "increasing", down_word: str = "decreasing") -> dict:
    """Full trend analysis of a monthly series. `series` may contain None (missing): those months are skipped."""
    clean = sorted((d, float(v)) for d, v in series if v is not None and not (isinstance(v, float) and math.isnan(v)))
    if not clean:
        return {"ok": False, "reason": "No valid values in this period."}
    years = clean[-1][0].year - clean[0][0].year + 1
    if years < MIN_YEARS or len(clean) < MIN_YEARS * 8:
        return {"ok": False, "reason": f"A trend needs at least {MIN_YEARS} years of data; this record has {years} year(s) with {len(clean)} valid months."}
    anomalies, clim = deseasonalise(clean)
    used = anomalies if deseason else clean  # what the chart shows
    # The tests run on YEARLY mean anomalies: neighbouring months are strongly correlated and many monthly values repeat
    # (rainless months, rounded soil wetness), which makes monthly tests over-confident. Yearly means are the standard basis.
    ann = annual_means(anomalies)
    dropped = years - len(ann)
    if len(ann) < MIN_YEARS:
        return {"ok": False, "reason": f"A trend needs at least {MIN_YEARS} complete years; this record has {len(ann)}."}
    t = np.array([yr + 0.5 for yr, _ in ann])
    y = np.array([v for _, v in ann])
    lin = ols(t, y)
    mk = mann_kendall(y)
    sen = sens_slope(t, y, mk["var_S"])
    span = float(ann[-1][0] - ann[0][0])
    ols_decade = {k: lin[k] * 10 for k in ("slope", "ci_low", "ci_high")}
    # Sen's slope is the headline rate (robust to outliers). If most yearly values repeat, the median pairwise slope can be
    # exactly 0 even though a trend exists, so the least-squares rate is reported instead.
    rate_method = "sen"
    per_decade = {k: sen[k] * 10 for k in ("slope", "ci_low", "ci_high")}
    if sen["slope"] == 0 and lin["slope"] != 0:
        rate_method, per_decade = "ols", dict(ols_decade)
    sig_mk, sig_t = mk["p_value"] < ALPHA, lin["p_value"] < ALPHA
    verdict = _verdict(per_decade, per_decade["slope"] / 10 * span, unit, mk["p_value"], lin["p_value"], sig_mk, sig_t, up_word, down_word, ann[0][0])
    # Monthly tests, for comparison only (scientists' view).
    tm = np.array([decimal_year(d) for d, _ in anomalies])
    ym = np.array([v for _, v in anomalies])
    mon_lin, mon_mk = ols(tm, ym), mann_kendall(ym)
    notes = []
    if rate_method == "ols":
        notes.append("Many years share the same value, so the robust Sen's slope is 0; the least-squares rate is shown instead.")
    if lin["lag1_autocorrelation"] > 0.3:
        notes.append(f"Neighbouring years are correlated (lag-1 autocorrelation {lin['lag1_autocorrelation']:.2f}), so the tests are somewhat "
                     "over-confident; treat a p-value near 0.05 with caution.")
    missing = sum(1 for _, v in series if v is None)
    if missing:
        notes.append(f"{missing} month(s) had no data and were skipped (never counted as 0).")
    if dropped > 0:
        notes.append(f"{dropped} year(s) with fewer than 10 months of data were left out of the yearly averages.")
    return {
        "ok": True, "unit": unit, "deseasonalised": deseason, "basis": "annual",
        "period": {"start": clean[0][0].isoformat()[:7], "end": clean[-1][0].isoformat()[:7], "first_year": ann[0][0], "last_year": ann[-1][0],
                   "years": len(ann), "months": len(clean), "missing_months": missing},
        "series": [{"date": d.isoformat()[:7], "value": round(v, 4)} for d, v in used],
        "raw": [{"date": d.isoformat()[:7], "value": round(v, 4)} for d, v in clean],
        "climatology": {str(m): round(v, 4) for m, v in sorted(clim.items())},
        "annual": [{"year": yr, "value": round(v, 4)} for yr, v in ann],
        "annual_raw": [{"year": yr, "value": round(v, 4)} for yr, v in annual_means(clean)],
        "ols": {**lin, "per_decade": ols_decade, "significant": sig_t},
        "mann_kendall": {**mk, "significant": sig_mk},
        "sen": {**sen, "per_decade": {k: sen[k] * 10 for k in ("slope", "ci_low", "ci_high")}, "total_change": sen["slope"] * span},
        "rate": {"method": rate_method, "per_decade": per_decade, "total_change": per_decade["slope"] / 10 * span},
        "monthly_tests": {"ols_p": mon_lin["p_value"], "mk_p": mon_mk["p_value"], "lag1_autocorrelation": mon_lin["lag1_autocorrelation"],
                          "n": len(ym), "ols_per_decade": mon_lin["slope"] * 10},
        "seasons": seasonal_trends(clean),
        "trend_line": {"t0": float(t[0]), "t1": float(t[-1]), "y0": lin["intercept"] + lin["slope"] * t[0], "y1": lin["intercept"] + lin["slope"] * t[-1],
                       "ci_low_slope": lin["ci_low"], "ci_high_slope": lin["ci_high"], "t_mean": float(np.mean(t)), "y_mean": float(np.mean(y)),
                       "offset": 0.0 if deseason else float(np.mean([v for _, v in clean]))},
        "verdict": verdict, "notes": notes, "alpha": ALPHA,
    }


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def seasonal_trends(clean: list[tuple[date, float]]) -> list[dict]:
    """The trend of each calendar month across the years ("are summers warming faster than winters?")."""
    out = []
    for m in range(1, 13):
        pts = [(d.year + 0.5, v) for d, v in clean if d.month == m]
        if len(pts) < MIN_YEARS:
            continue
        tt, yy = np.array([a for a, _ in pts]), np.array([b for _, b in pts])
        if np.std(yy) == 0:
            out.append({"month": m, "name": MONTHS[m - 1], "per_decade": 0.0, "p_value": 1.0, "significant": False})
            continue
        r = stats.linregress(tt, yy)
        out.append({"month": m, "name": MONTHS[m - 1], "per_decade": float(r.slope * 10), "p_value": float(r.pvalue), "significant": bool(r.pvalue < ALPHA)})
    return out


def _fmt(x: float) -> str:
    """Two significant figures for small numbers (0.0042), two decimals otherwise."""
    a = abs(x)
    if a == 0:
        return "0"
    if a < 0.1:
        return f"{x:.2g}" if a >= 1e-4 else f"{x:.1e}"
    return f"{x:.2f}" if a < 10 else f"{x:.1f}"


def _signed(x: float) -> str:
    return ("+" if x > 0 else "") + _fmt(x)


def _verdict(per_decade, total, unit, p_mk, p_t, sig_mk, sig_t, up_word, down_word, y0) -> dict:
    direction = up_word if per_decade["slope"] > 0 else down_word if per_decade["slope"] < 0 else "no change"
    rate = f"{_signed(per_decade['slope'])} {unit} per decade"
    rng = f"95 % range {_signed(per_decade['ci_low'])} to {_signed(per_decade['ci_high'])}"
    tests = f"Mann-Kendall {_p(p_mk)} · t-test {_p(p_t)}"
    if sig_mk and sig_t:
        level, headline = "significant", f"Yes — {direction}."
        why = "Both tests say this is very unlikely to be chance."
    elif sig_mk or sig_t:
        level, headline = "borderline", f"Probably — likely {direction}."
        why = "One test says significant, the other does not. More years of data would settle it."
    else:
        level, headline = "not_significant", f"Not yet — a hint of {direction}, but not significant."
        why = "The ups and downs from year to year are large compared with the trend. A longer record might make it significant."
    return {"level": level, "direction": direction, "headline": headline, "rate": rate, "range": rng, "tests": tests, "why": why,
            "total": f"{_signed(total)} {unit}", "total_text": f"about {_signed(total)} {unit} since {y0}"}


def _p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def quick(series: list[tuple[date, float | None]]) -> dict | None:
    """The yearly-basis trend of one series, without the chart data: for maps of many cells."""
    clean = sorted((d, float(v)) for d, v in series if v is not None)
    if not clean:
        return None
    anomalies, _ = deseasonalise(clean)
    ann = annual_means(anomalies)
    if len(ann) < MIN_YEARS:
        return None
    t = np.array([yr + 0.5 for yr, _ in ann])
    y = np.array([v for _, v in ann])
    if np.std(y) == 0:
        return {"per_decade": 0.0, "level": "not_significant", "p_t": 1.0, "p_mk": 1.0}
    lin, mk = ols(t, y), mann_kendall(y)
    sen = sens_slope(t, y, mk["var_S"])
    rate = sen["slope"] if sen["slope"] != 0 else lin["slope"]
    sig_t, sig_mk = lin["p_value"] < ALPHA, mk["p_value"] < ALPHA
    level = "significant" if sig_t and sig_mk else "borderline" if sig_t or sig_mk else "not_significant"
    return {"per_decade": float(rate * 10), "level": level, "p_t": lin["p_value"], "p_mk": mk["p_value"]}
