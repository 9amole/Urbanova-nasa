"""Stage S3 of the smart upgrade (PHASE2_SPEC rev. 2 §6, §11): intervention catalogue, measured options by partial
re-runs, the optimiser (with and without prices), cost ranges with maintenance, and draft packages."""

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from shapely.geometry import shape

from conftest import SAMPLES, make_project, run, upload
from test_acceptance import baseline_with_plan
from test_stage_s2 import hot_day, sample_context

MODS = ["urban_form", "solar_shade", "heat_stress", "hotspots", "interventions"]


def test_rates():
    from urbanova import cost

    with open(SAMPLES / "sample_rates_TEST_ONLY.csv", encoding="utf-8") as fh:
        return [{**cost.validate_rate(r), "id": f"rat_{i}"} for i, r in enumerate(csv.DictReader(fh))]


@pytest.fixture(scope="module")
def unpriced(tmp_path_factory):
    from urbanova.engine.core import run_design

    ctx = sample_context(tmp_path_factory.mktemp("s3a"))
    return ctx, run_design(ctx, MODS)


@pytest.fixture(scope="module")
def priced(tmp_path_factory):
    from urbanova.engine.core import run_design

    ctx = sample_context(tmp_path_factory.mktemp("s3b"))
    ctx.rates = test_rates()
    ctx.config = {**ctx.config, "budget_amount": 60000.0, "budget_currency": "XTS"}
    from conftest import unapprove

    with pytest.MonkeyPatch.context() as mp:  # the 'value required' path for irrigation water
        unapprove(mp, "interventions.water_l_per_tree_day", "interventions.water_l_per_m2_day", clear_value=True)
        return ctx, run_design(ctx, MODS)


def _car_park(out):
    det = out["module_status"]["interventions"]["details"]
    car = next(h for h in out["hotspots"] if h["types"] == ["H1"] and h["area_m2"] == pytest.approx(3299.9, abs=0.1))
    return car, next(h for h in det["per_hotspot"] if h["hotspot_number"] == car["number"])


# ------------------------------------------------------------------ acceptance (§6.6)
def test_car_park_gets_pareto_options_with_no_prices_at_all(unpriced):
    _, out = unpriced
    _, ev = _car_park(out)
    ranked = [o for o in ev["options"] if not o["separate"]]
    front = [o for o in ranked if o["pareto"]]
    assert len(front) >= 2 and all(o["effect"]["change"] > 0 for o in ranked)
    assert all(o["cost"]["status"] == "price_required" and o["ranking_mode"] == "benefit_per_quantity" for o in ranked)
    assert {"best_value", "largest_benefit"} <= {r for o in ranked for r in o["roles"]}
    assert not any("cheapest_effective" in o["roles"] for o in ranked)  # needs prices


def test_with_prices_options_have_cost_ranges_and_a_cost_pareto_front(priced):
    _, out = priced
    _, ev = _car_park(out)
    ranked = [o for o in ev["options"] if not o["separate"]]
    priced_opts = [o for o in ranked if o["cost"]["status"] == "priced"]
    assert priced_opts and all(o["cost"]["low"] <= o["cost"]["typical"] <= o["cost"]["high"] and o["cost"]["currency"] == "XTS" for o in priced_opts)
    # The TEST rates cover trees, shade and planting but not parking removal: priced options are ranked on cost,
    # the unpriced ones only within their own intervention.
    assert len([o for o in priced_opts if o["pareto"]]) >= 2 and all(o["ranking_mode"] == "benefit_and_cost" for o in priced_opts)
    assert {"best_value", "cheapest_effective"} <= {r for o in priced_opts for r in o["roles"]}
    assert all(o["ranking_mode"] == "benefit_per_quantity" and "best_value" not in o["roles"] for o in ranked if o not in priced_opts)


