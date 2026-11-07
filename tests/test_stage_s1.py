"""Stage S1 of the smart upgrade (PHASE2_SPEC rev. 2 §11): condition labels, basis records, TX90p,
statistical LST position, Nominatim address search."""

import json
import re
from datetime import date, timedelta

import numpy as np
import pytest

from conftest import ROOT, make_project, run
from test_acceptance import baseline_with_plan
from test_remediation import _generate, _local_box_wgs, _metric


# ------------------------------------------------------------------ condition (existing | design)
def test_every_module_has_a_condition_and_metrics_carry_it(client, auth):
    from urbanova.engine.core import CONDITION, MODULES

    assert set(CONDITION) == set(MODULES) and set(CONDITION.values()) <= {"existing", "design"}
    pid = make_project(client, auth)
    b = run(client, auth, pid, baseline_with_plan(client, auth, pid)["id"])
    assert b["metrics"]
    for m in b["metrics"]:
        # The sun's position depends on place and date only, so it is the same for every design.
        # So are UTCI values in full sun or shade on the hot day (climate and sun position only).
        assert m["condition"] == ("existing" if m["metric_name"].startswith(("solar_elevation", "solar_azimuth", "utci_")) else CONDITION[m["module"]])
        # A metric that depends on the design must come from a design analysis, and vice versa.
        assert bool(m["design_dependent"]) == (m["condition"] == "design"), m["metric_name"]


def test_existing_metrics_never_appear_as_scenario_differences():
    from urbanova import comparison

    def m(name, cond, value):
        return {"metric_name": name, "label": name, "unit": "%", "state": "available", "value": value, "method": "x",
                "output_kind": "calculated", "design_dependent": int(cond == "design"), "condition": cond, "reason": None}

    base, alt = {"id": "b", "name": "B"}, {"id": "a", "name": "A"}
    metrics = {"b": {"green_space_pct": m("green_space_pct", "design", 10), "ndvi_site_mean": m("ndvi_site_mean", "existing", 0.1)},
               "a": {"green_space_pct": m("green_space_pct", "design", 20), "ndvi_site_mean": m("ndvi_site_mean", "existing", 0.3)}}
    out = comparison.compare_core(base, [alt], metrics, {"b": [], "a": []}, {"b": [], "a": []}, {}, None)
    assert {r["metric"] for r in out["rows"]} == {"green_space_pct"}
    assert [s["metric"] for s in out["site_conditions_shared"]] == ["ndvi_site_mean"]


def test_condition_falls_back_for_records_written_before_the_field():
    from urbanova import values as V

    assert V.condition_of({"design_dependent": 1}) == "design"
    assert V.condition_of({"design_dependent": 0}) == "existing"
    assert V.condition_of({"design_dependent": 0, "condition": "design"}) == "design"


def test_analyses_doc_is_generated_from_the_config():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "gen_analyses_doc.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert (ROOT / "docs" / "ANALYSES.md").read_text(encoding="utf-8") == gen.render(), "run scripts/gen_analyses_doc.py"


# ------------------------------------------------------------------ basis records
def test_every_threshold_and_default_has_a_basis_record():
    from urbanova import basis, orchestrator
    from urbanova.settings import load_config

    rules = load_config("rules.json")
    ids = {r["id"] for r in basis.all_records()}
    needed = {f"climate.{ix['id']}" for ix in rules["climate_indices"]} | {f"finding.{f['id']}" for f in rules["findings"]}
    needed |= {f"config.{k}" for k in orchestrator.DEFAULT_CONFIG} | {"compliance.plot_area_tolerance_pct", "climate.TX90p",
               "climate.max_missing_fraction", "vegetation.ndvi_threshold", "vegetation.s2_offset", "vegetation.scl_mask",
               "surface_heat.st_scale_offset", "surface_heat.qa_mask_bits", "surface_heat.block_size_m", "surface_heat.min_ring_blocks",
               "solar.report_hours", "cost.stale_price_years", "finding.planning_failure"}
    assert not needed - ids, f"values without a basis record: {sorted(needed - ids)}"
    refs = set(re.findall(r"^\| (R-\d+) \|", (ROOT / "docs" / "PHASE2_SPEC.md").read_text(encoding="utf-8"), re.M))
    for r in basis.all_records():
        assert r["basis_type"] in basis.BASIS_TYPES, r["id"]
        assert r["applicability"], r["id"]
        for ref in r["references"]:
            if ref.startswith("R-"):
                assert ref.split()[0] in refs, f"{r['id']} cites unknown {ref}"


