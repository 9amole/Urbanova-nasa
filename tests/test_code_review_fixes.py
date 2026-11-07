"""Regression tests for the high-effort code review of urbanova/ (2026-11-07)."""

import copy
import json

import pytest

from conftest import ROOT, SAMPLES, make_project, upload
from test_acceptance import _fake_series

EXAMPLE = ROOT / "examples" / "engine_request_sample.json"


@pytest.fixture()
def request_sample():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_reference_only_plan_with_replace_keeps_existing_features(client, auth):
    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    before = len(client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["geojson"]["features"])
    r = upload(client, auth, pid, "sample_planning_conditions.pdf", "urban_plan", plan_mode="replace")
    assert r.status_code == 201, r.text
    assert r.json()["outcome"]["status"] == "stored_reference_only" and "replaced_features" not in r.json()["outcome"]
    assert len(client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["geojson"]["features"]) == before


def test_extract_plan_accepts_feature_boundary_and_rejects_bad_one(env):
    from urbanova.engine import EngineInputError, extract_plan

    boundary = json.loads((SAMPLES / "sample_site_boundary.geojson").read_text(encoding="utf-8"))
    feature = boundary if boundary.get("type") == "Feature" else {"type": "Feature", "properties": {}, "geometry": boundary["features"][0]["geometry"] if boundary.get("type") == "FeatureCollection" else boundary}
    plan = (SAMPLES / "sample_plan_baseline.geojson").read_bytes()
    for b in (boundary, feature):
        out = extract_plan("plan.geojson", plan, {"latitude": 30.12, "longitude": 31.85, "boundary": b})
        assert len(out["features"]["features"]) == 12
    with pytest.raises(EngineInputError) as ei:
        extract_plan("plan.geojson", plan, {"latitude": 30.12, "longitude": 31.85, "boundary": {"type": "Point", "coordinates": [31.85, 30.12]}})
    assert ei.value.errors[0]["loc"] == ["site"]


@pytest.mark.parametrize("mutate, loc", [
    (lambda r: r["compare"].update(alternative_ids=["does-not-exist"]), ["compare", "alternative_ids", 0]),
    (lambda r: r["compare"].update(criteria=[{"metric": "building_count", "weight": 1}]), ["compare", "criteria", 0, "metric"]),
])
def test_bad_comparison_fails_before_any_analysis(env, tmp_path, request_sample, monkeypatch, mutate, loc):
    from urbanova.engine import EngineInputError, analyse
    from urbanova.engine import api

    def must_not_run(*a, **k):
        raise AssertionError("analysis ran before the comparison was validated")

    monkeypatch.setattr(api, "run_design", must_not_run)
    mutate(request_sample)
    with pytest.raises(EngineInputError) as ei:
        analyse(request_sample, tmp_path)
    assert ei.value.errors[0]["loc"] == loc


def test_unusual_features_give_located_errors_not_crashes(env, tmp_path, request_sample):
    from urbanova.engine import EngineInputError, analyse

    bad = copy.deepcopy(request_sample)
    bad["designs"][0]["features"]["features"][0] = "not a feature"
    with pytest.raises(EngineInputError) as ei:
        analyse(bad, tmp_path / "a")
    assert ei.value.errors[0]["loc"] == ["designs", 0, "features", "features", 0]
    gc = copy.deepcopy(request_sample)
    pt = {"type": "Point", "coordinates": [31.8501, 30.1201]}
    gc["designs"][0]["features"]["features"].append(
        {"type": "Feature", "properties": {"feature_type": "unclassified"}, "geometry": {"type": "GeometryCollection", "geometries": [pt]}})
    res = analyse(gc, tmp_path / "b")  # validation must not raise KeyError on a GeometryCollection
    assert res["designs"][0]["design_id"] == "baseline"


def test_design_output_folders_never_collide(env, tmp_path, request_sample):
    from urbanova.engine import analyse

    req = copy.deepcopy(request_sample)
    req["designs"][0]["id"] = "alt:1"
    req["designs"][1]["id"] = "alt_1"
    req["designs"][0]["kind"], req["designs"][1]["kind"] = "baseline", "alternative"
    req["compare"] = None
    res = analyse(req, tmp_path)
    files = [{lay["file"] for lay in d["layers"]} for d in res["designs"]]
    assert files[0] and files[1] and not files[0] & files[1]
    for d in res["designs"]:
        for lay in d["layers"]:
            assert (tmp_path / lay["file"]).exists()


def test_run_all_shares_site_data_and_runs_every_scenario(client, auth, monkeypatch):
    from urbanova.providers import weather

    calls = []
    monkeypatch.setattr(weather, "fetch_open_meteo", lambda *a, **k: calls.append(1) or _fake_series("open_meteo_archive"))
    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"})
    for name in ("A", "B", "C"):
        client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": name})
    runs = client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth,
                       json={"config": {"modules": ["climate_baseline", "urban_form"]}}).json()
    assert len(runs) == 4 and len(calls) == 1  # one climate fetch for four scenarios
    states = [client.get(f"/api/v1/analysis-runs/{r['id']}", headers=auth).json()["run"]["status"] for r in runs]
    assert all(s in ("succeeded", "partially_succeeded") for s in states), states


def test_units_rule_has_one_helper_for_every_shape():
    from urbanova import plans

    assert plans.has_unconfirmed_units([{"attributes": {"units_unconfirmed": True}}])
    assert plans.has_unconfirmed_units([{"properties": {"units_unconfirmed": True}}])
    assert plans.has_unconfirmed_units([{"units_unconfirmed": True}])
    assert not plans.has_unconfirmed_units([{"attributes": {}}, {"properties": {"height_m": 3}}, {}])


def test_identical_requests_share_one_lock():
    from urbanova.providers import base

    key = base.cache_key("osm_overpass", {"q": "x"})
    pick = lambda k: base._KEY_LOCKS[int(k[:8], 16) % len(base._KEY_LOCKS)]
    assert pick(key) is pick(base.cache_key("osm_overpass", {"q": "x"}))
    assert len(base._KEY_LOCKS) == 64
