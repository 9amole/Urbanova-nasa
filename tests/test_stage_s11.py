"""Stage S11: site analysis without design files — the land as it is, before any design. Offline: the network modules
are replaced by stubs that report completed results; sun position and heat stress run for real."""

import pytest

from conftest import make_project, run
from test_acceptance import baseline_with_plan
from test_stage_s2 import hot_day

SITE_METRICS = {"climate_baseline": ("climate_mean_annual_TX35_days", 67.0, "days/year"), "site_context": ("context_mapped_road_length_km", 2.0, "km"),
                "land_cover": ("landcover_site_bare_sparse_vegetation_pct", 100.0, "%"), "surface_heat": ("lst_site_median_c", 53.1, "°C"),
                "vegetation": ("ndvi_site_mean", 0.08, "NDVI")}


@pytest.fixture()
def site_data(monkeypatch):
    """Network modules answer as if the providers worked (climate also leaves the hot day for heat stress)."""
    from urbanova import values as V
    from urbanova.analysis.common import COMPLETED, result
    from urbanova.engine import core

    def stub(mod):
        name, value, unit = SITE_METRICS[mod]

        def run_(ctx):
            if mod == "climate_baseline":
                ctx.shared["hot_day"] = hot_day()
                ctx.shared["hottest_months"] = [6, 7, 8]
            return result(COMPLETED, metrics=[V.metric(name, name, value, unit, method="stub", condition=V.EXISTING)])
        return run_

    for mod in SITE_METRICS:
        monkeypatch.setitem(core.MODULES, mod, stub(mod))


def test_a_project_without_a_plan_gets_a_complete_site_analysis(client, auth, site_data):
    pid = make_project(client, auth)
    b = run(client, auth, pid, None, {})
    ms = b["run"]["module_status"]
    for mod in ("urban_form", "planning_compliance", "hotspots", "interventions"):
        assert ms[mod]["status"] == "skipped" and ms[mod]["site_mode"] and "design plan" in ms[mod]["reason"], mod
    assert ms["solar_shade"]["status"] == "completed" and ms["heat_stress"]["status"] in ("completed", "partial")
    assert b["run"]["status"] == "succeeded"  # a complete site analysis is not "partial" because there is no design
    names = {m["metric_name"]: m for m in b["metrics"]}
    for n in ("utci_sun_peak_c", "utci_shade_peak_c", "utci_sun_very_strong_hours", "solar_elevation_deg_1200"):
        assert names[n]["state"] == "available" and names[n]["condition"] == "existing", n
    assert names["utci_sun_peak_c"]["value"] > names["utci_shade_peak_c"]["value"]
    assert not any(m["condition"] == "design" and m["state"] == "available" for m in b["metrics"])  # no design figures
    assert names["solar_open_space_shade_hours"]["state"] == "not_applicable"
    assert b["hotspots"] == []


def test_site_mode_summaries_then_full_mode_once_a_design_exists(client, auth, site_data):
    pid = make_project(client, auth)
    empty = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()
    assert empty["state"] == "empty" and empty["can_analyse_site"] and "Analyse the site now" in empty["next_step"]
    r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": None, "config": {}})  # the one-click button's call
    assert r.status_code == 202, r.text
    site = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()
    assert site["state"] == "site" and site["run_id"] == r.json()["id"] and "design plan" in site["next_step"]
    tiles = {m["name"]: m for m in site["site"]}
    assert tiles["lst_site_median_c"]["value"] == 53.1 and tiles["utci_sun_peak_c"]["state"] == "available"
    d = client.get(f"/api/v1/projects/{pid}/project-dashboard", headers=auth).json()
    assert d["state"] == "site" and d["mode"] == "site" and d["designs"] is None and d["hotspots"] == [] and d["rules"] == []
    assert d["run"]["scenario"] == "Site analysis (no design)" and d["tiles"]["land_cover"]["value"] == 100.0
    assert {m["key"] for m in d["quality"]["modules"]}.isdisjoint({"urban_form", "hotspots", "interventions", "planning_compliance"})
    # A report of the site analysis says what it is.
    rep = client.post(f"/api/v1/analysis-runs/{r.json()['id']}/reports", headers=auth, json={}).json()
    html = client.get(f"/api/v1/reports/{rep['id']}/download?format=html", headers=auth).text
    assert "site analysis report" in html and "Complete site analysis" in html and "no design hotspot is reported" not in html
    # Once a design exists and is analysed, both summaries switch to the full analysis.
    base = baseline_with_plan(client, auth, pid)
    run(client, auth, pid, base["id"], {"modules": ["urban_form"]})
    assert client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()["state"] == "ready"
    full = client.get(f"/api/v1/projects/{pid}/project-dashboard", headers=auth).json()
    assert full["state"] == "ready" and full["mode"] == "design" and full["designs"]["designs"][0]["kind"] == "baseline"


def test_readiness_without_a_design_lets_heat_stress_and_shade_run(client, auth):
    from urbanova import db, readiness

    pid = make_project(client, auth)
    rd = {a["analysis"]: a for a in readiness.assess(db.get("projects", pid), None)["analyses"]}
    assert "Building, tree or shade-structure heights (for hourly shadows)" not in rd["heat_stress"]["missing_inputs"]
    assert rd["solar_shade"]["status"] == "ready"


def test_engine_without_designs_is_a_site_analysis(env, tmp_path, site_data):
    from urbanova.engine import AnalysisResult, analyse

    res = analyse({"contract_version": "1.2", "site": {"latitude": 30.12, "longitude": 31.85}}, tmp_path)
    AnalysisResult.model_validate(res)
    [d] = res["designs"]
    assert res["contract_version"] == "1.2" and d["analysis_mode"] == "site" and d["status"] == "succeeded"
    assert d["modules"]["hotspots"]["status"] == "skipped" and d["modules"]["hotspots"]["site_mode"]
    assert any(m["metric_name"] == "utci_sun_peak_c" and m["state"] == "available" for m in d["metrics"])


def test_help_knows_site_analysis(client):
    hits = client.get("/api/v1/support/faq?q=analyse a plot before I have a design").json()
    assert hits[0]["id"] == "faq_site_only"
    assert "Analyse this site now" in client.get("/api/v1/support/pages/overview").json()["steps"][1]
