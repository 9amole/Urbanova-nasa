"""Tests added in the pre-commit review: source approval, licence context, provisional thresholds, robustness."""

import json
from datetime import date

import numpy as np
import pytest

from conftest import SAMPLES, login, make_project, run, upload
from test_acceptance import _fake_items, _fake_series, approve, baseline_with_plan, metric


# ---------------------------------------------------------------- data-source approval
def unapprove(monkeypatch, *ids):
    """Simulate a provider whose access route has not been approved."""
    from urbanova.providers.base import registry

    for i in ids:
        monkeypatch.setitem(registry()[i], "approval_status", "pending_supervisor_approval")


def test_unapproved_access_route_is_never_called(client, auth, monkeypatch):
    from urbanova.analysis import surface_heat, vegetation

    unapprove(monkeypatch, "pc_landsat_c2_l2", "pc_sentinel2_l2a")
    def boom(*a, **k):
        raise AssertionError("an unapproved provider must not be called")

    monkeypatch.setattr(surface_heat, "search", boom)
    monkeypatch.setattr(vegetation, "search", boom)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat", "vegetation"]})
    for mod in ("surface_heat", "vegetation"):
        st = b["run"]["module_status"][mod]
        assert st["status"] == "blocked" and "awaiting supervisor approval" in st["reason"]
        assert st["provider_failures"][0]["kind"] == "not_approved"
    assert not b["metrics"]


def test_readiness_reports_source_awaiting_approval(client, auth, env, monkeypatch):
    monkeypatch.setattr(env, "offline", False)
    unapprove(monkeypatch, "pc_landsat_c2_l2")
    pid = make_project(client, auth)
    rd = {a["analysis"]: a for a in client.post(f"/api/v1/projects/{pid}/readiness-check", headers=auth, json={}).json()["analyses"]}
    assert rd["surface_heat"]["status"] == "blocked" and "supervisor approval" in rd["surface_heat"]["missing_inputs"][0]
    assert rd["climate_baseline"]["status"] == "ready"