def test_measured_effect_equals_an_independent_full_rerun(unpriced, tmp_path):
    from urbanova import interventions as I
    from urbanova.engine.core import run_design

    ctx, out = unpriced
    car, ev = _car_park(out)
    entry = next(e for e in ctx.shared["hotspots"] if e["record"]["number"] == car["number"])
    site = shape(ctx.project["boundary"])
    from urbanova import geo

    site_l = geo.to_local(site, ctx.local_crs)
    for opt in [o for o in ev["options"] if not o["separate"]][:3]:
        d = I.Design(ctx.scenario["features"], ctx.local_crs)
        I.apply_recipe(d, opt["recipe"], entry["geom"], entry["record"], site_l)
        full = sample_context(tmp_path, features=d.fc)
        full_out = run_design(full, ["urban_form", "solar_shade", "heat_stress", "hotspots"])  # all design modules, own caches
        assert I.benefit(full, "H1", entry["geom"], entry["record"]) == pytest.approx(opt["effect"]["after"], abs=0.01)
        _, part_out = I.partial_run(ctx, d.fc, I.DESIGN_MODULES)
        m_full = {m["metric_name"]: m["value"] for m in full_out["metrics"]}
        m_part = {m["metric_name"]: m["value"] for m in part_out["metrics"]}
        assert m_part["heat_stress_hard_surface_very_strong_m2h"] == pytest.approx(m_full["heat_stress_hard_surface_very_strong_m2h"], abs=0.01)


def test_every_recommendation_states_cost_two_confidences_tradeoffs_inputs_and_method(priced):
    _, out = priced
    recs = [r for r in out["recommendations"] if r.get("options")]
    assert recs
    for r in recs:
        assert r["cost"]["status"] in ("priced", "price_required", "not_applicable")
        assert {"effect", "cost"} == set(r["confidence"]) and all(r["confidence"][k]["level"] and r["confidence"][k]["reasons"] for k in ("effect", "cost"))
        assert r["tradeoffs"] and r["required_inputs"] and r["evaluation_method"] and r["hotspot_id"]
        if r["intervention_id"] in ("I1", "I3"):
            assert any("water" in t.lower() for t in r["tradeoffs"])
            assert all(o["water"]["note"] and "value required" in o["water"]["note"] for o in r["options"])
        assert all(o["effect"].get("change", o["effect"].get("treated_m2")) > 0 for o in r["options"])


def test_cool_paving_is_listed_separately_and_never_ranked(unpriced):
    _, out = unpriced
    for h in out["module_status"]["interventions"]["details"]["per_hotspot"]:
        for o in h["options"]:
            if o["intervention_id"] == "I4":
                assert o["separate"] and not o["roles"] and not o["pareto"] and o["effect_confidence"]["level"] == "low"
    rec = next(r for r in out["recommendations"] if r.get("intervention_id") == "I4")
    assert any("radiant heat" in t for t in rec["tradeoffs"])


def test_no_recommendation_hangs_off_existing_or_regional_findings(env):
    from test_remediation import _local_box_wgs, _metric
    from urbanova import findings

    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    m = _metric("climate_mean_annual_TX35_days", "climate_baseline", 120.0, "days/year", condition="existing")
    mods = {"climate_baseline": {"status": "completed", "details": {}}, "interventions": {"status": "completed", "options_by_hotspot": {}}}
    fs, recs = findings.generate({"id": "run_x"}, project, mods, [m], {})
    assert fs and recs == []


def test_only_the_top_hotspots_are_optimised(unpriced):
    from urbanova import basis

    _, out = unpriced
    det = out["module_status"]["interventions"]["details"]
    n = basis.get("interventions.max_hotspots")["value"]
    assert [h["hotspot_number"] for h in det["per_hotspot"]] == list(range(1, min(n, len(out["hotspots"])) + 1))
    assert len(det["not_optimised"]) == max(0, len(out["hotspots"]) - n)
    late = [f for f in out["findings"] if f["hotspot_ids"] and f["title"].startswith(f"Hotspot {n + 1}:")]
    assert all(any("highest-priority" in x for x in f["limitations"]) for f in late)


# ------------------------------------------------------------------ generators
def _entry(ctx, number):
    return next(e for e in ctx.shared["hotspots"] if e["record"]["number"] == number)


def test_tree_generator_keeps_clear_of_buildings_and_inside_the_hotspot(unpriced):
    from urbanova import geo
    from urbanova import interventions as I
    from urbanova.analysis.urban_form import design_surfaces

    ctx, out = unpriced
    car, _ = _car_park(out)
    e = _entry(ctx, car["number"])
    d = I.Design(ctx.scenario["features"], ctx.local_crs)
    n = I.gen_trees(d, e["geom"], 9, "I1")
    trees = [geo.to_local(shape(f["geometry"]), ctx.local_crs) for f in d.fc["features"] if f["properties"].get("generated_by") == "I1"]
    assert n == len(trees) > 0 and all(e["geom"].contains(t) for t in trees)
    buildings = design_surfaces(ctx)["buildings"]
    assert all(t.distance(buildings) >= I.param("tree")["building_clearance_m"] for t in trees)