def test_basis_values_match_the_code():
    from urbanova import basis, cost, orchestrator
    from urbanova.analysis import climate, solar, surface_heat, vegetation
    from urbanova.settings import load_config

    rules = load_config("rules.json")
    assert basis.get("vegetation.ndvi_threshold")["value"] == vegetation.VEG_THRESHOLD
    assert basis.get("vegetation.scl_mask")["value"] == sorted(vegetation.SCL_MASK)
    assert basis.get("surface_heat.st_scale_offset")["value"] == {"scale": surface_heat.SCALE, "offset_k": surface_heat.OFFSET}
    assert basis.get("surface_heat.block_size_m")["value"] == surface_heat.BLOCK * 30
    assert basis.get("surface_heat.min_ring_blocks")["value"] == surface_heat.MIN_RING_BLOCKS
    assert basis.get("climate.max_missing_fraction")["value"] == climate.MAX_MISSING_FRACTION
    assert basis.get("solar.report_hours")["value"] == list(solar.REPORT_HOURS)
    assert basis.get("cost.stale_price_years")["value"] == cost.STALE_PRICE_YEARS
    assert basis.get("compliance.plot_area_tolerance_pct")["value"] == rules["plot_area_tolerance_pct"]
    for k, v in orchestrator.DEFAULT_CONFIG.items():
        assert basis.get(f"config.{k}")["value"] == v, k
    for ix in rules["climate_indices"]:
        assert basis.get(f"climate.{ix['id']}")["value"] == ix["threshold"], ix["id"]
    for f in rules["findings"]:
        # rules.json status and the basis agree on what is provisional
        assert (f["status"] == "provisional_not_approved") == basis.is_provisional(f"finding.{f['id']}"), f["id"]


def _lst(pct, diff_k=2.0):
    return [_metric("lst_site_percentile_in_surroundings", "surface_heat", pct, "percentile"),
            _metric("lst_site_minus_surroundings_k", "surface_heat", diff_k, "K")]


def test_provisional_rules_are_capped_and_statistical_ones_are_not(env):
    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    [f], _ = _generate(_lst(99.0), project)
    assert f["rule_id"] == "HEAT-LST-01" and f["severity"] == "high" and not f["threshold"]["severity_capped"]
    assert f["threshold"]["status"] == "statistical" and f["threshold"]["basis"]["references"] == ["R-6"]
    assert any("at least 1 K (a PROVISIONAL minimum" in x for x in f["limitations"])
    [g], _ = _generate(_lst(92.0), project)
    assert g["severity"] == "moderate"
    assert not _generate(_lst(60.0), project)[0]


def test_a_small_difference_is_not_reported_however_unusual(env):
    from urbanova import basis
    from urbanova.settings import load_config

    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    assert not _generate(_lst(99.0, diff_k=0.37), project)[0]  # the sample site: 95th percentile but only 0.37 K
    assert not _generate(_lst(99.0)[:1], project)[0]  # no measured difference → no finding
    rule = next(r for r in load_config("rules.json")["findings"] if r["id"] == "HEAT-LST-01")
    assert basis.get(rule["min_effect"]["basis"])["value"] == rule["min_effect"]["min"]
    assert basis.is_provisional(rule["min_effect"]["basis"])


def test_report_lists_values_used_with_their_basis(client, auth):
    pid = make_project(client, auth)
    b = run(client, auth, pid, baseline_with_plan(client, auth, pid)["id"])
    r = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth)
    assert r.status_code == 201, r.text
    rep = r.json()
    html = client.get(f"/api/v1/reports/{rep['id']}/download?format=html", headers=auth).text
    assert "Values used and their basis" in html and "finding.HEAT-LST-01" in html and "Statistical test" in html
    assert "Site &amp; urban-form indicators (coverage, green space) <small>(proposed design)</small>" in html  # offline: only design analyses have values


