"""A1b long-term climate trends inside URBANOVA's own analysis (Space Apps 2026). Offline: NASA POWER is faked with the
Trend Detective's synthetic record (temperature +0.3 °C per decade, rain flat); the other network modules are stubs."""

import pytest

from conftest import make_project, run
from test_detective import power  # noqa: F401  (fixture)
from test_stage_s11 import site_data  # noqa: F401  (fixture)


@pytest.fixture()
def real_trends(monkeypatch, site_data, power):  # noqa: F811
    """Site stubs for the other network modules, but the real climate_trends module on fake POWER data."""
    from urbanova.analysis import climate_trends
    from urbanova.engine import core

    monkeypatch.setitem(core.MODULES, "climate_trends", climate_trends.run)
    return power


def _engine(tmp_path):
    from urbanova.engine import AnalysisResult, analyse

    res = analyse({"contract_version": "1.3", "site": {"latitude": 30.03, "longitude": 31.47}}, tmp_path)
    AnalysisResult.model_validate(res)
    return res["designs"][0]


def test_a_site_analysis_reports_the_trend_rate_range_and_verdict(env, tmp_path, real_trends):
    d = _engine(tmp_path)
    mod = d["modules"]["climate_trends"]
    assert mod["status"] == "completed" and mod["shared_across_designs"] is False
    m = {x["metric_name"]: x for x in d["metrics"] if x["module"] == "climate_trends"}
    assert set(m) == {f"trend_{v}_per_decade" for v in ("t2m_max", "t2m", "t2m_min", "precip", "rh", "sun")}
    tmax = m["trend_t2m_max_per_decade"]
    assert tmax["state"] == "available" and tmax["unit"] == "°C/decade" and 0.2 < tmax["value"] < 0.4
    assert tmax["uncertainty"]["ci95_low"] < tmax["value"] < tmax["uncertainty"]["ci95_high"]
    assert tmax["extra"]["verdict"] == "significant" and tmax["condition"] == "existing"
    rain = m["trend_precip_per_decade"]
    assert rain["extra"]["verdict"] != "significant" or abs(rain["value"]) < 0.05  # the fake rain has no trend
    rows = {r["id"]: r for r in mod["details"]["rows"]}
    assert rows["precip"]["notes"][-1].startswith("Rainfall trends differ between datasets")  # the ERA5 caution travels with the result
    assert any("not a forecast" in x for x in mod["limitations"])


def test_the_module_and_the_detective_give_identical_numbers(client, env, tmp_path, real_trends):
    d = _engine(tmp_path)
    row = next(r for r in d["modules"]["climate_trends"]["details"]["rows"] if r["id"] == "t2m_max")
    api = client.post("/api/v1/detective/trend", json={"lat": 30.03, "lon": 31.47, "variable": "t2m_max"}).json()["result"]
    assert row["per_decade"] == pytest.approx(api["rate"]["per_decade"])
    assert row["p_mann_kendall"] == pytest.approx(api["mann_kendall"]["p_value"]) and row["p_t_test"] == pytest.approx(api["ols"]["p_value"])
    assert row["verdict"]["level"] == api["verdict"]["level"]


def test_a_significant_warming_becomes_an_informational_finding(client, auth, real_trends):
    from urbanova import explain

    pid = make_project(client, auth)
    b = run(client, auth, pid, None, {})
    [f] = [f for f in b["findings"] if f.get("rule_id") == "TREND-WARM-01"]
    assert f["severity"] == "info" and f["classification"] == "environmental_indicator" and "per decade" in f["title"]
    assert f["metric_ids"] and "not a forecast" in " ".join(f["limitations"])
    a = explain.answer("Is it getting hotter here?", b)
    assert a["sufficient_evidence"] and "Trend in daytime high" in a["answer"] and "(significant)" in a["answer"]


def test_no_finding_without_a_significant_warming(env, tmp_path, monkeypatch, real_trends):
    from urbanova.providers import power_monthly as pm

    flat = pm.fetch

    def no_heat_trend(lat, lon, start_year=1981, end_year=None):
        data, meta = flat(lat, lon)
        first = {k: v[0][1] for k, v in data.items()}
        return {k: [(d, None if x is None else first[k] + (x - first[k]) * 0) for d, x in v] for k, v in data.items()}, meta

    monkeypatch.setattr(pm, "fetch", no_heat_trend)
    d = _engine(tmp_path)
    assert not [f for f in d["findings"] if f.get("rule_id") == "TREND-WARM-01"]


def test_a_power_outage_is_unavailable_never_a_crash(env, tmp_path, monkeypatch, real_trends):
    from urbanova.providers import power_monthly as pm
    from urbanova.providers.base import ProviderError

    def down(*a, **k):
        raise ProviderError("nasa_power_monthly", "timeout", "no answer")

    monkeypatch.setattr(pm, "fetch", down)
    d = _engine(tmp_path)
    assert d["modules"]["climate_trends"]["status"] == "unavailable" and d["modules"]["climate_trends"]["provider_failures"]
    assert d["status"] == "partially_succeeded"


def test_trends_are_site_level_and_registered_everywhere():
    from urbanova.engine import core
    from urbanova.engine.contract import ModuleName
    from urbanova.readiness import ANALYSES, CONDITION, RUNNABLE
    from typing import get_args

    assert "climate_trends" in core.SITE_LEVEL and "climate_trends" in RUNNABLE and "climate_trends" in get_args(ModuleName)
    assert CONDITION["climate_trends"] == "existing" and "1981" in ANALYSES["climate_trends"]
