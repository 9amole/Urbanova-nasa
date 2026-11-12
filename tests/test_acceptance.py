"""Phase 1 acceptance tests (FR §17 and B.7). External providers are mocked or offline."""

import json
from datetime import date, timedelta

import numpy as np
import pytest

from conftest import SAMPLES, boundary, login, make_project, run, upload


def metric(bundle, name):
    return next((m for m in bundle["metrics"] if m["metric_name"] == name), None)


def baseline_with_plan(client, auth, pid):
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    r = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- §17 Coordinates only
def test_coordinates_only_project_runs_available_analyses(client, auth):
    pid = make_project(client, auth, with_boundary=False)
    p = client.get(f"/api/v1/projects/{pid}", headers=auth).json()
    items = [m["item"] for m in p["missing_information"]]
    assert "Site boundary" in items and "Urban plan / design geometry" in items
    b = run(client, auth, pid)
    ms = b["run"]["module_status"]
    # Offline: external modules are honestly unavailable; without a design this is a site analysis (S11): the design
    # modules are left out with a note, and only the sun position (no network) is produced, so the run is partial.
    assert ms["climate_baseline"]["status"] == "unavailable"
    assert ms["urban_form"]["status"] == "skipped" and "design plan" in ms["urban_form"]["reason"]
    assert ms["solar_shade"]["status"] == "completed"
    assert b["run"]["status"] == "partially_succeeded"  # not reported as success while the site data is unavailable
    assert all(m["state"] != "available" or m["value"] is not None for m in b["metrics"])


def test_invalid_coordinates_rejected_with_explanation(client, auth):
    r = client.post("/api/v1/projects", headers=auth, json={"name": "x", "latitude": 95, "longitude": 31})
    assert r.status_code == 422 and "Latitude" in r.json()["detail"]
    r = client.post("/api/v1/projects", headers=auth, json={"name": "x", "latitude": 0, "longitude": 0})
    assert r.status_code == 422 and "Null Island" in r.json()["detail"]
    r = client.post("/api/v1/projects", headers=auth, json={"name": "x", "latitude": 30.1200, "longitude": 31.8500,
                                                             "boundary": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]}})
    assert r.status_code == 422  # bow-tie polygon is invalid / far away


def test_run_requires_location_confirmation(client, auth):
    pid = make_project(client, auth, confirm=False)
    r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={})
    assert r.status_code == 422 and "Confirm the site location" in r.json()["detail"]


def test_moving_site_requires_reconfirmation(client, auth):
    pid = make_project(client, auth)
    client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"latitude": 30.1201})
    assert client.get(f"/api/v1/projects/{pid}", headers=auth).json()["project"]["location_confirmed"] is False


# ---------------------------------------------------------------- §17 Coordinates + urban plan
def test_plan_extraction_and_metrics(client, auth):
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan")
    out = r.json()["outcome"]
    assert out["features_extracted"] == 12 and out["by_type"]["building"] == 5
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    b = run(client, auth, pid, base["id"])
    assert b["run"]["module_status"]["urban_form"]["status"] == "completed"
    assert metric(b, "building_coverage_ratio_pct")["value"] == pytest.approx(19.0, abs=0.1)
    assert metric(b, "site_area_m2")["value"] == pytest.approx(40000, rel=0.001)
    assert metric(b, "building_footprint_m2")["output_kind"] == "calculated"


def test_plan_in_projected_crs_without_epsg_is_rejected(client, auth):
    pid = make_project(client, auth)
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"feature_type": "building"},
                                                      "geometry": {"type": "Polygon", "coordinates": [[[350000, 3322000], [350050, 3322000], [350050, 3322050], [350000, 3322000]]]}}]}
    r = upload(client, auth, pid, "p.geojson", "urban_plan", json.dumps(fc).encode())
    assert r.status_code == 422 and "EPSG" in r.json()["detail"]
    assert r.json()["file"]["status"] == "failed"  # original preserved, status recorded


def test_projected_geojson_with_declared_epsg_is_reprojected(client, auth):
    pid = make_project(client, auth)
    from pyproj import Transformer

    x, y = Transformer.from_crs(4326, 32636, always_xy=True).transform(31.8500, 30.1200)
    ring = [[x, y], [x + 20, y], [x + 20, y + 20], [x, y + 20], [x, y]]
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"feature_type": "building"}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    r = upload(client, auth, pid, "p.geojson", "urban_plan", json.dumps(fc).encode(), epsg="32636")
    assert r.status_code == 201 and r.json()["outcome"]["source_crs"] == "EPSG:32636"


