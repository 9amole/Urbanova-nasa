"""Stage S5 of the smart upgrade (PHASE2_SPEC rev. 2 §7): comparison trade-offs (cost vs heat stress, Pareto front),
what each design gives up, no winner without weights, best under priorities (never a design failing a verified rule,
budget respected) and robustness to ±20 % weights."""

import json

import pytest

from conftest import SAMPLES, make_project, upload
from test_acceptance import _two_scenarios


def _m(name, value, cond="design", unit="x"):
    return {"metric_name": name, "label": name, "unit": unit, "state": "available", "value": value, "method": "m", "output_kind": "calculated",
            "design_dependent": int(cond == "design"), "condition": cond, "reason": None}


def _cost(total, currency="XTS", status="complete"):
    return {"status": status, "totals_by_currency": {currency: total} if total is not None else {}, "lines": [], "exclusions": [], "warnings": [], "label": "",
            "maintenance_per_year_by_currency": {}, "maintenance_value_required": []}


def _check(status):
    return [{"rule_type": "min_green_space_pct", "label": "Minimum green", "status": status, "limit": 20, "measured": 10 if status == "fails" else 25}]


def _designs(heat, green, costs, fails=()):
    """Designs b (baseline), a1, a2, ... with heat-stress m²·h, green % and cost; `fails` lists ids failing a verified rule."""
    ids = ["b"] + [f"a{i}" for i in range(1, len(heat))]
    scen = [{"id": i, "name": i.upper()} for i in ids]
    metrics = {i: {"heat_stress_hard_surface_very_strong_m2h": _m("heat_stress_hard_surface_very_strong_m2h", h, unit="m²·h"),
                   "green_space_pct": _m("green_space_pct", g, unit="%"), "ndvi_site_mean": _m("ndvi_site_mean", 0.1, "existing")}
               for i, h, g in zip(ids, heat, green)}
    checks = {i: _check("fails" if i in fails else "passes") for i in ids}
    cost_map = {i: c for i, c in zip(ids[1:], costs)}
    return scen, metrics, checks, cost_map


def _compare(scen, metrics, checks, costs, criteria=None, budget=None, hotspots=None):
    from urbanova import comparison

    return comparison.compare_core(scen[0], scen[1:], metrics, checks, {s["id"]: [] for s in scen}, costs, criteria, hotspots=hotspots, budget=budget)


# ------------------------------------------------------------------ §7 acceptance
def test_without_weights_there_is_no_winner_but_the_tradeoff_chart_is_given():
    scen, metrics, checks, costs = _designs([100, 50, 60], [10, 20, 25], [_cost(10), _cost(20)])
    out = _compare(scen, metrics, checks, costs)
    assert out["choice"]["mode"] == "no_winner" and "no single best design" in out["choice"]["statement"].lower()
    assert out["ranking"] is None and len(out["tradeoffs"]["chart"]["points"]) == 3
    assert "ndvi_site_mean" not in {r["metric"] for r in out["rows"]}  # existing land is never a difference


def test_pareto_front_and_unpriced_designs_are_listed_not_plotted_at_zero():
    scen, metrics, checks, costs = _designs([100, 50, 60, 40], [10, 20, 25, 30], [_cost(10), _cost(20), _cost(None, status="partial")])
    chart = _compare(scen, metrics, checks, costs)["tradeoffs"]["chart"]
    assert chart["front"] == ["b", "a1"]  # a2 costs more and is hotter than a1
    pts = {p["id"]: p for p in chart["points"]}
    assert pts["a2"]["pareto"] is False and pts["b"]["cost"] == 0
    assert pts["a3"]["cost"] is None and [n["id"] for n in chart["not_plotted"]] == ["a3"] and "price" in chart["not_plotted"][0]["missing"]