def test_shade_convert_and_parking_generators_hit_their_shares(unpriced):
    from urbanova import interventions as I

    ctx, out = unpriced
    car, _ = _car_park(out)
    hs = _entry(ctx, car["number"])["geom"]
    for cov in (0.5, 1.0):
        assert I.gen_shade(I.Design(ctx.scenario["features"], ctx.local_crs), hs, cov, "I2") == pytest.approx(cov * hs.area, rel=0.01)
    paved = I._paved_in(I.Design(ctx.scenario["features"], ctx.local_crs), hs).area
    d = I.Design(ctx.scenario["features"], ctx.local_crs)
    assert I.gen_convert(d, hs, 0.25, "I3") == pytest.approx(0.25 * paved, rel=0.01)
    assert I._paved_in(d, hs).area == pytest.approx(0.75 * paved, rel=0.01)
    d = I.Design(ctx.scenario["features"], ctx.local_crs)
    assert I.gen_reduce_parking(d, hs, 0.5, "I5") == pytest.approx(0.5 * paved, rel=0.01)


def test_fix_rule_trims_the_setback_so_the_check_passes(tmp_path):
    from urbanova import interventions as I
    from urbanova.engine.core import run_design

    rule = {"id": "rule_1", "rule_type": "min_setback_m", "value": 25.0, "value_text": None, "document_name": "conditions.pdf (SYNTHETIC)", "page": 1, "exact_text": "Setback 25 m"}
    rules = {"verified": {"min_setback_m": rule}, "unverified_count": 0, "ambiguities": []}
    ctx = sample_context(tmp_path, rules=rules)
    out = run_design(ctx, ["urban_form", "solar_shade", "heat_stress", "planning_compliance", "hotspots", "interventions"])
    h4 = next(h for h in out["hotspots"] if "H4" in h["types"])
    ev = out["module_status"]["interventions"]["details"]["per_hotspot"]
    opts = [o for h in ev if h["hotspot_number"] == h4["number"] for o in h["options"] if o["intervention_id"] == "I6"]
    assert opts and opts[0]["effect"]["after"] == 0 and opts[0]["effect"]["rule_passes_after"]
    assert opts[0]["quantity"]["type"] == "building_footprint_removed_m2"
    rec = next(r for r in out["recommendations"] if r.get("intervention_id") == "I6")
    assert rec["hotspot_id"] == h4["id"]


# ------------------------------------------------------------------ cost (§6.4)
def test_option_cost_priority_ranges_maintenance_and_ageing():
    from urbanova import cost

    today = date.today().isoformat()
    old = (date.today() - timedelta(days=365 * 3)).isoformat()
    lib = [{"item": "trees_added_count", "unit": "each", "low": 80, "typical": 100, "high": 150, "currency": "EGP", "price_date": today, "source": "Quote A",
            "region": "test", "basis_type": "quotation", "maintenance_per_year": 5},
           {"item": "shade_structure_added_m2", "unit": "m2", "low": 1, "typical": 2, "high": 3, "currency": "EGP", "price_date": today, "source": "List",
            "region": "test", "basis_type": "approved_list", "approved_by": None}]
    c = cost.option_cost("trees_added_count", 10, [], lib)
    assert (c["low"], c["typical"], c["high"], c["maintenance_per_year"]) == (800, 1000, 1500, 50) and c["confidence"]["level"] == "medium"
    own = [{"quantity_type": "trees_added_count", "rate": 90.0, "currency": "EGP", "price_date": today, "source": "Project quote", "maintenance_per_year": None}]
    c = cost.option_cost("trees_added_count", 10, own, lib)
    assert c["source_kind"] == "project_rate" and c["low"] == c["typical"] == c["high"] == 900 and c["confidence"]["level"] == "high"
    c = cost.option_cost("trees_added_count", 10, [{**own[0], "price_date": old}], [])
    assert c["stale"] and c["confidence"]["level"] == "low"
    assert cost.option_cost("shade_structure_added_m2", 10, [], lib)["status"] == "price_required"  # an unapproved list is never used
    assert cost.option_cost(None, 10, [], lib)["status"] == "not_applicable"


def test_cost_library_starts_empty_and_rates_accept_maintenance():
    from urbanova import cost
    from urbanova.settings import load_config

    assert load_config("cost_library.json")["entries"] == []
    r = cost.validate_rate({"quantity_type": "trees_added_count", "unit": "each", "rate": "10", "currency": "XTS", "price_date": "2026-01-01",
                            "source": "TEST", "maintenance_per_year": "2.5"})
    assert r["maintenance_per_year"] == 2.5
    with pytest.raises(cost.CostError):
        cost.validate_rate({**r, "rate": "10", "maintenance_per_year": "-1"})