# ---------------------------------------------------------------- §17 Invalid plan scale
def test_image_plan_is_not_used_for_areas(client, auth):
    pid = make_project(client, auth)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = upload(client, auth, pid, "plan.png", "urban_plan", png)
    assert r.status_code == 201
    assert r.json()["outcome"]["status"] == "stored_reference_only" and r.json()["outcome"]["features_extracted"] == 0
    rd = client.post(f"/api/v1/projects/{pid}/readiness-check", headers=auth, json={}).json()
    assert next(a for a in rd["analyses"] if a["analysis"] == "urban_form")["status"] == "blocked"


def test_dxf_requires_epsg(client, auth):
    pid = make_project(client, auth)
    dxf = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
    r = upload(client, auth, pid, "plan.dxf", "urban_plan", dxf)
    assert r.status_code == 422 and "EPSG" in r.json()["detail"]


def test_dxf_extraction_with_epsg(client, auth, tmp_path):
    import ezdxf
    from pyproj import Transformer

    x, y = Transformer.from_crs(4326, 32636, always_xy=True).transform(31.8500, 30.1200)
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(x, y), (x + 30, y), (x + 30, y + 20), (x, y + 20)], close=True, dxfattribs={"layer": "BUILDINGS"})
    msp.add_lwpolyline([(x - 10, y - 10), (x + 40, y - 10), (x + 40, y - 2), (x - 10, y - 2)], close=True, dxfattribs={"layer": "GREEN_AREA"})
    path = tmp_path / "t.dxf"
    doc.saveas(path)
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "t.dxf", "urban_plan", path.read_bytes(), epsg="32636")
    assert r.status_code == 201, r.text
    assert r.json()["outcome"]["by_type"] == {"building": 1, "green_space": 1}


# ---------------------------------------------------------------- §17 Planning documents
def test_planning_document_candidates_sources_and_ambiguity(client, auth):
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "sample_planning_conditions.pdf", "planning_document")
    out = r.json()["outcome"]
    assert out["candidates"] >= 6
    assert any(a["rule_type"] == "plot_area_m2" for a in out["ambiguities"])
    rules = client.get(f"/api/v1/projects/{pid}/rules", headers=auth).json()["rules"]
    cov = next(x for x in rules if x["rule_type"] == "max_building_coverage_pct")
    assert cov["value"] == 35 and cov["page"] == 1 and "net plot area" in cov["exact_text"] and cov["status"] == "candidate"
    green = next(x for x in rules if x["rule_type"] == "min_green_space_pct")
    assert green["page"] == 2
    assert {x["value"] for x in rules if x["rule_type"] == "plot_area_m2"} == {40000.0, 42000.0}
    # "6 floors (G+5)" is consistent: G+5 = ground + 5 = 6 floors, so no false conflict.
    assert {x["value"] for x in rules if x["rule_type"] == "max_floors"} == {6.0}
    assert not any(a["rule_type"] == "max_floors" for a in out["ambiguities"])


