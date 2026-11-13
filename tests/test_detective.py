"""Trend Detective API (Space Apps 2026), offline: NASA POWER is faked with a synthetic 1981–2025 record."""

from datetime import date

import numpy as np
import pytest


@pytest.fixture()
def power(monkeypatch):
    """Every catalog parameter: a seasonal cycle, noise and a known trend (temperature +0.03 °C/yr, rain flat)."""
    from urbanova.providers import power_monthly as pm

    calls = []

    def fake(lat, lon, start_year=1981, end_year=None):
        calls.append((lat, lon))
        rng = np.random.default_rng(int(abs(lat * 100 + lon)))
        out = {}
        for v in pm.catalog()["variables"]:
            slope = 0.03 if v["interest"] == "heat" else 0.0
            rows = []
            for i in range(45 * 12):
                y, m = 1981 + i // 12, i % 12 + 1
                missing = v["id"] in ("sun", "cloud") and y < 1984
                rows.append((date(y, m, 1), None if missing else 20 + 6 * np.sin(m / 12 * 2 * np.pi) + slope * (y - 1981) + rng.normal(0, 0.4)))
            out[v["power"]] = rows
        return out, {"cache_hit": False}

    monkeypatch.setattr(pm, "fetch", fake)
    return calls


def test_catalog_is_public_and_explains_every_variable(client):
    c = client.get("/api/v1/detective/catalog").json()
    assert len(c["variables"]) >= 12 and len(c["regions"]) >= 5 and c["last_year"] >= 2025
    for v in c["variables"]:
        assert v["definition"] and v["why"] and v["unit"] and v["model"] and "power" not in v
    assert "55 km" in c["source"]["resolution"] and "NASA" in c["source"]["attribution"]


