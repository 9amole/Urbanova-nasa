"""Tests for the integration component `urbanova.engine` (contract, validation, decoupling, D4 sharing)."""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from conftest import ROOT, SAMPLES
from test_acceptance import _fake_items, _fake_series, approve

EXAMPLE = ROOT / "examples" / "engine_request_sample.json"


@pytest.fixture()
def request_sample():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_request_runs_and_matches_the_result_contract(env, tmp_path, request_sample):
    from urbanova.engine import AnalysisRequest, AnalysisResult, analyse

    AnalysisRequest.model_validate(request_sample)
    res = analyse(request_sample, tmp_path / "out")
    AnalysisResult.model_validate(res)
    assert res["contract_version"] == "1.2" and res["request_id"] == "example-synthetic-site"
    assert [d["design_id"] for d in res["designs"]] == ["baseline", "alt-greener"]
    base = res["designs"][0]
    # Offline: external modules are honestly unavailable, design modules complete; missing is never 0.
    assert base["modules"]["climate_baseline"]["status"] == "unavailable" and base["modules"]["urban_form"]["status"] == "completed"
    assert all(m["value"] is None for m in base["metrics"] if m["state"] != "available")
    green = next(m for m in base["metrics"] if m["metric_name"] == "green_space_pct")
    assert green["value"] == pytest.approx(9.06, abs=0.01)
    # Verified rules are applied: green space 9.06 % < 20 % fails.
    assert base["findings"][0]["severity"] == "high" and "green-space" in base["findings"][0]["title"].lower()
    sev = [f["severity"] for f in base["findings"]]
    order = {"high": 0, "moderate": 1, "low": 2, "info": 3}
    assert sev == sorted(sev, key=order.get)
    # Layers point at files that exist under the output directory.
    for lay in base["layers"]:
        assert (tmp_path / "out" / lay["file"]).exists(), lay
    c = res["comparison"]
    assert c["comparable"] and c["baseline_id"] == "baseline" and c["ranking"]["small_set_warning"]
    assert c["costs"]["alt-greener"]["totals_by_currency"].get("XTS", 0) > 0
    assert any("provisional" in x for x in res["limitations"])


def test_engine_needs_no_database(env, tmp_path, request_sample):
    from urbanova.engine import analyse

    analyse(request_sample, tmp_path / "out")
    assert not env.db_path.exists(), "the engine must not create or use the prototype database"


def test_same_input_gives_same_fingerprint(env, tmp_path, request_sample):
    from urbanova.engine import analyse

    a = analyse(request_sample, tmp_path / "a")
    other = dict(request_sample, request_id="another-id")
    b = analyse(other, tmp_path / "b")
    assert a["input_fingerprint"] == b["input_fingerprint"]
    changed = copy.deepcopy(request_sample)
    changed["site"]["latitude"] = 30.1201
    assert analyse(changed, tmp_path / "c")["input_fingerprint"] != a["input_fingerprint"]


@pytest.mark.parametrize("mutate, loc_start", [
    (lambda r: r.update(contract_version="2.0"), ["contract_version"]),
    (lambda r: r["site"].update(latitude=200), ["site", "latitude"]),
    (lambda r: r["designs"][1].update(kind="baseline"), ["designs"]),
    (lambda r: r["designs"][0]["features"]["features"][0]["properties"].update(feature_type="castle"), ["designs", 0, "features", "features", 0]),
    (lambda r: r["designs"][0]["features"]["features"][0]["properties"].update(height_m="40 ft"), ["designs", 0, "features", "features", 0]),
    (lambda r: r["rules"]["verified"][3].update(value=150), ["rules", "verified", 3, "value"]),
    (lambda r: r["settings"].update(unknown_setting=1), ["settings", "unknown_setting"]),
    (lambda r: r["settings"].update(lst_buffer_m=99999), ["settings"]),
    (lambda r: r["compare"].update(baseline_id="alt-greener"), ["compare", "baseline_id"]),
    (lambda r: r["rates"][0].update(currency="dollars"), ["rates", 0, "currency"]),
])
def test_invalid_requests_are_refused_with_a_located_message(env, tmp_path, request_sample, mutate, loc_start):
    from urbanova.engine import EngineInputError, analyse

    mutate(request_sample)
    with pytest.raises(EngineInputError) as ei:
        analyse(request_sample, tmp_path / "out")
    locs = [e["loc"] for e in ei.value.errors]
    assert any(loc[:len(loc_start)] == loc_start for loc in locs), (locs, str(ei.value))
    assert all(e["msg"] for e in ei.value.errors)