def test_missing_rules_means_not_assessed(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    upload(client, auth, pid, "sample_planning_conditions.pdf", "planning_document")  # candidates only
    b = run(client, auth, pid, base["id"])
    pc = b["run"]["module_status"]["planning_compliance"]
    assert pc["status"] == "blocked" and "not assessed" in pc["reason"]
    assert not any(f["classification"] == "planning_rule_check" for f in b["findings"])


def test_conflicting_plot_areas_block_ratio_checks(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    upload(client, auth, pid, "sample_planning_conditions.pdf", "planning_document")
    rules = client.get(f"/api/v1/projects/{pid}/rules", headers=auth).json()["rules"]
    for rt in ("max_building_coverage_pct", "max_height_m"):
        rid = next(x["id"] for x in rules if x["rule_type"] == rt)
        client.post(f"/api/v1/rules/{rid}/decision", headers=auth, json={"decision": "verified"})
    b = run(client, auth, pid, base["id"])
    checks = {c["rule_type"]: c for c in b["run"]["module_status"]["planning_compliance"]["details"]["checks"]}
    assert checks["max_building_coverage_pct"]["status"] == "not_assessed" and "conflicting plot areas" in checks["max_building_coverage_pct"]["reason"]
    assert checks["max_height_m"]["status"] == "passes"  # non-ratio checks unaffected
    # Resolve by verifying the 42,000 m² figure (4.8% from the 40,000 m² boundary: within the 5% tolerance).
    rid = next(x["id"] for x in rules if x["rule_type"] == "plot_area_m2" and x["value"] == 42000)
    client.post(f"/api/v1/rules/{rid}/decision", headers=auth, json={"decision": "verified"})
    b = run(client, auth, pid, base["id"])
    c = {c["rule_type"]: c for c in b["run"]["module_status"]["planning_compliance"]["details"]["checks"]}["max_building_coverage_pct"]
    assert c["status"] == "passes" and c["measured"] == pytest.approx(19.0, abs=0.1)  # boundary area basis
    client.post(f"/api/v1/projects/{pid}/site-area-basis", headers=auth, json={"basis": "document"})
    b = run(client, auth, pid, base["id"])
    c = {c["rule_type"]: c for c in b["run"]["module_status"]["planning_compliance"]["details"]["checks"]}["max_building_coverage_pct"]
    assert c["status"] == "passes" and c["measured"] == pytest.approx(7600.7 / 42000 * 100, rel=0.01)


def test_verified_rule_failure_creates_planning_finding(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    client.post(f"/api/v1/projects/{pid}/rules", headers=auth, json={"rule_type": "min_green_space_pct", "value": 20, "source": "Test clause 7"})
    rid = client.get(f"/api/v1/projects/{pid}/rules", headers=auth).json()["rules"][0]["id"]
    client.post(f"/api/v1/rules/{rid}/decision", headers=auth, json={"decision": "verified"})
    b = run(client, auth, pid, base["id"])
    f = next(f for f in b["findings"] if f["classification"] == "planning_rule_check")
    assert f["severity"] == "high" and "not a statutory" in f["limitations"][0]
    assert any(r["finding_id"] == f["id"] for r in b["recommendations"])


# ---------------------------------------------------------------- §17 Missing building heights
def test_missing_heights_footprints_ok_shade_not_claimed(client, auth):
    pid = make_project(client, auth)
    fc = json.loads((SAMPLES / "sample_plan_baseline.geojson").read_text())
    for f in fc["features"]:
        if f["properties"]["feature_type"] == "building":
            f["properties"].pop("height_m", None)
            f["properties"].pop("floors", None)
    upload(client, auth, pid, "noh.geojson", "urban_plan", json.dumps(fc).encode())
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    b = run(client, auth, pid, base["id"])
    assert metric(b, "building_coverage_ratio_pct")["state"] == "available"
    assert metric(b, "max_building_height_m")["state"] == "unknown"
    assert metric(b, "floor_area_ratio")["state"] == "unknown" and metric(b, "floor_area_ratio")["value"] is None
    # The sample tree has height+canopy so it casts shade; buildings without height add a limitation.
    st = b["run"]["module_status"]["solar_shade"]
    assert st["status"] == "partial" and any("no height" in w for w in st["warnings"])
    rd = client.post(f"/api/v1/projects/{pid}/readiness-check", headers=auth, json={"scenario_id": base["id"]}).json()
    assert next(a for a in rd["analyses"] if a["analysis"] == "solar_shade")["status"] == "ready_with_limitations"


def test_no_heights_at_all_blocks_shade_metrics(client, auth):
    pid = make_project(client, auth)
    fc = json.loads((SAMPLES / "sample_plan_baseline.geojson").read_text())
    for f in fc["features"]:
        f["properties"].pop("height_m", None)
    upload(client, auth, pid, "noh.geojson", "urban_plan", json.dumps(fc).encode())
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    b = run(client, auth, pid, base["id"])
    m = metric(b, "solar_open_space_shade_hours")
    assert m["state"] == "blocked" and m["value"] is None and "heights" in m["reason"]
    assert metric(b, "solar_elevation_deg_1200")["state"] == "available"  # sun position still calculated


# ---------------------------------------------------------------- §17 API unavailable / weather fallback
def _fake_series(provider):
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(731)]
    tmax = [25 + 12 * np.sin((d.timetuple().tm_yday - 100) / 365 * 2 * np.pi) for d in days]
    return {
        "provider": provider, "provider_name": provider, "model": "test", "dates": [d.isoformat() for d in days],
        "series": {"tmax_c": tmax, "tmin_c": [t - 12 for t in tmax], "rh_mean_pct": [40.0] * len(days)}, "units": {"tmax_c": "°C", "tmin_c": "°C", "rh_mean_pct": "%"},
        "grid_point": {"latitude": 30, "longitude": 31.5, "elevation_m": 300}, "timezone": "x", "spatial_resolution": "test", "qc": {"days": len(days), "missing": {"tmax_c": 0}, "out_of_range": {}},
        "meta": {"cache_key": "k", "retrieved_at": "now", "raw_path": None}, "request_params": {}, "attribution": "test", "license": "test",
        "transformations": [], "excluded_variables": [{"variable": "wind_max_10m_kmh", "reason": "wind excluded"}],
    }


def test_primary_weather_failure_uses_disclosed_fallback(client, auth, monkeypatch):
    from urbanova.providers import weather
    from urbanova.providers.base import ProviderError

    def fail(*a, **k):
        raise ProviderError("open_meteo_archive", "http_error", "HTTP 503")

    monkeypatch.setattr(weather, "fetch_open_meteo", fail)
    monkeypatch.setattr(weather, "fetch_nasa_power", lambda *a, **k: _fake_series("nasa_power_daily"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    st = b["run"]["module_status"]["climate_baseline"]
    assert st["status"] == "partial"
    assert any("fallback" in w for w in st["warnings"]) and st["provider_failures"][0]["kind"] == "http_error"
    assert b["datasets"][0]["source_id"] == "nasa_power_daily"
    assert st["details"]["summary"]["hottest_months"] == [6, 7, 8]


def test_api_failure_does_not_stop_unrelated_modules(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    b = run(client, auth, pid, base["id"])  # offline: all providers fail
    ms = b["run"]["module_status"]
    assert ms["climate_baseline"]["status"] == "unavailable" and ms["urban_form"]["status"] == "completed"
    assert ms["climate_baseline"]["provider_failures"]  # the failed sources are reported
    assert b["run"]["status"] == "partially_succeeded"
    assert any(f["title"].startswith("Climate baseline") and f["classification"] == "data_quality_issue" for f in b["findings"])


# ---------------------------------------------------------------- §17 Cloud-covered satellite imagery
def _fake_items(n=4):
    return [{"id": f"LC09_TEST_{i}", "properties": {"datetime": f"2025-07-{10 + i:02d}T08:00:00Z", "eo:cloud_cover": 5, "platform": "landsat-9", "proj:epsg": 32636},
             "assets": {"qa_pixel": {"href": "qa"}, "lwir11": {"href": "st"}}} for i in range(n)]


def approve(monkeypatch, *ids):
    """Simulate a supervisor approval of a provider for the duration of one test."""
    from urbanova.providers.base import registry

    for i in ids:
        monkeypatch.setitem(registry()[i], "approval_status", "approved")


def test_cloud_covered_scenes_are_excluded_not_invented(client, auth, monkeypatch):
    from urbanova.analysis import surface_heat

    approve(monkeypatch, "pc_landsat_c2_l2")
    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: _fake_items())
    monkeypatch.setattr(surface_heat, "read_to_grid", lambda pid, href, epsg, crs, t, shape, **k: np.full(shape, 8.0 if href == "qa" else 48000.0, dtype="float32"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7]})
    st = b["run"]["module_status"]["surface_heat"]
    assert st["status"] == "unavailable"
    assert len(st["details"]["excluded_scenes"]) == 4 and all("clear" in s["reason"] for s in st["details"]["excluded_scenes"])
    assert not [m for m in b["metrics"] if m["module"] == "surface_heat"]


def test_clear_scenes_produce_relative_lst(client, auth, monkeypatch):
    from conftest import unapprove
    from urbanova.analysis import surface_heat

    unapprove(monkeypatch, "finding.HEAT-LST-02")

    approve(monkeypatch, "pc_landsat_c2_l2")
    def read(pid, href, epsg, crs, t, shape, **k):
        if href == "qa":
            return np.zeros(shape, dtype="float32")
        arr = np.full(shape, (40 + 273.15 - 149.0) / 0.00341802, dtype="float32")
        r, c = shape
        arr[r // 2 - 4:r // 2 + 4, c // 2 - 4:c // 2 + 4] = (46 + 273.15 - 149.0) / 0.00341802  # hot site centre
        return arr

    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: _fake_items(3))
    monkeypatch.setattr(surface_heat, "read_to_grid", read)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7]})
    assert any("native 100 m thermal pixels" in w for w in b["run"]["module_status"]["surface_heat"]["warnings"])
    assert metric(b, "lst_surroundings_median_c")["value"] == pytest.approx(40.0, abs=0.05)
    assert metric(b, "lst_site_median_c")["output_kind"] == "observed"
    assert metric(b, "lst_site_hottest_decile_share_pct")["output_kind"] == "classified"
    f = next(f for f in b["findings"] if f["rule_id"] == "HEAT-LST-02")
    assert "PROVISIONAL threshold, not approved" in f["severity_rule"] and f["threshold"]["status"] == "provisional_not_approved"
    # 4 ha sample site < 9 native 100 m thermal pixels → explicit resolution warning, partial status.
    assert b["run"]["module_status"]["surface_heat"]["status"] == "partial"