def test_with_weights_the_best_names_its_weights_and_never_fails_a_verified_rule():
    scen, metrics, checks, costs = _designs([100, 50, 60], [10, 30, 20], [_cost(10), _cost(20)], fails={"a1"})
    ch = _compare(scen, metrics, checks, costs, criteria=[{"metric": "green_space_pct", "weight": 1}, {"metric": "heat_stress_hard_surface_very_strong_m2h", "weight": 1}])["choice"]
    assert ch["mode"] == "best_under_priorities" and ch["best"] == "a2"  # a1 scores highest but fails a verified rule
    assert any(x["id"] == "a1" and "verified planning rule" in x["why"] for x in ch["excluded"])
    assert {w["criterion"] for w in ch["weights"]} == {"green_space_pct", "heat_stress_hard_surface_very_strong_m2h"}
    assert ch["robustness"]["result"] in ("robust", "sensitive") and ch["robustness"]["statement"]


def test_budget_excludes_designs_over_it_or_without_a_price():
    scen, metrics, checks, costs = _designs([100, 50, 60, 55], [10, 30, 20, 25], [_cost(100), _cost(20), _cost(None, status="partial")])
    ch = _compare(scen, metrics, checks, costs, criteria=[{"metric": "green_space_pct", "weight": 1}], budget={"amount": 50, "currency": "XTS"})["choice"]
    why = {x["id"]: x["why"] for x in ch["excluded"]}
    assert "over the budget" in why["a1"] and "no complete price" in why["a3"] and ch["best"] == "a2"


def test_derived_cost_and_compliance_criteria_rank_and_missing_is_never_zero():
    scen, metrics, checks, costs = _designs([100, 50, 60], [10, 30, 20], [_cost(10), _cost(None, status="partial")])
    out = _compare(scen, metrics, checks, costs, criteria=[{"metric": "cost_typical", "weight": 1}, {"metric": "compliance_failures", "weight": 1}])
    cost_c = next(c for c in out["ranking"]["criteria"] if c["metric"] == "cost_typical")
    assert set(cost_c["values"]) == {"b", "a1"}  # a2 has no complete price: excluded from that criterion, not scored 0
    assert any("missing for some scenarios" in n for n in out["ranking"]["notes"])


def test_robustness_reports_a_flip_and_a_clear_winner():
    criteria = [{"metric": "green_space_pct", "weight": 1}, {"metric": "heat_stress_hard_surface_very_strong_m2h", "weight": 1}]
    # Close call: a1 is better on green, a2 on heat; equal weights favour a1 slightly.
    scen, metrics, checks, costs = _designs([100, 55, 50], [10, 30, 28.2], [_cost(10), _cost(10)])
    rob = _compare(scen, metrics, checks, costs, criteria=criteria)["choice"]["robustness"]
    assert rob["result"] == "sensitive" and rob["sensitive_to"]
    scen, metrics, checks, costs = _designs([100, 30, 90], [10, 40, 12], [_cost(10), _cost(10)])
    rob = _compare(scen, metrics, checks, costs, criteria=criteria)["choice"]["robustness"]
    assert rob["result"] == "robust" and "Robust" in rob["statement"]


def test_hotspots_by_severity_are_compared():
    scen, metrics, checks, costs = _designs([100, 50], [10, 20], [_cost(10)])
    hs = {"b": [{"severity": "moderate", "area_m2": 3000.0, "site_wide": 0}, {"severity": "low", "area_m2": 200.0, "site_wide": 0}],
          "a1": [{"severity": "moderate", "area_m2": 1200.0, "site_wide": 0}]}
    h = _compare(scen, metrics, checks, costs, hotspots=hs)["hotspots"]
    assert h["b"]["count"] == 2 and h["a1"]["vs_baseline"] == {"located_area_m2": -2000.0, "count": -1, "outcome": "improvement"}