def test_site_only_request(env, tmp_path):
    from urbanova.engine import AnalysisResult, analyse

    res = analyse({"contract_version": "1.0", "site": {"latitude": 30.12, "longitude": 31.85}}, tmp_path)
    AnalysisResult.model_validate(res)
    [d] = res["designs"]
    # S11: a request without designs is a site analysis; design-only modules are left out with a note, not blocked
    assert d["design_id"] is None and d["analysis_mode"] == "site" and res["comparison"] is None
    assert d["modules"]["urban_form"]["status"] == "skipped" and d["modules"]["urban_form"]["site_mode"] and "design plan" in d["modules"]["urban_form"]["reason"]


def test_site_data_is_fetched_once_and_shared_by_every_design(env, tmp_path, request_sample, monkeypatch):
    """D4: one request = one set of site observations, so designs are compared on identical data."""
    from urbanova.analysis import context, surface_heat, vegetation
    from urbanova.engine import analyse
    from urbanova.providers import weather
    from urbanova.providers.base import ProviderError

    calls = {"weather": 0, "osm": 0, "landsat_search": 0, "s2_search": 0}

    def fake_weather(*a, **k):
        calls["weather"] += 1
        return _fake_series("open_meteo_archive")

    def fake_osm(*a, **k):
        calls["osm"] += 1
        raise ProviderError("osm_overpass", "http_error", "HTTP 504 (simulated)")

    def landsat_search(*a, **k):
        calls["landsat_search"] += 1
        return _fake_items(3)

    def s2_search(*a, **k):
        calls["s2_search"] += 1
        return [{"id": f"S2_TEST_{i}", "properties": {"datetime": f"2025-06-{10 + i:02d}T08:00:00Z", "eo:cloud_cover": 3, "proj:epsg": 32636,
                                                     "s2:processing_baseline": "05.10"},
                 "assets": {"SCL": {"href": "scl"}, "B04": {"href": "red"}, "B08": {"href": "nir"}}} for i in range(2)]

    approve(monkeypatch, "pc_landsat_c2_l2", "pc_sentinel2_l2a")
    monkeypatch.setattr(weather, "fetch_open_meteo", fake_weather)
    monkeypatch.setattr(context, "fetch_context", fake_osm)
    monkeypatch.setattr(surface_heat, "search", landsat_search)
    monkeypatch.setattr(surface_heat, "read_to_grid", lambda pid, href, epsg, crs, t, shape, **k:
                        np.zeros(shape, dtype="float32") if href == "qa" else np.full(shape, 48000.0, dtype="float32"))
    monkeypatch.setattr(vegetation, "search", s2_search)
    monkeypatch.setattr(vegetation, "read_to_grid", lambda pid, href, epsg, crs, t, shape, **k:
                        np.full(shape, 4.0 if href == "scl" else (1600.0 if href == "red" else 3000.0), dtype="float32"))
    res = analyse(request_sample, tmp_path / "out")
    assert calls == {"weather": 1, "osm": 1, "landsat_search": 1, "s2_search": 1}, calls
    base, alt = res["designs"]
    for mod in ("climate_baseline", "site_context", "surface_heat", "vegetation"):
        assert base["modules"][mod]["status"] == alt["modules"][mod]["status"], mod
    assert base["modules"]["site_context"]["status"] == "unavailable"  # the failure is shared too, never mixed
    assert alt["modules"]["climate_baseline"]["shared_across_designs"] is True
    site_metrics = lambda d: {m["metric_name"]: m["value"] for m in d["metrics"] if not m["design_dependent"]}
    assert site_metrics(base) == site_metrics(alt)
    # The design-specific part of vegetation still differs per design.
    planned = [next((m for m in d["metrics"] if m["metric_name"] == "ndvi_planned_green_currently_vegetated_pct"), None) for d in (base, alt)]
    assert all(planned)


def test_extract_plan_returns_reviewable_features(env):
    from urbanova.engine import extract_plan

    out = extract_plan("plan.geojson", (SAMPLES / "sample_plan_baseline.geojson").read_bytes(), {"latitude": 30.12, "longitude": 31.85})
    assert out["detected_type"] == "geojson" and len(out["features"]["features"]) == 12
    f = out["features"]["features"][0]["properties"]
    assert {"feature_type", "review_status", "classification_confidence", "issues"} <= set(f)
    assert not env.db_path.exists()