def test_trend_endpoint_finds_the_warming_and_not_the_flat_rain(client, power):
    r = client.post("/api/v1/detective/trend", json={"lat": 30.031, "lon": 31.474, "variable": "t2m"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["point"] == {"lat": 30.03, "lon": 31.47} and j["result"]["verdict"]["level"] == "significant"
    assert j["result"]["rate"]["per_decade"]["slope"] == pytest.approx(0.3, abs=0.06)
    assert j["source"]["parameter"] == "T2M" and "MERRA-2" in j["source"]["model"]
    rain = client.post("/api/v1/detective/trend", json={"lat": 30.03, "lon": 31.47, "variable": "precip"}).json()["result"]
    assert rain["verdict"]["level"] != "significant"
    # missing early solar months are skipped, never zero
    sun = client.post("/api/v1/detective/trend", json={"lat": 30.03, "lon": 31.47, "variable": "sun"}).json()["result"]
    assert sun["period"]["start"] == "1984-01" and sun["period"]["missing_months"] == 36
    # a shorter period and the raw series
    short = client.post("/api/v1/detective/trend", json={"lat": 30.03, "lon": 31.47, "variable": "t2m", "start_year": 2000, "end_year": 2020,
                                                         "deseasonalise": False}).json()
    assert short["result"]["period"]["start"] == "2000-01" and short["result"]["period"]["end"] == "2020-12" and not short["result"]["deseasonalised"]


@pytest.mark.parametrize("body, msg", [({"lat": 95, "lon": 0, "variable": "t2m"}, "Latitude"), ({"lat": 1, "lon": 1, "variable": "nope"}, "Unknown variable"),
                                       ({"lat": 1, "lon": 1, "variable": "t2m", "start_year": 1970}, "between 1981"),
                                       ({"lat": "x", "lon": 1, "variable": "t2m"}, "latitude")])
def test_bad_requests_get_a_clear_message(client, power, body, msg):
    r = client.post("/api/v1/detective/trend", json=body)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_too_short_a_period_is_explained_not_guessed(client, power):
    j = client.post("/api/v1/detective/trend", json={"lat": 1, "lon": 1, "variable": "t2m", "start_year": 2018, "end_year": 2024}).json()
    assert j["result"]["ok"] is False and "at least 10 years" in j["result"]["reason"]


def test_suggestions_use_only_catalog_variables(client, env, monkeypatch):
    from urbanova import detective

    rules = client.post("/api/v1/detective/suggest", json={"place": "city", "interest": "heat"}).json()  # offline → rules
    assert rules["by"] == "rules" and [s["id"] for s in rules["suggestions"]][:3] == ["t2m", "t2m_max", "t2m_min"]
    assert client.post("/api/v1/detective/suggest", json={"place": "moon", "interest": "heat"}).status_code == 422
    # AI answer: unknown ids are dropped, duplicates removed
    real_ai = detective._ai_suggest
    monkeypatch.setattr(detective, "_ai_suggest", lambda *a: None)
    assert detective.suggest("desert", "water", "Sahara")["by"] == "rules"

    from urbanova.ai import llm

    monkeypatch.setattr(llm, "available", lambda **k: [{"name": "groq", "id": "groq:x", "model": "x"}])
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: '{"suggestions": [{"id": "precip", "reason": "Look for drying."}, {"id": "made_up", "reason": "x"}, '
                                                          '{"id": "precip", "reason": "dup"}, {"id": "soil_root", "reason": "Check soils."}]}')
    got = real_ai("desert", "water", "Sahara", {v["id"]: v for v in detective.pm.catalog()["variables"]})
    assert [g["id"] for g in got] == ["precip", "soil_root"]


def test_demo_regions_point_at_catalog_variables():
    from urbanova import detective
    from urbanova.providers import power_monthly as pm

    for r in detective.REGIONS:
        assert pm.variable(r["variable"]) and -90 <= r["lat"] <= 90 and r["story"]


def test_detective_is_the_public_landing_page_and_the_help_assistant_knows_it(client):
    js = client.get("/app.js").text
    assert '(state.token ? "#/projects" : "#/detective")' in js and 'parts[0] === "detective") return await viewDetective(parts)' in js
    assert '<script src="detective.js' in client.get("/").text and 'href="#/detective"' in client.get("/").text
    assert client.get("/api/v1/support/pages/detective").json()["title"] == "Trend Detective"
    assert client.get("/api/v1/support/faq?q=what does a significant trend mean").json()[0]["id"] == "faq_trend_significant"
    from urbanova.ai.support import LINK

    assert LINK.match("#/detective")


def test_trend_map_counts_where_it_changes(client, monkeypatch):
    """Where is it changing? Two cells warm, one cools, one is flat: the summary counts them by significant direction."""
    from urbanova.providers import power_monthly as pm

    def fake_region(lat, lon, parameter, half=2.5):
        cells = []
        for k, (dlat, slope) in enumerate([(0.0, 0.05), (0.5, 0.04), (1.0, -0.05), (1.5, 0.0)]):
            rng = np.random.default_rng(k)
            rows = [(date(1981 + i // 12, i % 12 + 1, 1), 10 + slope * (i // 12) + rng.normal(0, 0.1)) for i in range(45 * 12)]
            cells.append({"lat": lat + dlat, "lon": lon, "series": rows})
        return cells, {}

    monkeypatch.setattr(pm, "fetch_region", fake_region)
    m = client.post("/api/v1/detective/map", json={"lat": 10, "lon": 20, "variable": "t2m"}).json()
    assert len(m["cells"]) == 4 and m["counts"] == {"up": 2, "down": 1, "unclear": 1}
    assert "2 show a significant trend towards warming, 1 towards cooling" in m["summary"] and m["cell_size"]["lat"] == 0.5
    assert client.post("/api/v1/detective/map", json={"lat": 10, "lon": 20, "variable": "nope"}).status_code == 422


def test_soil_wetness_is_shown_in_percent(client, power):
    j = client.post("/api/v1/detective/trend", json={"lat": 30.03, "lon": 31.47, "variable": "soil_root"}).json()
    assert j["variable"]["unit"] == "%" and "scale" not in j["variable"]
    assert 1000 < abs(j["result"]["raw"][0]["value"]) < 3000  # the fake 0–1-style value of about 20, scaled ×100


def test_the_opposite_rain_demo_has_two_places():
    from urbanova import detective

    pair = next(r for r in detective.REGIONS if r.get("pair"))
    assert pair["variable"] == "precip" and pair["pair"]["lat"] < 0 < pair["lat"]
