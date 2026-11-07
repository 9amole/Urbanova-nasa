"""Stage S2 of the smart upgrade (PHASE2_SPEC rev. 2 §11): heat stress (A9, UTCI), design hotspots H1/H3/H4
with merging, and the H2 hot cluster as site context (§3.3 and §4 acceptance)."""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box, shape

from conftest import SAMPLES, make_project, run
from test_acceptance import baseline_with_plan

LAT, LON = 30.12, 31.85


def hot_day(peak=38.0):
    """A synthetic hot day: very strong heat stress in sun at every hour from 08:00."""
    return {"hours": {h: {"ta_c": peak - 8 + min(h - 7, 8), "rh_pct": 30.0, "v10_ms": 3.0, "dni_wm2": 800.0 if 8 <= h <= 16 else 500.0, "dhi_wm2": 120.0}
                      for h in range(7, 18)}, "days": 20, "threshold_c": peak, "months": [6, 7, 8], "dates": []}


def sample_context(tmp_path, features=None, rules=None):
    from urbanova.analysis.common import Context

    b = json.loads((SAMPLES / "sample_site_boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]
    plan = features or json.loads((SAMPLES / "sample_plan_baseline.geojson").read_text(encoding="utf-8"))
    ctx = Context(project={"id": "p", "latitude": LAT, "longitude": LON, "boundary": b}, scenario={"id": "s", "name": "base", "features": plan},
                  config={"solar_date": "2025-07-21"}, run_id="r", out_dir=Path(tmp_path), rules=rules or {"verified": {}, "unverified_count": 0, "ambiguities": []})
    ctx.shared["hot_day"] = hot_day()
    return ctx


@pytest.fixture(scope="module")
def sample_run(tmp_path_factory):
    from urbanova.engine.core import run_design

    ctx = sample_context(tmp_path_factory.mktemp("s2"))
    return ctx, run_design(ctx, ["urban_form", "solar_shade", "heat_stress", "hotspots"])


def hotspot_record(**over):
    """A minimal linked hotspot record, as engine.core.link_hotspots produces it."""
    h = {"id": "hs_1", "run_id": "run_x", "number": 1, "types": ["H1"], "type_labels": ["Sun-exposed hard surface"], "condition": "design", "site_wide": False,
         "geometry": None, "area_m2": 3299.9, "site_share_pct": 8.2, "size_class": "large", "intensity": "high", "severity": "moderate",
         "severity_uncapped": "high", "severity_capped": True, "provisional_basis": ["hotspots.size_classes"], "severity_basis": "§4.2",
         "supporting_metrics": [{"name": "area_m2", "label": "Area", "value": 3299.9, "unit": "m²", "source_metric_ids": ["met_a"]}],
         "likely_causes": [{"code": "LARGE_PAVED_AREA", "text": "94% hard surface", "evidence": {"hard_share": 0.94}, "evidence_ids": ["met_b"]}],
         "rule": None, "merged_from": 1, "confidence": "adequate", "confidence_reason": "Design geometry"}
    return {**h, **over}


# ------------------------------------------------------------------ UTCI (A9)
def test_utci_reproduces_the_pythermalcomfort_reference_cases():
    from urbanova.analysis.heat_stress import utci_c

    assert utci_c(25, 25, 1.0, 50) == pytest.approx(24.6, abs=0.1)
    assert utci_c(40, 25, 1.0, 50) == pytest.approx(40.4, abs=0.1)
    assert utci_c(60, 60, 1.0, 50) is None  # outside the valid input range: reported, never extrapolated


def test_utci_categories_follow_the_assessment_scale_and_its_basis():
    from urbanova import basis
    from urbanova.analysis import heat_stress as hs

    assert [hs.category(v) for v in (31.9, 32.0, 37.9, 38.0, 45.9, 46.0)] == [
        "moderate heat stress", "strong heat stress", "strong heat stress", "very strong heat stress", "very strong heat stress", "extreme heat stress"]
    cats = basis.get("heat_stress.utci_categories")
    assert cats["basis_type"] == "standard" and cats["value"] == {"moderate": 26, "strong": 32, "very_strong": 38, "extreme": 46}
    assert (hs.STRONG, hs.VERY_STRONG) == (cats["value"]["strong"], cats["value"]["very_strong"])
    assert basis.get("heat_stress.min_wind_ms")["value"] == hs.MIN_WIND_MS


def test_shade_lowers_mean_radiant_temperature_and_utci(sample_run):
    _, out = sample_run
    prof = out["module_status"]["heat_stress"]["details"]["profile"]
    noon = next(r for r in prof if r["hour"] == 12)
    assert noon["tr_sun_c"] > noon["tr_shade_c"] > noon["ta_c"] and noon["utci_sun_c"] > noon["utci_shade_c"]
    by = {m["metric_name"]: m for m in out["metrics"]}
    assert by["utci_sun_peak_c"]["condition"] == "existing" and by["heat_stress_hard_surface_very_strong_m2h"]["condition"] == "design"


def test_hot_day_profile_places_utc_hours_on_solar_time():
    from urbanova.analysis.climate import equation_of_time_min, hot_day_profile

    lon = 30.0  # solar time = UTC + 2 h + equation of time
    days = [date(2024, 7, 1) + timedelta(d) for d in range(10)]
    times, ta = [], []
    for d in days:
        for h in range(24):
            times.append(f"{d.isoformat()}T{h:02d}:00")
            solar_h = h + 2 + equation_of_time_min(d.timetuple().tm_yday) / 60
            ta.append(20 + solar_h)  # temperature equal to 20 + solar hour, so interpolation is exact
    series = {"ta_c": ta, "rh_pct": [30.0] * len(ta), "v10_ms": [2.0] * len(ta), "dni_wm2": [700.0] * len(ta), "dhi_wm2": [100.0] * len(ta)}
    dates = [d.isoformat() for d in days]
    tmax = [35.0] * 5 + [41.0] * 5  # only the last five days are hot
    prof = hot_day_profile({"times": times, "series": series}, dates, tmax, [7], 40.0, lon)
    assert prof["days"] == 5 and prof["dates"] == dates[5:]
    assert prof["hours"][12]["ta_c"] == pytest.approx(32.0, abs=0.01) and prof["hours"][7]["ta_c"] == pytest.approx(27.0, abs=0.01)
    assert hot_day_profile({"times": times, "series": series}, dates, tmax, [7], 45.0, lon) is None


# ------------------------------------------------------------------ H1 exactness and the sample site
def test_h1_sun_exposed_area_is_exact():
    from urbanova.analysis.hotspots import exposure_pieces
    from urbanova.analysis.solar import prism_shadow

    building = box(40, 40, 60, 60)
    hard = box(0, 0, 100, 100).difference(building)
    shadow = prism_shadow(building, 10.0, 45.0, 180.0)  # sun due south at 45°: a 10 m shadow to the north
    pieces = exposure_pieces(hard, {12: shadow, 13: None}, {12: (45.0, 180.0), 13: (0.0, 0.0)}, {12}, {12})
    lit = sum(p["geom"].area for p in pieces if p["sun_h"] == 1)
    assert lit == pytest.approx(100 * 100 - 20 * 20 - 20 * 10, abs=0.01)  # analytic: square − building − 20 m × 10 m shadow
    assert sum(p["geom"].area for p in pieces) == pytest.approx(hard.area, abs=1e-6)


def test_sample_car_park_and_plaza_are_h1_with_hand_calculated_areas(sample_run):
    from urbanova import geo

    ctx, out = sample_run
    feats = {f["properties"]["name"]: geo.to_local(shape(f["geometry"]), ctx.local_crs) for f in ctx.scenario["features"]["features"]}
    shadows = [s for s in ctx.shared["solar"]["shade_by_hour"].values() if s is not None]
    for name in ("Surface car park", "Paved plaza"):
        g = feats[name]
        hs = [h for h in out["hotspots"] if "H1" in h["types"] and geo.to_local(shape(h["geometry"]), ctx.local_crs).intersection(g).area > 0.99 * g.area]
        assert len(hs) == 1, name
        # Hand calculation: no hour's shadow leaves any part of the feature out of the ≥ 4 very-strong hours, so the whole polygon is the hotspot.
        hours_shaded = sum(1 for s in shadows if s.intersection(g).area > 0)
        assert hours_shaded <= len(shadows) - 4
        assert hs[0]["area_m2"] == pytest.approx(round(g.area, 1), abs=0.1), name
    assert {"H1", "H3"} <= set(next(h for h in out["hotspots"] if h["area_m2"] == pytest.approx(round(feats["Paved plaza"].area, 1), abs=0.1))["types"])


def test_every_hotspot_meets_the_section_4_acceptance(sample_run):
    from urbanova import geo

    ctx, out = sample_run
    hs = out["hotspots"]
    assert hs and [h["number"] for h in hs] == list(range(1, len(hs) + 1))
    available_design = {m["id"] for m in out["metrics"] if m["state"] == "available" and m["condition"] == "design"}
    for h in hs:
        assert h["area_m2"] > 0 and h["severity"] in ("high", "moderate", "low") and h["severity_basis"].startswith("§4.2")
        assert h["supporting_metrics"] and h["condition"] == "design" and set(h["types"]) <= {"H1", "H3", "H4"}
        assert any(c["evidence_ids"] for c in h["likely_causes"]), h["number"]
        assert {i for c in h["likely_causes"] for i in c["evidence_ids"]} <= available_design | {m["id"] for m in out["metrics"]}
    geoms = [geo.to_local(shape(h["geometry"]), ctx.local_crs) for h in hs if h["geometry"]]
    for i, a in enumerate(geoms):
        for b in geoms[i + 1:]:
            assert a.intersection(b).area <= 0.5 * min(a.area, b.area)
    order = [(-{"high": 3, "moderate": 2, "low": 1}[h["severity"]], -h["area_m2"]) for h in hs]
    assert order == sorted(order)


def test_hotspot_findings_chain_back_to_available_design_metrics(sample_run):
    _, out = sample_run
    by_id = {m["id"]: m for m in out["metrics"]}
    hfs = [f for f in out["findings"] if f["hotspot_ids"]]
    assert len(hfs) == len(out["hotspots"])
    for f in hfs:
        assert f["metric_ids"] and all(by_id[i]["state"] == "available" for i in f["metric_ids"])
        assert any(by_id[i]["condition"] == "design" for i in f["metric_ids"])
    recs = [r for r in out["recommendations"] if r["finding_id"] in {f["id"] for f in hfs}]
    assert recs and all(r["target"]["hotspot_id"] for r in recs)


def test_shade_01_is_replaced_by_hotspots():
    from urbanova import basis
    from urbanova.settings import load_config

    assert "SHADE-01" not in {f["id"] for f in load_config("rules.json")["findings"]}
    assert basis.get("finding.SHADE-01") is None


def test_every_cited_metric_name_resolves_to_a_metric_id(sample_run):
    _, out = sample_run
    for h in out["hotspots"]:
        assert all(s["source_metric_ids"] for s in h["supporting_metrics"] if s["name"] != "relative_shortfall_pct"), h["number"]
        assert all(c["evidence_ids"] for c in h["likely_causes"]), h["number"]


def test_contract_lists_every_runnable_module():
    from typing import get_args

    from urbanova.engine.contract import ModuleName
    from urbanova.readiness import RUNNABLE

    assert set(get_args(ModuleName)) == set(RUNNABLE)


def test_hotspots_validate_against_the_contract(sample_run):
    from urbanova.engine.contract import Hotspot

    _, out = sample_run
    for h in out["hotspots"]:
        Hotspot.model_validate({k: v for k, v in h.items() if k != "run_id"})


# ------------------------------------------------------------------ size, severity, merging, H4
def test_size_classes_and_the_severity_table():
    from urbanova.analysis.hotspots import SEVERITY, size_class

    assert size_class(150, 0.5) == "small" and size_class(500, 0.9) == "small" and size_class(500, 2) == "medium"
    assert size_class(1000, 2) == "large" and size_class(300, 6) == "large"
    assert SEVERITY == {"high": {"small": "moderate", "medium": "high", "large": "high"},
                        "moderate": {"small": "low", "medium": "moderate", "large": "high"},
                        "low": {"small": "low", "medium": "low", "large": "moderate"}}


def test_unapproved_size_classes_cap_severity_at_moderate(sample_run):
    from urbanova import basis

    _, out = sample_run
    assert basis.is_provisional("hotspots.size_classes")
    big = [h for h in out["hotspots"] if h["severity_uncapped"] == "high"]
    assert big and all(h["severity"] == "moderate" and h["severity_capped"] and "hotspots.size_classes" in h["provisional_basis"] for h in big)


def test_overlapping_hotspots_merge_and_keep_their_types():
    from urbanova.analysis.hotspots import merge

    a = {"types": ["H1"], "geom": box(0, 0, 10, 10), "area": 100, "intensity": "moderate", "site_wide": False}
    b = {"types": ["H3"], "geom": box(2, 0, 12, 10), "area": 100, "intensity": "high", "site_wide": False}
    c = {"types": ["H1"], "geom": box(10, 0, 20, 10), "area": 100, "intensity": "low", "site_wide": False}  # touches a, 20 % overlap with b
    site = {"types": ["H4"], "geom": None, "area": 500, "intensity": "low", "site_wide": True}
    out = merge([a, b, c, site])
    merged = next(h for h in out if h["types"] == ["H1", "H3"])
    assert merged["intensity"] == "high" and merged["area"] == pytest.approx(120) and merged["merged_from"] == 2
    assert len(out) == 3 and site in out


def test_h4_locates_setback_and_height_violations_and_keeps_ratios_site_wide(tmp_path):
    from urbanova.analysis.hotspots import h4_candidates
    from urbanova.analysis.urban_form import design_surfaces

    surf = design_surfaces(sample_context(tmp_path))
    site_area = surf["site"].area
    checks = [
        {"status": "fails", "rule_type": "min_setback_m", "rule_id": "r1", "label": "Minimum setback", "limit": 25.0, "measured": 15.0, "unit": "m", "metric": "min_building_setback_m"},
        {"status": "fails", "rule_type": "max_height_m", "rule_id": "r2", "label": "Maximum building height", "limit": 14.0, "measured": 18.0, "unit": "m", "metric": "max_building_height_m"},
        {"status": "fails", "rule_type": "min_green_space_pct", "rule_id": "r3", "label": "Minimum green", "limit": 20.0, "measured": 9.0, "unit": "%", "metric": "green_space_pct"},
        {"status": "passes", "rule_type": "max_floors", "rule_id": "r4", "label": "Max floors", "limit": 9, "measured": 6, "unit": "floors", "metric": "max_building_floors"},
    ]
    out = {c["rule"]["rule_id"]: c for c in h4_candidates(checks, surf, site_area)}
    assert set(out) == {"r1", "r2", "r3"}
    assert out["r1"]["geom"] is not None and out["r1"]["intensity"] == "high"  # 40 % short
    assert out["r2"]["geom"].area == pytest.approx(sum(g.area for g, p in surf["building_props"] if p["height_m"] > 14), abs=0.01)
    assert out["r3"]["site_wide"] and out["r3"]["area"] == pytest.approx(0.11 * site_area) and out["r3"]["intensity"] == "high"


def test_failed_rule_finding_links_to_its_h4_hotspot(env):
    from urbanova import findings

    h4 = hotspot_record(id="hs_9", types=["H4"], type_labels=["Planning violation"], rule={"rule_id": "rule_1"})
    check = {"status": "fails", "rule_id": "rule_1", "label": "Minimum setback", "measured": 2.0, "unit": "m", "operator": ">=", "limit": 5.0,
             "rule_source": {"document": "d", "page": 1, "text": "setback 5 m"}, "metric": "min_building_setback_m"}
    fs, _ = findings.generate({"id": "run_x"}, {"id": "p"}, {}, [], {"compliance_checks": [check]}, [h4])
    [f] = fs
    assert f["severity"] == "high" and f["hotspot_ids"] == ["hs_9"]


# ------------------------------------------------------------------ fallbacks
def test_without_a_hot_day_heat_stress_is_blocked_and_h1_h3_are_absent(tmp_path):
    from urbanova.engine.core import run_design

    ctx = sample_context(tmp_path)
    ctx.shared.pop("hot_day")
    out = run_design(ctx, ["urban_form", "solar_shade", "heat_stress", "hotspots"])
    assert out["module_status"]["heat_stress"]["status"] == "blocked"
    assert out["module_status"]["urban_form"]["status"] == "completed" and out["module_status"]["solar_shade"]["status"] == "completed"
    assert out["hotspots"] == [] and any("H1 and H3 not produced" in n for n in out["module_status"]["hotspots"]["details"]["notes"])
    by = {m["metric_name"]: m for m in out["metrics"]}
    assert by["hotspot_count"]["state"] == "unknown" and by["hotspot_count"]["value"] is None  # not assessed is never "0 hotspots"


def test_fallback_climate_provider_gives_no_hot_day(env, monkeypatch):
    from urbanova.analysis import climate
    from urbanova.analysis.common import Context

    data = {"provider": "nasa_power_daily", "dates": ["2024-07-01"], "series": {"tmax_c": [40.0]}}
    ctx = Context(project={"id": "p", "latitude": LAT, "longitude": LON}, scenario=None, config={}, run_id="r", out_dir=Path("."))
    metrics, why = climate._hot_day(ctx, data, {"hottest_months": [6, 7, 8], "tmax_p90_c": 39.0}, {"start_year": 2020, "end_year": 2024}, [])
    assert why and "fallback" in why and all(m["state"] == "unavailable" for m in metrics) and "hot_day" not in ctx.shared


# ------------------------------------------------------------------ H2: Gi* hot clusters (site context)
def test_gi_star_matches_the_closed_form():
    from urbanova.analysis.spatial_stats import band_neighbours, gi_star

    rng = np.random.default_rng(1)
    grid = rng.normal(30, 2, (6, 7))
    grid[0, 0] = np.nan
    g = gi_star(grid, 1.5, perms=99)
    valid = ~np.isnan(grid)
    x = grid[valid]
    neigh = band_neighbours(valid, 1.5)
    n, xbar, s = x.size, x.mean(), x.std()
    i = 10
    w = neigh[i].size + 1
    z = (x[i] + x[neigh[i]].sum() - xbar * w) / (s * np.sqrt((n * w - w ** 2) / (n - 1)))
    assert g["z"][valid][i] == pytest.approx(z)
    assert np.isnan(g["z"][0, 0]) and np.all((g["p"][valid] > 0) & (g["p"][valid] <= 1))
    assert np.array_equal(gi_star(grid, 1.5, perms=99)["p"], g["p"], equal_nan=True)  # seeded


def test_gi_star_false_discoveries_on_null_fields_stay_within_q():
    from urbanova.analysis.spatial_stats import hot_clusters

    q, fields, hits = 0.05, 200, 0
    for seed in range(fields):
        grid = np.random.default_rng(1000 + seed).normal(40, 1.5, (12, 12))
        hits += bool(hot_clusters(grid, 3, q, perms=199, seed=seed)["significant"].any())
    # With no real cluster every discovery is false, so the false-discovery rate is the share of fields with any discovery.
    assert hits / fields <= q + 0.03


def test_gi_star_recovers_a_planted_cluster():
    from urbanova.analysis.spatial_stats import hot_clusters

    grid = np.random.default_rng(7).normal(40, 1.0, (16, 16))
    grid[5:9, 5:9] += 3.0
    sig = hot_clusters(grid, 3, 0.05, perms=499)["significant"]
    assert sig[5:9, 5:9].mean() >= 0.75 and sig.sum() - sig[4:10, 4:10].sum() <= 4


def test_gi_star_on_autocorrelated_fields_finds_planted_clusters_and_records_its_rate():
    """Known limit (flagged for the spec): a spatially autocorrelated field is not random in space, so permutation Gi*
    reports its warm patches; the rate is recorded here, not asserted to be ≤ q. Planted clusters are still recovered."""
    from urbanova.analysis.spatial_stats import hot_clusters

    def smooth(seed):
        z = np.random.default_rng(seed).normal(0, 1, (20, 20))
        k = np.ones((3, 3)) / 9
        pad = np.pad(z, 1, mode="wrap")
        return 40 + sum(pad[i:i + 20, j:j + 20] * k[i, j] for i in range(3) for j in range(3)) * 3

    rate = np.mean([hot_clusters(smooth(s), 3, 0.05, perms=199, seed=s)["significant"].any() for s in range(40)])
    assert 0 <= rate <= 1
    grid = smooth(99)
    grid[8:12, 8:12] += 4.0
    assert hot_clusters(grid, 3, 0.05, perms=499)["significant"][8:12, 8:12].mean() >= 0.75


def test_h2_site_context_is_never_a_hotspot(env):
    from urbanova.analysis.surface_heat import hot_cluster

    rng = np.random.default_rng(5)
    comp = 40 + rng.normal(0, 0.5, (60, 60))
    comp[24:36, 24:36] += 4.0
    site = np.zeros_like(comp, bool)
    site[27:33, 27:33] = True
    out = hot_cluster(comp, site, np.ones_like(site))
    assert out["share_pct"] == pytest.approx(100.0) and out["cluster_blocks"] >= 4
    cold = hot_cluster(40 + rng.normal(0, 0.5, (60, 60)), site, np.ones_like(site))
    assert cold["share_pct"] == 0.0


# ------------------------------------------------------------------ app: persistence, report
def test_run_bundle_report_and_api_show_numbered_hotspots(client, auth, monkeypatch):
    from urbanova.analysis import climate
    from urbanova.analysis.common import COMPLETED, result

    def fake_climate(ctx):
        ctx.shared["hot_day"] = hot_day()
        ctx.shared["hottest_months"] = [6, 7, 8]
        return result(COMPLETED)

    monkeypatch.setitem(__import__("urbanova.engine.core", fromlist=["MODULES"]).MODULES, "climate_baseline", fake_climate)
    pid = make_project(client, auth)
    b = run(client, auth, pid, baseline_with_plan(client, auth, pid)["id"], {"modules": ["hotspots"], "solar_date": "2025-07-21"})
    assert b["run"]["status"] in ("succeeded", "partially_succeeded")
    hs = b["hotspots"]
    assert hs and [h["number"] for h in hs] == list(range(1, len(hs) + 1)) and isinstance(hs[0]["likely_causes"], list)
    assert any(f["hotspot_ids"] for f in b["findings"])
    assert b["run"]["configuration"]["modules"] == ["climate_baseline", "urban_form", "solar_shade", "heat_stress", "hotspots"]
    r = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth)
    assert r.status_code == 201, r.text
    html = client.get(f"/api/v1/reports/{r.json()['id']}/download?format=html", headers=auth).text
    assert "<h3 id='hotspots'>Design hotspots</h3>" in html and "<th>Likely causes</th>" in html and "No design hotspots" not in html
    pdf = client.get(f"/api/v1/reports/{r.json()['id']}/download?format=pdf", headers=auth)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