def test_extract_plan_dxf_without_units_is_flagged(env, tmp_path):
    from test_remediation import _write_dxf

    from urbanova.engine import EngineInputError, analyse, extract_plan

    dxf = _write_dxf(tmp_path / "p.dxf", 0)
    out = extract_plan("p.dxf", dxf.read_bytes(), {"latitude": 30.12, "longitude": 31.85}, epsg="32636")
    assert out["summary"]["units_unconfirmed"] is True
    assert all(f["properties"]["units_unconfirmed"] for f in out["features"]["features"])
    req = {"contract_version": "1.0", "site": {"latitude": 30.12, "longitude": 31.85},
           "designs": [{"id": "b", "name": "b", "kind": "baseline", "features": out["features"]}]}
    with pytest.raises(EngineInputError, match="unconfirmed units"):
        analyse(req, tmp_path / "out")


def test_extract_rules_from_the_sample_pdf(env):
    from urbanova.engine import extract_rules

    out = extract_rules("c.pdf", (SAMPLES / "sample_planning_conditions.pdf").read_bytes())
    types = {c["rule_type"] for c in out["candidates"]}
    assert {"max_building_coverage_pct", "min_green_space_pct", "plot_area_m2"} <= types
    assert out["conflicts"] and out["conflicts"][0]["rule_type"] == "plot_area_m2"
    assert all(c["page"] and c["exact_text"] for c in out["candidates"])


def test_cli_analyse_and_input_errors(env, tmp_path, request_sample, capsys):
    from urbanova.engine.__main__ import main

    req = tmp_path / "req.json"
    req.write_text(json.dumps(request_sample), encoding="utf-8")
    assert main(["analyse", str(req), "--out", str(tmp_path / "o"), "--result", str(tmp_path / "res.json")]) == 0
    assert main(["validate", str(tmp_path / "res.json")]) == 0
    bad = dict(request_sample, site={"latitude": 999, "longitude": 0})
    req.write_text(json.dumps(bad), encoding="utf-8")
    capsys.readouterr()
    assert main(["analyse", str(req), "--out", str(tmp_path / "o")]) == 2
    err = json.loads(capsys.readouterr().out)
    assert err["error"] == "invalid_input" and err["errors"][0]["loc"][:2] == ["site", "latitude"]


def test_exported_schemas_are_current(tmp_path):
    """schemas/ in the repository must match the contract models (regenerate with `python -m urbanova.engine schema --out schemas`)."""
    from urbanova.engine.__main__ import main

    main(["schema", "--out", str(tmp_path)])
    for name in ("analysis_request", "analysis_result"):
        assert (ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8") == (tmp_path / f"{name}.schema.json").read_text(encoding="utf-8"), name


@pytest.mark.parametrize("props, expected", [({"leisure": "park"}, "green_space"), ({"leisure": "garden"}, "green_space"),
                                             ({"amenity": "parking"}, "parking"), ({"natural": "water"}, "water"), ({"leisure": "pitch"}, "unclassified")])
def test_gis_export_tags_are_classified(props, expected):
    """Found in the D2 real-geometry run: OSM-style GIS exports carry leisure/natural/amenity tags."""
    from urbanova import plans

    assert plans.classify(props)[0] == expected


def test_ai_functions_accept_an_engine_result(env, tmp_path, request_sample):
    """INTEGRATION_CONTRACT §5: a backend without the prototype database feeds engine results to the AI service."""
    from urbanova.ai import answer_question, bundle_from_result, chat_turn, comparison_from_result, generate_insights
    from urbanova.ai.chat import Tools
    from urbanova.engine import analyse

    res = analyse(request_sample, tmp_path / "out")
    b = bundle_from_result(res)
    assert b["run"]["design_id"] == "baseline" and b["metrics"] and bundle_from_result(res, "alt-greener")["run"]["design_id"] == "alt-greener"
    with pytest.raises(KeyError):
        bundle_from_result(res, "no-such-design")
    ins = generate_insights(b, ai_allowed=False)
    assert ins["generated_by"] == "fallback" and ins["blocks"]["site_status"]
    assert answer_question(b, "Which hotspot comes first?", ai_allowed=False) is None  # → caller shows the automatic answer
    cmp_ = comparison_from_result(res)
    reply = chat_turn(b, "What is the green-space proportion?", [], False, comparison=cmp_)
    assert reply["generated_by"] == "fallback" and reply["answer"]
    assert chat_turn(b, "Delete hotspot 1", [], True)["refusal"] == "read_only"
    rows = Tools(b, cmp_).call("compare", {})[0]["comparison"]["rows"]
    alt_name = next(d["design_name"] for d in res["designs"] if d["design_id"] == "alt-greener")
    assert rows and {r["alternative"] for r in rows} == {alt_name}