def test_provenance_shows_source_status(client, auth, monkeypatch):
    from urbanova.providers import weather

    monkeypatch.setattr(weather, "fetch_open_meteo", lambda *a, **k: _fake_series("open_meteo_archive"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    ds = b["datasets"][0]
    assert ds["approval_status"] == "fr_recommended" and "terms confirmed by Project decision maker" in ds["approval_label"]
    assert ds["terms_confirmed"] is True and ds["usage_context"] == "non_commercial_evaluation"
    # Without a recorded confirmation the label says the terms are not yet confirmed.
    from urbanova import orchestrator
    from urbanova.providers.base import registry

    monkeypatch.delitem(registry()["open_meteo_archive"], "confirmation")
    info = orchestrator.approval_info("open_meteo_archive")
    assert "not yet confirmed" in info["approval_label"] and info["terms_confirmed"] is False


# ---------------------------------------------------------------- Open-Meteo licence context
def test_commercial_context_blocks_open_meteo_free_tier_and_discloses_fallback(client, auth, env, monkeypatch):
    from urbanova.providers import base, weather

    monkeypatch.setattr(env, "usage_context", "commercial")
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    ok, kind, reason = base.availability("open_meteo_archive")
    assert not ok and kind == "license_restricted" and "commercially" in reason
    monkeypatch.setattr(weather, "fetch_nasa_power", lambda *a, **k: _fake_series("nasa_power_daily"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    st = b["run"]["module_status"]["climate_baseline"]
    assert st["provider_failures"][0]["kind"] == "license_restricted"
    assert b["datasets"][0]["source_id"] == "nasa_power_daily" and any("fallback" in w for w in st["warnings"])


def test_commercial_key_uses_customer_endpoint_and_is_never_stored(env, monkeypatch):
    import httpx

    from urbanova import db
    from urbanova.providers import base, weather

    monkeypatch.setattr(env, "offline", False)
    monkeypatch.setattr(env, "usage_context", "commercial")
    monkeypatch.setenv("OPEN_METEO_API_KEY", "TEST-KEY-123")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"latitude": 30, "longitude": 31.75, "elevation": 100, "timezone": "Africa/Cairo",
                                         "daily_units": {"time": "iso8601", "temperature_2m_max": "°C"},
                                         "daily": {"time": ["2025-07-01", "2025-07-02"], "temperature_2m_max": [36.0, 37.5]}})

    real = httpx.Client
    monkeypatch.setattr(base.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    data = weather.fetch_open_meteo(30.12, 31.85, date(2025, 7, 1), date(2025, 7, 2))
    assert seen["url"].startswith("https://customer-archive-api.open-meteo.com/") and "apikey=TEST-KEY-123" in seen["url"]
    assert data["meta"]["licence_tier"] == "commercial subscription" and data["series"]["tmax_c"] == [36.0, 37.5]
    logs = db.select("provider_logs", "1=1", [])
    assert logs and all("TEST-KEY-123" not in (l["url"] or "") for l in logs)
    cached = list((env.cache_dir / "open_meteo_archive").glob("*.json"))
    assert cached and all("TEST-KEY-123" not in p.read_text(encoding="utf-8") for p in cached)


def test_weather_dates_outside_request_are_rejected(env, monkeypatch):
    import httpx

    from urbanova.providers import base, weather

    monkeypatch.setattr(env, "offline", False)
    body = {"daily_units": {"temperature_2m_max": "°C"}, "daily": {"time": ["2019-01-01"], "temperature_2m_max": [20.0]}}
    real = httpx.Client
    monkeypatch.setattr(base.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), **kw))
    with pytest.raises(base.ProviderError) as e:
        weather.fetch_open_meteo(30.12, 31.85, date(2025, 7, 1), date(2025, 7, 2))
    assert e.value.kind == "invalid_response"


# ---------------------------------------------------------------- heat & vegetation co-location (FR-18 step 7)
def test_lst_vegetation_colocation_is_labelled_not_causal(client, auth, monkeypatch):
    from urbanova.analysis import surface_heat, vegetation

    approve(monkeypatch, "pc_landsat_c2_l2", "pc_sentinel2_l2a")
    s2 = [{"id": "S2_TEST", "properties": {"datetime": "2026-07-01T08:00:00Z", "eo:cloud_cover": 1, "proj:epsg": 32636, "s2:processing_baseline": "05.11"},
           "assets": {"SCL": {"href": "scl"}, "B04": {"href": "red"}, "B08": {"href": "nir"}}}]

    def s2_read(pid, href, epsg, crs, t, shape, **k):
        if href == "scl":
            return np.full(shape, 4.0, dtype="float32")
        arr = np.full(shape, 1000 + (1500 if href == "red" else 4000), dtype="float32")
        r, c = shape
        if href == "nir":
            arr[r // 2 - 10:r // 2 + 10, c // 2 - 10:c // 2 + 10] = 1000 + 1700  # sparse vegetation at the hot centre
        return arr

    def lst_read(pid, href, epsg, crs, t, shape, **k):
        if href == "qa":
            return np.zeros(shape, dtype="float32")
        arr = np.full(shape, (40 + 273.15 - 149.0) / 0.00341802, dtype="float32")
        r, c = shape
        arr[r // 2 - 4:r // 2 + 4, c // 2 - 4:c // 2 + 4] = (46 + 273.15 - 149.0) / 0.00341802
        return arr

    monkeypatch.setattr(vegetation, "search", lambda *a, **k: s2)
    monkeypatch.setattr(vegetation, "read_to_grid", s2_read)
    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: _fake_items(3))
    monkeypatch.setattr(surface_heat, "read_to_grid", lst_read)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["vegetation", "surface_heat"], "hot_months": [7]})

    hot, rest = metric(b, "lst_ndvi_mean_in_hottest_decile"), metric(b, "lst_ndvi_mean_elsewhere")
    assert hot["value"] < rest["value"]
    assert "not show that vegetation causes" in hot["quality"]["note"]


