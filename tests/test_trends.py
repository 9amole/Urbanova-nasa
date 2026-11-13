"""Trend engine (Space Apps 2026): known slopes are recovered, noise is not called a trend, gaps are skipped."""

from datetime import date

import numpy as np
import pytest
from scipy import stats


def _series(years=30, slope_per_year=0.03, noise=0.3, seasonal=8.0, seed=1, gaps=()):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(years * 12):
        y, m = 1990 + i // 12, i % 12 + 1
        d = date(y, m, 1)
        if (y, m) in gaps:
            out.append((d, None))
            continue
        t = y + (m - 0.5) / 12
        out.append((d, 20 + seasonal * np.sin(2 * np.pi * (m - 1) / 12) + slope_per_year * (t - 1990) + rng.normal(0, noise)))
    return out


def test_a_known_warming_trend_is_recovered_and_significant():
    from urbanova import trends

    r = trends.analyse(_series(), "°C", label="temperature", up_word="warming", down_word="cooling")
    assert r["ok"] and r["verdict"]["level"] == "significant" and r["verdict"]["direction"] == "warming"
    assert r["sen"]["per_decade"]["slope"] == pytest.approx(0.3, abs=0.05)  # 0.03 °C/yr = 0.3 °C per decade
    assert r["sen"]["per_decade"]["ci_low"] < 0.3 < r["sen"]["per_decade"]["ci_high"]
    assert r["ols"]["per_decade"]["ci_low"] < 0.3 < r["ols"]["per_decade"]["ci_high"]
    assert r["mann_kendall"]["p_value"] < 0.001 and r["ols"]["p_value"] < 0.001
    assert r["verdict"]["headline"] == "Yes — warming." and r["verdict"]["rate"].startswith("+0.") and r["rate"]["method"] == "sen"
    assert r["basis"] == "annual" and r["period"]["years"] == 30 and r["verdict"]["total"].startswith("+")


def test_pure_noise_is_not_called_significant():
    from urbanova import trends

    hits = sum(trends.analyse(_series(slope_per_year=0.0, seed=s), "mm/day")["verdict"]["level"] == "significant" for s in range(20))
    assert hits <= 2  # both tests at 5 % → rarely both significant by chance