# ---------------------------------------------------------------- §17 Two design alternatives / changed source
def _two_scenarios(client, auth, pid):
    base = baseline_with_plan(client, auth, pid)
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "Alt"}).json()
    park = next(f for f in alt["features"]["features"] if f["properties"].get("name") == "Surface car park")
    client.patch(f"/api/v1/scenarios/{alt['id']}", headers=auth, json={"changes": [{"op": "remove", "feature_id": park["id"]}]})
    r = client.post(f"/api/v1/scenarios/{alt['id']}/import", headers=auth, files={"file": ("a.geojson", (SAMPLES / "sample_alternative_additions.geojson").read_bytes())})
    assert r.status_code == 200, r.text
    return base, alt


def test_two_alternatives_compared_consistently(client, auth):
    pid = make_project(client, auth)
    base, alt = _two_scenarios(client, auth, pid)
    runs = client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {}}).json()
    assert len(runs) == 2
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    res = c["result"]
    assert res["comparable"]
    rows = {r["metric"]: r for r in res["rows"]}
    assert rows["green_space_pct"]["outcome"] == "improvement"
    assert rows["hard_surface_pct"]["outcome"] == "improvement"
    assert rows["building_coverage_ratio_pct"]["outcome"] == "unchanged"
    assert rows["solar_open_space_exposed_pct_1500"]["outcome"] in ("improvement", "regression", "unchanged")
    assert res["ranking"] is None  # no weights → no fabricated ranking
    assert "universally optimal" in res["statement"]