# ---------------------------------------------------------------- robustness / validation
def test_upload_size_limit(client, auth, env, monkeypatch):
    monkeypatch.setattr(env, "max_upload_mb", 0.001)
    pid = make_project(client, auth)
    r = upload(client, auth, pid, "big.geojson", "urban_plan", b"{" + b" " * 5000 + b"}")
    assert r.status_code == 413 and "limit" in r.json()["detail"]


def test_non_utf8_rate_table_gives_clear_error(client, auth):
    pid = make_project(client, auth)
    csv = "quantity_type,unit,rate,currency,price_date,source\ntrees_added_count,each,5,EGP,2026-01-01,Devis \xe9t\xe9\n".encode("latin-1")
    r = upload(client, auth, pid, "rates.csv", "rate_table", csv)
    assert r.status_code == 422 and "UTF-8" in r.json()["detail"]


def test_invalid_run_settings_rejected(client, auth):
    pid = make_project(client, auth)
    for cfg, word in (({"context_buffer_m": "abc"}, "number"), ({"max_scene_cloud_pct": 95}, "between"), ({"solar_date": "21/06/2026"}, "YYYY-MM-DD"), ({"bogus": 1}, "Unknown")):
        r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"config": cfg})
        assert r.status_code == 422 and word in r.json()["detail"], (cfg, r.text)


def test_bad_scenario_import_is_a_clear_error(client, auth):
    pid = make_project(client, auth)
    baseline_with_plan(client, auth, pid)
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "A"}).json()
    r = client.post(f"/api/v1/scenarios/{alt['id']}/import", headers=auth, files={"file": ("x.geojson", b'{"type": "Point", "coordinates": [31.85, 30.12]}')})
    assert r.status_code == 422 and "FeatureCollection" in r.json()["detail"]


def test_report_rejects_comparison_from_other_runs(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "A"}).json()
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form"]}})
    cmp_ = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    later = run(client, auth, pid, base["id"], {"modules": ["urban_form"]})  # a newer run not in the comparison
    r = client.post(f"/api/v1/analysis-runs/{later['run']['id']}/reports", headers=auth, json={"comparison_id": cmp_["id"]})
    assert r.status_code == 422 and "does not include this analysis run" in r.json()["detail"]
    listed = client.get(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth).json()
    assert listed[0]["id"] == cmp_["id"] and "vs" in listed[0]["label"]


def test_comparison_refuses_runs_made_before_boundary_change(client, auth):
    from conftest import boundary

    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "A"}).json()
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form"]}})
    bd = boundary()
    bd["features"][0]["geometry"]["coordinates"][0][0][0] += 0.0001
    bd["features"][0]["geometry"]["coordinates"][0][-1][0] += 0.0001
    client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"boundary": bd})
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    assert not c["result"]["comparable"] and "boundary" in " ".join(c["result"]["issues"])


def test_owner_role_cannot_be_changed(client, auth):
    pid = make_project(client, auth)
    r = client.post(f"/api/v1/projects/{pid}/members", headers=auth, json={"email": "tester@example.com", "role": "viewer"})
    assert r.status_code == 422 and "owner" in r.json()["detail"]


def test_land_use_distribution(client, auth):
    pid = make_project(client, auth)
    fc = json.loads((SAMPLES / "sample_site_boundary.geojson").read_text())
    zone = {"type": "Feature", "geometry": fc["features"][0]["geometry"], "properties": {"feature_type": "land_use_zone", "land_use": "residential"}}
    upload(client, auth, pid, "z.geojson", "urban_plan", json.dumps({"type": "FeatureCollection", "features": [zone]}).encode())
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    b = run(client, auth, pid, base["id"], {"modules": ["urban_form"]})
    m = metric(b, "land_use_zone_count")
    assert m["extra"]["distribution_pct_of_site"]["residential"] == pytest.approx(100, abs=0.1)


def test_report_and_export_label_provisional_and_licence(client, auth, monkeypatch):
    from urbanova.providers import weather

    monkeypatch.setattr(weather, "fetch_open_meteo", lambda *a, **k: _fake_series("open_meteo_archive"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["climate_baseline"]})
    rep = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth, json={}).json()
    html = client.get(f"/api/v1/reports/{rep['id']}/download?format=html", headers=auth).text
    assert "Non-commercial evaluation prototype" in html and "Recommended in the FR" in html
    assert "PROVISIONAL THRESHOLD" in html  # the TX35 finding uses a provisional band