def test_the_seasonal_cycle_changes_the_chart_not_the_verdict_and_seasons_are_tested_separately():
    from urbanova import trends

    s = _series(slope_per_year=0.01, noise=0.2, seasonal=12.0, years=20)
    raw, anom = trends.analyse(s, "°C", deseason=False), trends.analyse(s, "°C", deseason=True)
    assert raw["ols"]["p_value"] == pytest.approx(anom["ols"]["p_value"]) and raw["verdict"]["level"] == anom["verdict"]["level"]  # yearly basis
    assert max(abs(p["value"]) for p in raw["series"]) > 10 > max(abs(p["value"]) for p in anom["series"])  # the chart differs
    # only July warms: the seasonal view finds July and no other month
    rng = np.random.default_rng(5)
    jul = [(date(1990 + i // 12, i % 12 + 1, 1), 20 + (0.1 * (i // 12) if i % 12 == 6 else 0) + rng.normal(0, 0.2)) for i in range(25 * 12)]
    seas = {m["name"]: m for m in trends.analyse(jul, "°C")["seasons"]}
    assert seas["Jul"]["significant"] and seas["Jul"]["per_decade"] == pytest.approx(1.0, abs=0.2)
    assert sum(m["significant"] for m in seas.values()) <= 2


def test_missing_months_are_skipped_not_zero_and_short_records_are_refused():
    from urbanova import trends

    gaps = {(1995, m) for m in range(1, 13)}
    r = trends.analyse(_series(gaps=gaps), "°C")
    assert r["ok"] and r["period"]["missing_months"] == 12 and r["period"]["months"] == 30 * 12 - 12
    assert all(p["date"][:4] != "1995" for p in r["raw"]) and any("never counted as 0" in n for n in r["notes"])
    short = trends.analyse(_series(years=6), "°C")
    assert not short["ok"] and "at least 10 years" in short["reason"]


def test_statistics_match_independent_calculations():
    from urbanova import trends

    r = trends.analyse(_series(years=15, seed=7), "°C")
    t = np.array([a["year"] + 0.5 for a in r["annual"]])  # the tests run on yearly mean anomalies
    y = np.array([a["value"] for a in r["annual"]])
    ref = stats.linregress(t, y)
    assert r["ols"]["slope"] == pytest.approx(ref.slope, rel=1e-3) and r["ols"]["p_value_plain"] == pytest.approx(ref.pvalue, rel=1e-2, abs=1e-12)
    # Mann-Kendall S by brute force, and Kendall's tau from scipy agree in sign and size
    s = sum(np.sign(y[j] - y[i]) for i in range(len(y)) for j in range(i + 1, len(y)))
    mk = trends.mann_kendall(y)  # the listed values are rounded to 4 decimals, so test on exactly these numbers
    assert mk["S"] == int(s) == r["mann_kendall"]["S"]
    tau, p = stats.kendalltau(t, y)
    assert mk["tau"] == pytest.approx(tau, abs=0.01) and mk["p_value"] == pytest.approx(p, abs=0.01)
    # Sen's slope = median of pairwise slopes
    sl = [(y[j] - y[i]) / (t[j] - t[i]) for i in range(len(y)) for j in range(i + 1, len(y))]
    assert trends.sens_slope(t, y, mk["var_S"])["slope"] == pytest.approx(float(np.median(sl)), rel=1e-6)
    assert r["sen"]["slope"] == pytest.approx(float(np.median(sl)), rel=1e-2)
    assert r["monthly_tests"]["n"] == 15 * 12 and 0 <= r["monthly_tests"]["mk_p"] <= 1  # the monthly view, for comparison


def test_mostly_repeated_values_fall_back_to_the_least_squares_rate():
    """Found live: rainless or rounded values repeat, so Sen's slope can be exactly 0 although a trend exists."""
    from urbanova import trends

    # 30 years: 20 completely dry years and 10 wet years that get wetter, so most yearly means are identical
    wet = {1992, 1995, 1998, 2001, 2004, 2007, 2010, 2013, 2016, 2019}
    s = [(date(1990 + i // 12, i % 12 + 1, 1), (0.5 + 0.05 * (i // 12) if 1990 + i // 12 in wet else 0.0)) for i in range(30 * 12)]
    r = trends.analyse(s, "mm/day", up_word="getting wetter", down_word="getting drier")
    assert r["sen"]["slope"] == 0 and r["rate"]["method"] == "ols" and r["rate"]["per_decade"]["slope"] > 0
    assert "Sen's slope is 0" in " ".join(r["notes"]) and r["verdict"]["rate"].startswith("+")


def test_corrected_tests_keep_false_alarms_near_five_percent():
    """Monte-Carlo (small version of scripts/validate_detective.py): no trend, strongly correlated years (AR(1) 0.6)."""
    import math

    from urbanova import trends

    rng = np.random.default_rng(42)
    plain = corrected = 0
    n_sim = 400
    for _ in range(n_sim):
        e = rng.normal(0, 1, 95)
        x = np.zeros(95)
        for i in range(1, 95):
            x[i] = 0.6 * x[i - 1] + e[i]
        y = x[50:]
        t = np.arange(45) + 0.5
        lin = trends.ols(t, y)
        plain += lin["p_value"] < 0.05 and trends.mann_kendall(y)["p_value"] < 0.05
        corrected += trends.ols_effective_n(t, y, lin)["p_value"] < 0.05 and trends.mann_kendall_corrected(t, y)["p_value"] < 0.05
    assert plain / n_sim > 0.15 and corrected / n_sim < 0.10 + 2 * math.sqrt(0.1 * 0.9 / n_sim)


def test_pettitt_finds_a_shift_and_bh_controls_false_discoveries():
    from urbanova import trends

    rng = np.random.default_rng(3)
    y = np.concatenate([rng.normal(0, 1, 20), rng.normal(3, 1, 25)])
    cp = trends.pettitt(y)
    assert 17 <= cp["index"] <= 21 and cp["p_value"] < 0.01
    assert trends.benjamini_hochberg([0.001, 0.2, 0.03, 0.04], q=0.10) == [True, False, True, True]
    assert trends.benjamini_hochberg([0.5, 0.6]) == [False, False] and trends.benjamini_hochberg([]) == []


def test_statistics_match_the_pymannkendall_reference():
    """Independent reference implementation (pymannkendall 1.4.3): identical S, Var(S), z, p, tau and Sen's slope, ties included."""
    pmk = pytest.importorskip("pymannkendall")
    from urbanova import trends

    rng = np.random.default_rng(8)
    for _ in range(30):
        n = int(rng.integers(12, 50))
        y = np.round(0.02 * np.arange(n) + rng.normal(0, 1, n), 1)
        ours, ref = trends.mann_kendall(y), pmk.original_test(y)
        sen = trends.sens_slope(np.arange(n) + 0.5, y, ours["var_S"])
        assert ours["S"] == ref.s and ours["var_S"] == pytest.approx(ref.var_s) and ours["p_value"] == pytest.approx(ref.p, abs=1e-12)
        assert ours["tau"] == pytest.approx(ref.Tau) and sen["slope"] == pytest.approx(ref.slope)