# ------------------------------------------------------------------ give-up list on the sample designs
def test_give_up_counts_parking_spaces_trees_and_marks_water_value_required():
    from shapely.geometry import shape

    from urbanova import comparison, cost, geo

    project = {"id": "p", "latitude": 30.12, "longitude": 31.85,
               "boundary": json.loads((SAMPLES / "sample_site_boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]}
    base_fc = json.loads((SAMPLES / "sample_plan_baseline.geojson").read_text(encoding="utf-8"))
    adds = json.loads((SAMPLES / "sample_alternative_additions.geojson").read_text(encoding="utf-8"))
    alt_fc = {"type": "FeatureCollection", "features": [f for f in base_fc["features"] if f["properties"]["name"] != "Surface car park"] + adds["features"]}
    est = cost.estimate(project, {"features": base_fc}, {"features": alt_fc}, rates=[])
    [g] = comparison._give_up([{"id": "alt", "name": "ALT"}], {"alt": est})
    local = geo.utm_crs_for(30.12, 31.85)
    area = lambda fc, name: geo.to_local(shape(next(f for f in fc["features"] if f["properties"]["name"] == name)["geometry"]), local).area
    removed = area(base_fc, "Surface car park") - area(alt_fc, "Reduced car park")
    assert g["parking_area_removed_m2"] == pytest.approx(removed, abs=0.2) and g["parking_spaces_lost"] == int(removed // 25)
    assert g["trees_added"] == 6 and g["planting_added_m2"] > 0
    assert g["irrigation_l_per_day"] is None and "value required" in g["irrigation_note"]
    assert g["building_footprint_lost_m2"] == 0


# ------------------------------------------------------------------ app and engine
def test_comparison_endpoint_with_budget_and_derived_criteria(client, auth):
    pid = make_project(client, auth)
    upload(client, auth, pid, "sample_rates_TEST_ONLY.csv", "rate_table")
    base, alt = _two_scenarios(client, auth, pid)
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form", "solar_shade"]}})
    body = {"baseline_id": base["id"], "alternative_ids": [alt["id"]], "criteria": [{"metric": "green_space_pct", "weight": 2}, {"metric": "cost_typical", "weight": 1}],
            "budget_amount": 1_000_000, "budget_currency": "XTS"}
    r = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json=body)
    assert r.status_code == 201, r.text
    res = r.json()["result"]
    assert res["comparable"] and res["choice"]["mode"] in ("best_under_priorities", "no_eligible_design")
    assert res["choice"]["budget"] == {"amount": 1_000_000.0, "currency": "XTS"} and res["tradeoffs"]["give_up"][0]["trees_added"] == 6
    bad = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={**body, "budget_currency": "dollars"})
    assert bad.status_code == 422
    plain = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()["result"]
    assert plain["choice"]["mode"] == "no_winner"
    rep = client.post(f"/api/v1/analysis-runs/{r.json()['run_ids'][base['id']]}/reports", headers=auth, json={"comparison_id": r.json()["id"]})
    assert rep.status_code == 201, rep.text
    html = client.get(f"/api/v1/reports/{rep.json()['id']}/download?format=html", headers=auth).text
    assert "The choice" in html and "What each alternative gives up" in html and "Trade-off: cost against heat stress" in html


def test_engine_accepts_derived_criteria_and_a_budget():
    from urbanova.engine.contract import CompareRequest

    req = CompareRequest.model_validate({"baseline_id": "b", "alternative_ids": ["a"], "criteria": [{"metric": "cost_typical", "weight": 1}],
                                         "budget_amount": 5, "budget_currency": "EGP"})
    assert req.budget_amount == 5
    from urbanova import comparison

    assert "cost_typical" in comparison.DIRECTIONS and "compliance_failures" in comparison.DIRECTIONS


def test_no_eligible_design_does_not_claim_robustness():
    scen, metrics, checks, costs = _designs([100, 50], [10, 30], [_cost(10)], fails={"b", "a1"})
    ch = _compare(scen, metrics, checks, costs, criteria=[{"metric": "green_space_pct", "weight": 1}])["choice"]
    assert ch["mode"] == "no_eligible_design" and ch["robustness"]["result"] == "not_assessed" and "Not assessed" in ch["robustness"]["statement"]