def test_uniform_surface_temperature_is_not_a_hotspot(client, auth, monkeypatch):
    """Regression: with tied values, '≥ P90' used to put every pixel in the hottest decile."""
    from urbanova.analysis import surface_heat

    approve(monkeypatch, "pc_landsat_c2_l2")
    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: _fake_items(3))
    monkeypatch.setattr(surface_heat, "read_to_grid", lambda pid, href, epsg, crs, t, shape, **k:
                        np.zeros(shape, dtype="float32") if href == "qa" else np.full(shape, (40 + 273.15 - 149.0) / 0.00341802, dtype="float32"))
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7]})
    assert metric(b, "lst_site_hottest_decile_share_pct")["value"] == 0.0
    assert metric(b, "lst_site_minus_surroundings_k")["value"] == pytest.approx(0, abs=0.01)
    assert not [f for f in b["findings"] if f["category"] == "heat"]


def test_objective_not_answered_without_its_essential_evidence(client, auth):
    pid = make_project(client, auth, objectives=["surface_heat", "planning_compliance", "data_gaps"])
    base = baseline_with_plan(client, auth, pid)
    b = run(client, auth, pid, base["id"])  # offline + LST source not approved
    ans = {o["objective"]: o for o in b["objectives"]}
    assert ans["surface_heat"]["answerable"] is False and ans["surface_heat"]["not_available"]
    assert ans["planning_compliance"]["answerable"] is False  # no verified rules
    assert ans["data_gaps"]["answerable"] is True


def test_recorded_approval_enables_satellite_routes_and_is_shown(client, auth, env, monkeypatch):
    from urbanova.analysis import surface_heat
    from urbanova.providers.base import availability, registry

    monkeypatch.setattr(env, "offline", False)
    for pid_ in ("pc_landsat_c2_l2", "pc_sentinel2_l2a"):
        p = registry()[pid_]
        assert p["approval_status"] == "approved" and p["approved_by"] and p["approved_on"]
        assert availability(pid_)[0] is True
    pid = make_project(client, auth)
    rd = {a["analysis"]: a for a in client.post(f"/api/v1/projects/{pid}/readiness-check", headers=auth, json={}).json()["analyses"]}
    assert rd["surface_heat"]["status"] == "ready" and rd["vegetation"]["status"] == "ready"
    monkeypatch.setattr(env, "offline", True)
    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: _fake_items(3))
    monkeypatch.setattr(surface_heat, "read_to_grid", lambda pid, href, epsg, crs, t, shape, **k:
                        np.zeros(shape, dtype="float32") if href == "qa" else np.full(shape, 48000.0, dtype="float32"))
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7]})
    ds = b["datasets"][0]
    assert ds["approval_status"] == "approved" and ds["approval_label"].startswith("Approved by Project owner")
    assert "confirmed by Project decision maker" in ds["approval_label"] and "commercial-use terms unverified" in ds["approval_label"]


def test_basemap_requests_send_origin_referrer(client):
    """OSM's tile usage policy rejects tile requests without a Referer; 'no-referrer' broke the basemap."""
    r = client.get("/")
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    bm = client.get("/api/v1/meta").json()["basemap"]
    assert "{z}" in bm["url"] and "OpenStreetMap" in bm["attribution"]


@pytest.mark.parametrize("layer, expected", [
    ("PARKING", "parking"), ("CAR_PARK", "parking"), ("Surface car park", "parking"), ("GARAGE", "parking"),
    ("PARK", "green_space"), ("Pocket park", "green_space"), ("GREEN_LANDSCAPE", "green_space"),
])
def test_parking_layers_are_not_classified_as_green(layer, expected):
    from urbanova import plans
    assert plans.classify({}, layer)[0] == expected
    assert plans.classify({"name": layer})[0] == expected