def test_comparison_refuses_different_settings_and_stale_runs(client, auth):
    pid = make_project(client, auth)
    base, alt = _two_scenarios(client, auth, pid)
    run(client, auth, pid, base["id"], {"context_buffer_m": 500})
    run(client, auth, pid, alt["id"], {"context_buffer_m": 800})
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    assert not c["result"]["comparable"] and "different settings" in " ".join(c["result"]["issues"])
    run(client, auth, pid, alt["id"], {"context_buffer_m": 500})
    tree = next(f for f in client.get(f"/api/v1/scenarios/{alt['id']}", headers=auth).json()["features"]["features"] if f["properties"]["feature_type"] == "building")
    client.patch(f"/api/v1/scenarios/{alt['id']}", headers=auth, json={"changes": [{"op": "update", "feature_id": tree["id"], "properties": {"height_m": 30}}]})
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    assert not c["result"]["comparable"] and "edited after its last run" in " ".join(c["result"]["issues"])


def test_baseline_is_immutable_and_changes_mark_results_stale(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    fid = base["features"]["features"][0]["id"]
    r = client.patch(f"/api/v1/scenarios/{base['id']}", headers=auth, json={"changes": [{"op": "remove", "feature_id": fid}]})
    assert r.status_code == 422 and "immutable" in r.json()["detail"]
    b = run(client, auth, pid, base["id"])
    rid = b["run"]["id"]
    assert b["run"]["stale_modules"] == {}
    # Change the source boundary: geometry-dependent modules become stale, climate does not.
    bd = boundary()
    bd["features"][0]["geometry"]["coordinates"][0][0][0] += 0.0001
    bd["features"][0]["geometry"]["coordinates"][0][-1][0] += 0.0001
    assert client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"boundary": bd}).status_code == 200
    stale = client.get(f"/api/v1/analysis-runs/{rid}", headers=auth).json()["run"]["stale_modules"]
    assert "urban_form" in stale and "climate_baseline" not in stale
    # The earlier run and its metrics are preserved.
    assert client.get(f"/api/v1/analysis-runs/{rid}/metrics", headers=auth).json()


