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
    assert "0.3" in r["verdict"]["headline"] and "per decade" in r["verdict"]["headline"] and r["rate"]["method"] == "sen"


def test_pure_noise_is_not_called_significant():
    from urbanova import trends

    hits = sum(trends.analyse(_series(slope_per_year=0.0, seed=s), "mm/day")["verdict"]["level"] == "significant" for s in range(20))
    assert hits <= 2  # both tests at 5 % → rarely both significant by chance


def test_the_seasonal_cycle_hides_a_trend_until_it_is_removed():
    from urbanova import trends

    s = _series(slope_per_year=0.01, noise=0.2, seasonal=12.0, years=20)
    with_cycle = trends.analyse(s, "°C", deseason=False)
    without = trends.analyse(s, "°C", deseason=True)
    assert without["ols"]["p_value"] < with_cycle["ols"]["p_value"] and without["verdict"]["level"] == "significant"
    assert any("seasonal cycle was not removed" in n for n in with_cycle["notes"])


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
    t = np.array([int(p["date"][:4]) + (int(p["date"][5:]) - 0.5) / 12 for p in r["series"]])
    y = np.array([p["value"] for p in r["series"]])
    ref = stats.linregress(t, y)
    assert r["ols"]["slope"] == pytest.approx(ref.slope, rel=1e-3) and r["ols"]["p_value"] == pytest.approx(ref.pvalue, rel=1e-2, abs=1e-12)
    # Mann-Kendall S by brute force, and Kendall's tau from scipy agree in sign and size
    s = sum(np.sign(y[j] - y[i]) for i in range(len(y)) for j in range(i + 1, len(y)))
    mk = trends.mann_kendall(y)  # the listed values are rounded to 4 decimals, so test on exactly these numbers
    assert mk["S"] == int(s) and abs(mk["S"] - r["mann_kendall"]["S"]) <= 10
    tau, p = stats.kendalltau(t, y)
    assert mk["tau"] == pytest.approx(tau, abs=0.01) and mk["p_value"] == pytest.approx(p, abs=0.01)
    # Sen's slope = median of pairwise slopes
    sl = [(y[j] - y[i]) / (t[j] - t[i]) for i in range(len(y)) for j in range(i + 1, len(y))]
    assert trends.sens_slope(t, y, mk["var_S"])["slope"] == pytest.approx(float(np.median(sl)), rel=1e-6)
    assert r["sen"]["slope"] == pytest.approx(float(np.median(sl)), rel=1e-3)


def test_mostly_repeated_values_fall_back_to_the_least_squares_rate():
    """Found live: desert rainfall has many rainless months, so Sen's slope is exactly 0 although a trend exists."""
    from urbanova import trends

    rng = np.random.default_rng(3)
    s = [(date(1990 + i // 12, i % 12 + 1, 1), (0.0 if rng.random() < 0.7 else 0.05 + 0.004 * (i / 12))) for i in range(30 * 12)]
    r = trends.analyse(s, "mm/day", deseason=False, label="rainfall", up_word="getting wetter", down_word="getting drier")
    assert r["sen"]["slope"] == 0 and r["rate"]["method"] == "ols" and r["rate"]["per_decade"]["slope"] > 0
    assert "Sen's slope is 0" in " ".join(r["notes"]) and "0 mm/day" not in r["verdict"]["headline"]