# ------------------------------------------------------------------ TX90p
def _days(start: date, n: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_tx90p_matches_a_hand_calculation():
    from urbanova.analysis.climate import _calendar_day, tx90p

    rng = np.random.default_rng(1)
    base_d = _days(date(1961, 1, 1), 365 * 30 + 7)
    seasonal = lambda d: 30 + 8 * np.sin((_calendar_day(d) - 100) / 365 * 2 * np.pi)
    base_t = [seasonal(d) + rng.normal(0, 2) for d in base_d]
    an_d = _days(date(2021, 1, 1), 365 * 4 + 1)
    same = tx90p(an_d, [seasonal(d) + rng.normal(0, 2) for d in an_d], base_d, base_t)
    warmer = tx90p(an_d, [seasonal(d) + 2 + rng.normal(0, 2) for d in an_d], base_d, base_t)
    assert 7 <= same["mean"] <= 13  # same climate as the base → about 10 %
    assert warmer["mean"] > 30  # 2 °C warmer → far more days above the base 90th percentile
    # Hand check of one threshold: 5-day window centred on 1 July over all base years.
    k = _calendar_day("2001-07-01")
    sample = [t for d, t in zip(base_d, base_t) if abs(_calendar_day(d) - k) <= 2]
    assert same["thresholds_c"][k] == pytest.approx(np.percentile(sample, 90, method="median_unbiased"))
    assert set(same["per_year"]) == {"2021", "2022", "2023", "2024"}


def test_tx90p_needs_a_complete_base_period():
    from urbanova.analysis.climate import tx90p

    base_d = _days(date(1961, 1, 1), 3650)
    assert tx90p(base_d, [30.0] * 3650, base_d, [None] * 3650) is None


def test_tx90p_is_unavailable_with_a_reason_when_the_base_cannot_be_fetched(client, auth, monkeypatch):
    from test_acceptance import _fake_series
    from urbanova.providers import weather

    monkeypatch.setattr(weather, "fetch_open_meteo", lambda *a, **k: _fake_series("open_meteo_archive"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})  # offline: base request fails
    [m] = [m for m in b["metrics"] if m["metric_name"] == "climate_mean_annual_TX90p_pct"]
    assert m["state"] == "unavailable" and m["value"] is None and "1961–1990" in m["reason"]
    assert b["run"]["module_status"]["climate_baseline"]["status"] == "completed"  # the rest of the module is unaffected

    base = {**_fake_series("open_meteo_archive")}
    days = _days(date(1961, 1, 1), 365 * 30 + 7)
    base.update(dates=days, series={"tmax_c": [25 + 12 * np.sin((date.fromisoformat(d).timetuple().tm_yday - 100) / 365 * 2 * np.pi) for d in days]})
    monkeypatch.setattr(weather, "fetch_tmax_base", lambda *a, **k: base)
    b2 = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    [m2] = [m for m in b2["metrics"] if m["metric_name"] == "climate_mean_annual_TX90p_pct"]
    assert m2["state"] == "available" and m2["unit"] == "% of days"


def test_tx90p_is_not_mixed_with_the_fallback_provider(client, auth, monkeypatch):
    from test_acceptance import _fake_series
    from urbanova.providers import weather
    from urbanova.providers.base import ProviderError

    def fail(*a, **k):
        raise ProviderError("open_meteo_archive", "http_error", "HTTP 503")

    monkeypatch.setattr(weather, "fetch_open_meteo", fail)
    monkeypatch.setattr(weather, "fetch_nasa_power", lambda *a, **k: _fake_series("nasa_power_daily"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    [m] = [m for m in b["metrics"] if m["metric_name"] == "climate_mean_annual_TX90p_pct"]
    assert m["state"] == "unavailable" and "1981" in m["reason"]


# ------------------------------------------------------------------ statistical LST position
def _field(site_offset: float, noise: float, seed: int = 0):
    rng = np.random.default_rng(seed)
    comp = 40 + rng.normal(0, noise, (60, 60))
    site = np.zeros((60, 60), bool)
    site[27:33, 27:33] = True
    ring = ~site
    ring[25:35, 25:35] = False  # gap around the site
    comp[site] += site_offset
    return comp, site, ring


def test_hot_site_ranks_above_the_95th_percentile_of_its_surroundings():
    from urbanova.analysis.surface_heat import site_position

    pos = site_position(*_field(3.0, 0.8))
    assert pos["ring_blocks"] > 300 and pos["percentile"] >= 95 and pos["z"] > 2


def test_uniform_field_gives_the_middle_and_no_finding(env):
    from urbanova.analysis.surface_heat import site_position

    pos = site_position(*_field(0.0, 0.0))
    assert pos["percentile"] == 50 and pos["z"] is None
    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    assert not _generate(_lst(pos["percentile"]), project)[0]


def test_blocks_with_missing_pixels_are_not_used():
    from urbanova.analysis.surface_heat import MIN_RING_BLOCKS, site_position

    comp, site, ring = _field(1.0, 0.5)
    comp[ring] = np.nan
    comp[0:3, 0:3 * (MIN_RING_BLOCKS - 1)] = 40.0  # only MIN_RING_BLOCKS − 1 complete blocks
    pos = site_position(comp, site, ring)
    assert pos["ring_blocks"] == MIN_RING_BLOCKS - 1 and pos["percentile"] is None


# ------------------------------------------------------------------ Nominatim address search
NOMINATIM_BODY = [{"display_name": "Fifth Settlement, New Cairo, Cairo, Egypt", "lat": "30.0074", "lon": "31.4913",
                   "boundingbox": ["29.9", "30.1", "31.3", "31.6"], "type": "suburb"},
                  {"display_name": "broken", "lat": "x", "lon": "1"}]


@pytest.fixture()
def nominatim(monkeypatch):
    from urbanova.providers import geocode

    calls = []

    def fake(provider_id, method, url, *, params=None, cache=False, cache_params=None, **k):
        calls.append(params)
        return NOMINATIM_BODY, {"cache_hit": False}

    monkeypatch.setattr(geocode, "http_json", fake)
    monkeypatch.setattr(geocode, "_last_request", 0.0)
    return calls


def test_geocode_endpoint_returns_places_with_attribution(client, auth, nominatim):
    r = client.get("/api/v1/geocode", params={"q": "  Fifth   Settlement "}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["name"] for x in body["results"]] == ["Fifth Settlement, New Cairo, Cairo, Egypt"]  # malformed entry dropped
    assert body["results"][0]["latitude"] == pytest.approx(30.0074) and body["results"][0]["bbox"] == [29.9, 30.1, 31.3, 31.6]
    assert "OpenStreetMap" in body["attribution"]
    assert nominatim == [{"q": "Fifth Settlement", "format": "jsonv2", "limit": 5, "accept-language": "en"}]


def test_geocode_requires_sign_in_and_a_real_query(client, auth, nominatim):
    assert client.get("/api/v1/geocode", params={"q": "Cairo"}).status_code == 401
    assert client.get("/api/v1/geocode", params={"q": "ab"}, headers=auth).status_code == 422
    assert client.get("/api/v1/geocode", params={"q": "x" * 201}, headers=auth).status_code == 422
    assert nominatim == []


def test_geocode_offline_gives_a_clear_message(client, auth):
    r = client.get("/api/v1/geocode", params={"q": "New Cairo"}, headers=auth)
    assert r.status_code == 503 and "Address search is unavailable" in r.json()["detail"]


def test_geocode_waits_a_second_between_uncached_requests(env, nominatim, monkeypatch):
    from urbanova.providers import geocode

    clock = {"t": 100.0}
    slept = []
    monkeypatch.setattr(geocode, "_clock", lambda: clock["t"])
    monkeypatch.setattr(geocode, "_sleep", lambda s: slept.append(s) or clock.__setitem__("t", clock["t"] + s))
    geocode.search("New Cairo")
    clock["t"] += 0.3
    geocode.search("Old Cairo")
    assert slept[0] == 0 and slept[1] == pytest.approx(0.7)


def test_nominatim_is_registered_and_approved():
    from urbanova.providers.base import availability, registry

    p = registry()["nominatim"]
    assert p["approval_status"] == "approved" and p["approved_by"] and p["approved_on"] == "2026-11-07"
    assert p["caching_permitted"] and "OpenStreetMap" in p["attribution"]
    assert availability("nominatim")[0]


# ------------------------------------------------------------------ review regressions
def test_tx90p_with_an_empty_calendar_window_is_unavailable_not_a_crash():
    from urbanova.analysis.climate import _calendar_day, tx90p

    base_d = _days(date(1961, 1, 1), 365 * 30 + 7)
    k = _calendar_day("2001-07-01")
    base_t = [None if abs(_calendar_day(d) - k) <= 3 else 30.0 for d in base_d]  # same week missing every year (98 % complete)
    assert tx90p(_days(date(2021, 1, 1), 365), [31.0] * 365, base_d, base_t) is None


def test_existing_land_findings_are_context_without_design_recommendations(env):
    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    [f], recs = _generate(_lst(99.0), project)
    assert recs == [] and any("decision S-8" in x for x in f["limitations"])
    # A design finding (a located design hotspot) still gets its recommendations.
    from test_stage_s2 import hotspot_record
    from urbanova import findings

    _, recs2 = findings.generate({"id": "run_x"}, project, {}, [], {}, [hotspot_record()])
    assert recs2


def test_large_site_is_judged_against_averages_of_its_own_size():
    from urbanova.analysis.surface_heat import site_position

    rng = np.random.default_rng(3)
    comp = 40 + rng.normal(0, 1.0, (90, 90))
    site = np.zeros((90, 90), bool)
    site[30:60, 30:60] = True  # 900 pixels ≈ 100 blocks
    ring = ~site
    ring[27:63, 27:63] = False
    comp[site] += 0.6  # well under one block SD, but large for a 100-block average
    pos = site_position(comp, site, ring)
    assert pos["site_blocks"] == 100 and pos["percentile"] >= 95
    assert site_position(comp, site, ring) == pos  # seeded: reproducible


def test_index_links_assets_by_content_hash(client):
    import hashlib

    r = client.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    for name in ("app.js", "styles.css"):
        digest = hashlib.sha256((ROOT / "web" / name).read_bytes()).hexdigest()[:12]
        assert f"{name}?v={digest}" in r.text