def test_feature_correction_preserves_original(client, auth):
    pid = make_project(client, auth)
    upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan")
    f = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["features"][0]
    r = client.patch(f"/api/v1/features/{f['id']}", headers=auth, json={"feature_type": "open_space", "height_m": None})
    upd = r.json()
    assert upd["feature_type"] == "open_space" and upd["classification_source"] == "user correction"
    assert upd["original_geometry"] == f["original_geometry"]
    assert upd["correction_history"][-1]["before"]["feature_type"] == "building"


# ---------------------------------------------------------------- §17 Incomplete analysis report
def test_partial_run_report_is_labelled_partial(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    b = run(client, auth, pid, base["id"])
    rep = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth, json={}).json()
    assert rep["status"] == "partial" and "climate_baseline" in rep["summary"]["incomplete_modules"]
    html = client.get(f"/api/v1/reports/{rep['id']}/download?format=html", headers=auth).text
    assert "Partial" in html and "Data unavailable" in html and "not a statutory approval" in html
    pdf = client.get(f"/api/v1/reports/{rep['id']}/download?format=pdf", headers=auth)
    assert pdf.content.startswith(b"%PDF-")
    z = client.get(f"/api/v1/analysis-runs/{b['run']['id']}/export", headers=auth)
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert {"run.json", "metrics.csv", "design_features.geojson", "findings.geojson"} <= set(names)


# ---------------------------------------------------------------- §17 Unsupported AI question
def test_unsupported_question_is_not_answered(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    b = run(client, auth, pid, base["id"])
    rid = b["run"]["id"]
    a = client.post(f"/api/v1/analysis-runs/{rid}/ask", headers=auth, json={"question": "How many degrees cooler will the park make the street?"}).json()
    assert a["sufficient_evidence"] is False and "no validated model" in a["answer"]
    a = client.post(f"/api/v1/analysis-runs/{rid}/ask", headers=auth, json={"question": "What is the noise level at night?"}).json()
    assert a["sufficient_evidence"] is False
    a = client.post(f"/api/v1/analysis-runs/{rid}/ask", headers=auth, json={"question": "What is the building coverage?"}).json()
    assert a["sufficient_evidence"] is True and a["based_on"]


# ---------------------------------------------------------------- B.7 backend tests
def test_misleading_extension_detected_by_content(client, auth):
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "conditions.png", "planning_document", (SAMPLES / "sample_planning_conditions.pdf").read_bytes())
    assert r.status_code == 201 and any("does not match its content" in w for w in r.json()["warnings"])
    r = upload(client, auth, pid, "plan.geojson", "urban_plan", b"MZ\x90\x00 executable bytes")
    assert r.status_code == 422 and "not a supported format" in r.json()["detail"]
    r = upload(client, auth, pid, "x.pdf", "rate_table", (SAMPLES / "sample_planning_conditions.pdf").read_bytes())
    assert r.status_code == 422


def test_raster_with_unknown_crs_is_flagged(client, auth, tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "nocrs.tif"
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32", transform=from_origin(0, 4, 1, 1)) as d:
        d.write(np.ones((1, 4, 4), dtype="float32"))
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "nocrs.tif", "supplementary_raster", path.read_bytes(), source="Test survey", variable="lst", unit="°C")
    assert r.status_code == 201
    assert r.json()["outcome"]["status"] == "flagged_unknown_crs"
    ds = client.get(f"/api/v1/projects/{pid}/datasets", headers=auth).json()[0]
    assert ds["acquisition_method"] == "manual_upload" and ds["quality"]["status"] == "flagged_unknown_crs"