def test_catalogue_intensities_match_their_basis_records():
    from urbanova import interventions as I

    from urbanova import basis

    for iid, item in I.catalogue().items():
        if item.get("intensity_basis"):  # sizes live only in their basis record
            assert "intensities" not in item and basis.get(f"interventions.{item['intensity_basis'].split('.')[0]}"), iid
        assert bool(I.intensities(iid)) == (item["generator"] != "check_only"), iid
        for k in ("title", "intervention", "expected_direction", "required_inputs", "tradeoffs", "evaluation_method", "applies_to", "literature"):
            assert k in item, (iid, k)


# ------------------------------------------------------------------ package and drafts
def test_budget_package_is_re_evaluated_together_as_a_draft(priced):
    _, out = priced
    pkg = out["module_status"]["interventions"]["details"]["package"]
    assert pkg["status"] == "draft" and pkg["option_ids"] and pkg["cost"]["typical"] <= 60000 and pkg["features"]["features"]
    assert all(e["after_together"] is not None and e["after_together"] < e["before"] for e in pkg["effects"])


def test_no_package_without_a_budget(unpriced):
    _, out = unpriced
    assert out["module_status"]["interventions"]["details"]["package"] is None


def test_draft_is_not_analysed_until_approved(client, auth, monkeypatch):
    from urbanova.analysis.common import COMPLETED, result
    from urbanova.engine import core

    def fake_climate(ctx):
        ctx.shared["hot_day"] = hot_day()
        ctx.shared["hottest_months"] = [6, 7, 8]
        return result(COMPLETED)

    monkeypatch.setitem(core.MODULES, "climate_baseline", fake_climate)
    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_rates_TEST_ONLY.csv", "rate_table").status_code in (200, 201)
    sid = baseline_with_plan(client, auth, pid)["id"]
    b = run(client, auth, pid, sid, {"modules": ["interventions"], "solar_date": "2025-07-21", "budget_amount": 60000, "budget_currency": "XTS"})
    assert b["run"]["status"] in ("succeeded", "partially_succeeded")
    recs = [r for r in b["recommendations"] if r.get("options")]
    assert recs and all(isinstance(r["options"], list) and r["confidence"]["effect"]["level"] for r in recs)
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/analysis-runs/{b['run']['id']}/export", headers=auth).content))
    assert "options.csv" in z.namelist() and len(z.read("options.csv").decode("utf-8").splitlines()) > 1
    rep = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth)
    assert rep.status_code == 201, rep.text
    html = client.get(f"/api/v1/reports/{rep.json()['id']}/download?format=html", headers=auth).text
    assert "<h3 id='options'>Measured options per hotspot</h3>" in html and "XTS" in html
    assert client.get(f"/api/v1/reports/{rep.json()['id']}/download?format=pdf", headers=auth).content[:4] == b"%PDF"
    r = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/package/draft", headers=auth)
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["status"] == "draft"
    refused = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": draft["id"], "config": {}})
    assert refused.status_code >= 400
    ok = client.post(f"/api/v1/scenarios/{draft['id']}/approve", headers=auth)
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    again = client.post(f"/api/v1/scenarios/{draft['id']}/approve", headers=auth)
    assert again.status_code >= 400
    assert client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": draft["id"], "config": {"modules": ["urban_form"]}}).status_code == 202


def test_budget_settings_are_validated(client, auth):
    pid = make_project(client, auth)
    for cfg in ({"budget_amount": 100}, {"budget_amount": -5, "budget_currency": "XTS"}, {"budget_amount": 100, "budget_currency": "dollars"}):
        r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"config": cfg})
        assert r.status_code >= 400, cfg


def test_engine_contract_carries_options_and_package(priced):
    from urbanova.engine.api import _rec
    from urbanova.engine.contract import Recommendation

    _, out = priced
    for r in out["recommendations"]:
        Recommendation.model_validate(_rec(r))
    assert json.dumps(out["module_status"]["interventions"]["details"]["package"], default=str)


def test_test_prices_never_give_high_cost_confidence():
    from urbanova import cost

    c = cost.option_cost("trees_added_count", 10, test_rates())
    assert c["status"] == "priced" and c["confidence"]["level"] == "low" and any("TEST" in r for r in c["confidence"]["reasons"])