def test_rate_limit_bounded_retry_and_secret_redaction(env, monkeypatch):
    import httpx

    from urbanova import db
    from urbanova.providers import base

    monkeypatch.setattr(env, "offline", False)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="slow down")
        return httpx.Response(200, json={"ok": True})

    real = httpx.Client
    monkeypatch.setattr(base.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    body, meta = base.http_json("open_meteo_archive", "GET", "https://example.test/x?sig=SECRETSIG&a=1")
    assert body == {"ok": True} and calls["n"] == 2
    logs = db.select("provider_logs", "1=1", [], order="id")
    assert [l["outcome"] for l in logs] == ["rate_limited", "success"]
    assert all("SECRETSIG" not in (l["url"] or "") for l in logs)


def test_provider_auth_failure_not_retried(env, monkeypatch):
    import httpx

    from urbanova.providers import base

    monkeypatch.setattr(env, "offline", False)
    real = httpx.Client
    monkeypatch.setattr(base.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(403)), **kw))
    with pytest.raises(base.ProviderError) as e:
        base.http_json("osm_overpass", "GET", "https://example.test/")
    assert e.value.kind == "auth_failed"


def test_disabled_provider_is_never_called(env):
    from urbanova.providers import base

    with pytest.raises(base.ProviderError) as e:
        base.http_json("copernicus_cdse", "GET", "https://example.test/")
    assert e.value.kind == "disabled"


# ---------------------------------------------------------------- costs, values, access control
def test_cost_without_rates_gives_quantities_only(client, auth):
    pid = make_project(client, auth)
    base, alt = _two_scenarios(client, auth, pid)
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form"]}})
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    cost = c["result"]["costs"][alt["id"]]
    assert cost["status"] == "quantities_only" and cost["totals_by_currency"] == {}
    assert {ln["quantity_type"] for ln in cost["lines"]} >= {"green_space_added_m2", "trees_added_count", "hard_surface_removed_m2"}
    assert all(ln["status"] == "price_required" for ln in cost["lines"])


def test_rate_validation(client, auth):
    pid = make_project(client, auth)
    bad = {"quantity_type": "trees_added_count", "unit": "m2", "rate": 5, "currency": "EGP", "price_date": "2026-01-01", "source": "q"}
    r = client.post(f"/api/v1/projects/{pid}/rates", headers=auth, json=bad)
    assert r.status_code == 422 and "does not match" in r.json()["detail"]
    r = client.post(f"/api/v1/projects/{pid}/rates", headers=auth, json={**bad, "unit": "each", "source": ""})
    assert r.status_code == 422 and "source" in r.json()["detail"]


def test_value_state_invariant():
    from urbanova import values as V

    with pytest.raises(ValueError):
        V.metric("x", "x", 0.0, "m", state=V.UNKNOWN, reason="r")
    with pytest.raises(ValueError):
        V.metric("x", "x", None, "m", state=V.UNAVAILABLE)
    zero = V.metric("x", "x", 0.0, "m")
    assert zero["state"] == "available" and zero["value"] == 0.0


def test_other_users_cannot_see_project(client, auth):
    pid = make_project(client, auth)
    other = login(client, "other@example.com")
    assert client.get(f"/api/v1/projects/{pid}", headers=other).status_code == 404
    assert client.get("/api/v1/projects", headers=other).json() == []
    assert client.get(f"/api/v1/projects/{pid}").status_code == 401


def test_viewer_cannot_edit(client, auth):
    pid = make_project(client, auth)
    viewer = login(client, "viewer@example.com")
    client.post(f"/api/v1/projects/{pid}/members", headers=auth, json={"email": "viewer@example.com", "role": "viewer"})
    assert client.get(f"/api/v1/projects/{pid}", headers=viewer).status_code == 200
    assert client.patch(f"/api/v1/projects/{pid}", headers=viewer, json={"name": "x"}).status_code == 403


def test_solar_position_reference_values():
    from urbanova.analysis.solar import solar_position

    el, az = solar_position(30.0, date(2026, 6, 21), 12)
    assert el == pytest.approx(83.4, abs=0.3) and az == pytest.approx(180, abs=1)
    el, _ = solar_position(30.0, date(2026, 12, 21), 12)
    assert el == pytest.approx(36.6, abs=0.3)
    el, az = solar_position(30.0, date(2026, 3, 20), 6.0)
    assert el == pytest.approx(0, abs=1.0) and az == pytest.approx(90, abs=2)


def test_prism_shadow_length():
    from shapely.geometry import box

    from urbanova.analysis.solar import prism_shadow

    sh = prism_shadow(box(0, 0, 10, 10), 10, 45, 180)  # sun due south at 45° → 10 m shadow to the north
    assert sh.bounds == pytest.approx((0, 0, 10, 20), abs=1e-6)
    assert sh.area == pytest.approx(200, rel=1e-6)
