"""Regression tests for the Phase A/B remediation (classification, areas, rule extraction,
DXF units, attribute units, re-upload, approval wording, provisional severity, PDF report,
small comparisons, regional climate findings)."""

from pathlib import Path

import pytest
from shapely.geometry import box, mapping

from conftest import SAMPLES, make_project, run, upload

LAT, LON = 30.1200, 31.8500


def _local_box_wgs(x0, y0, x1, y1):
    """A box given in metres relative to the sample point, returned as WGS 84 GeoJSON."""
    from urbanova import geo

    local = geo.utm_crs_for(LAT, LON)
    from shapely.geometry import Point
    c = geo.to_local(Point(LON, LAT), local)
    g = box(c.x + x0, c.y + y0, c.x + x1, c.y + y1)
    return mapping(geo.reproject(g, local, geo.WGS84))


# ------------------------------------------------------------------ A1 classification
@pytest.mark.parametrize("text, expected", [
    ("Surface car park", "parking"), ("Parking lot", "parking"), ("PARKING", "parking"), ("Car parking", "parking"),
    ("car-park", "parking"), ("Carpark", "parking"),
    ("Pocket park", "green_space"), ("Park", "green_space"), ("Courtyard garden", "green_space"),
    ("Shade canopy structure", "shade_structure"), ("Plaza shade canopy", "shade_structure"), ("PERGOLA", "shade_structure"),
    ("Existing tree", "tree"), ("Tree canopy", "tree"),
])
def test_keyword_classification(text, expected):
    from urbanova import plans

    assert plans.classify({"name": text})[0] == expected
    assert plans.classify({}, text)[0] == expected


def _write_dxf(path: Path, insunits: int | None, *, with_insert=False, with_hatch=False):
    import ezdxf

    from urbanova import geo
    from shapely.geometry import Point

    local = "EPSG:32636"
    c = geo.reproject(Point(LON, LAT), geo.WGS84, local)
    x, y = c.x, c.y
    doc = ezdxf.new("R2018")
    if insunits is not None:
        doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    sq = lambda x0, y0, s: [(x + x0, y + y0), (x + x0 + s, y + y0), (x + x0 + s, y + y0 + s), (x + x0, y + y0 + s)]
    msp.add_lwpolyline(sq(0, 0, 20), close=True, dxfattribs={"layer": "BUILDINGS"})
    msp.add_lwpolyline(sq(30, 0, 20), close=True, dxfattribs={"layer": "CAR_PARK"})
    msp.add_lwpolyline(sq(60, 0, 20), close=True, dxfattribs={"layer": "LANDSCAPE_PARK"})
    msp.add_lwpolyline(sq(90, 0, 10), close=True, dxfattribs={"layer": "SHADE_CANOPY"})
    if with_insert:
        blk = doc.blocks.new("KIOSK")
        blk.add_lwpolyline([(0, 0), (4, 0), (4, 4), (0, 4)], close=True, dxfattribs={"layer": "BUILDINGS"})
        msp.add_blockref("KIOSK", (x + 0, y + 40), dxfattribs={"layer": "BUILDINGS"})
    if with_hatch:
        h = msp.add_hatch(dxfattribs={"layer": "GREEN"})
        h.paths.add_polyline_path(sq(0, 60, 15), is_closed=True)
    doc.saveas(path)
    return path


def test_dxf_classifies_from_layer_names_only(client, auth, tmp_path):
    pid = make_project(client, auth)
    dxf = _write_dxf(tmp_path / "plan.dxf", 6)
    r = upload(client, auth, pid, "plan.dxf", "urban_plan", content=dxf.read_bytes(), epsg="32636")
    assert r.status_code == 201, r.text
    feats = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["features"]
    types = sorted(f["feature_type"] for f in feats)
    assert types == ["building", "green_space", "parking", "shade_structure"]
    assert all("DXF layer" in f["classification_source"] for f in feats)


# ------------------------------------------------------------------ A2 open-space union
def test_open_space_overlap_counted_once(env):
    from urbanova.analysis.common import Context
    from urbanova.analysis import urban_form

    half = _local_box_wgs(0, 0, 50, 100)
    project = {"id": "p", "latitude": LAT, "longitude": LON, "boundary": _local_box_wgs(0, 0, 100, 100)}
    scen = {"id": "s", "name": "s", "features": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": half, "properties": {"feature_type": "open_space"}},
        {"type": "Feature", "geometry": half, "properties": {"feature_type": "green_space"}},
    ]}}
    ms = {m["metric_name"]: m for m in urban_form.compute(Context(project, scen, {}, "r", Path(".")))["metrics"]}
    assert ms["open_space_pct"]["value"] == pytest.approx(50.0, abs=0.1)
    assert ms["green_space_pct"]["value"] == pytest.approx(50.0, abs=0.1)


# ------------------------------------------------------------------ A3 rule extraction
@pytest.mark.parametrize("text, rule_type, value", [
    ("Maximum building coverage of the plot: 40,5%.", "max_building_coverage_pct", 40.5),
    ("Open space 25,5 %.", "min_open_space_pct", 25.5),
    ("Building setback 3 5 m.", "min_setback_m", 5.0),
    ("Setback of 1 500 m from the road.", "min_setback_m", 1500.0),
    ("Maximum 2 levels of basement parking.", "max_floors", 2.0),
])
def test_risky_numbers_are_low_confidence_with_a_note(text, rule_type, value):
    from urbanova import documents

    [c] = [c for c in documents.find_candidates([text]) if c["rule_type"] == rule_type]
    assert c["value"] == pytest.approx(value)
    assert c["confidence"] == "low" and c["notes"], c


@pytest.mark.parametrize("text, rule_type, value", [
    ("Maximum building coverage of the plot 40.5%.", "max_building_coverage_pct", 40.5),
    ("Total area 1,250 m2.", "plot_area_m2", 1250.0),
    ("Maximum 4 floors.", "max_floors", 4.0),
    ("Maximum height 18 m.", "max_height_m", 18.0),
])
def test_plain_numbers_keep_medium_confidence(text, rule_type, value):
    from urbanova import documents

    [c] = [c for c in documents.find_candidates([text]) if c["rule_type"] == rule_type]
    assert c["value"] == pytest.approx(value) and c["confidence"] == "medium"


def test_verification_applies_manual_entry_range_checks(client, auth):
    pid = make_project(client, auth)
    add = lambda v: client.post(f"/api/v1/projects/{pid}/rules", headers=auth, json={"rule_type": "min_green_space_pct", "value": v, "source": "Test clause 1"})
    rid = add("20").json()["id"]
    for bad in ("150", "-5", "0"):
        r = client.post(f"/api/v1/rules/{rid}/decision", headers=auth, json={"decision": "verified", "value": bad})
        assert r.status_code == 422, (bad, r.text)
    assert client.post(f"/api/v1/rules/{rid}/decision", headers=auth, json={"decision": "verified", "value": "25"}).status_code == 200
    assert add("150").status_code == 422


# ------------------------------------------------------------------ A4 DXF units and entities
def test_unitless_dxf_blocks_areas_until_units_confirmed(client, auth, tmp_path):
    pid = make_project(client, auth)
    dxf = _write_dxf(tmp_path / "nounits.dxf", 0)  # 0 = unitless, as exported by many CAD tools
    r = upload(client, auth, pid, "nounits.dxf", "urban_plan", content=dxf.read_bytes(), epsg="32636")
    assert r.status_code == 201, r.text
    assert r.json()["outcome"]["units_unconfirmed"] is True
    fid = r.json()["file"]["id"]
    q = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["quality"]
    assert len(q["units_unconfirmed"]) == 4 and len(q["needs_review"]) == 4
    # No baseline (and so no area-based urban-form ratios) while the scale is unresolved.
    r = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"})
    assert r.status_code == 422 and "units" in r.json()["detail"]
    ready = client.post(f"/api/v1/projects/{pid}/readiness-check", headers=auth, json={}).json()
    assert next(a for a in ready["analyses"] if a["analysis"] == "urban_form")["status"] == "blocked"
    assert client.post(f"/api/v1/files/{fid}/confirm-units", headers=auth).json() == {"confirmed": 4}
    feats = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["features"]
    assert all(f["correction_history"][-1]["event"] == "units_confirmed_metres" for f in feats)
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"})
    assert base.status_code == 201, base.text
    out = run(client, auth, pid, base.json()["id"], {"modules": ["urban_form"]})
    assert out["run"]["module_status"]["urban_form"]["status"] in ("completed", "partial")


def test_urban_form_refuses_a_scenario_with_unconfirmed_units(env):
    from urbanova.analysis.common import Context
    from urbanova.analysis import urban_form

    project = {"id": "p", "latitude": LAT, "longitude": LON, "boundary": _local_box_wgs(0, 0, 100, 100)}
    scen = {"id": "s", "name": "s", "features": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _local_box_wgs(0, 0, 10, 10), "properties": {"feature_type": "building", "units_unconfirmed": True}}]}}
    assert urban_form.run(Context(project, scen, {}, "r", Path(".")))["status"] == "blocked"


def test_dxf_insert_and_hatch_are_read_and_skips_are_counted(client, auth, tmp_path):
    import ezdxf

    pid = make_project(client, auth)
    dxf = _write_dxf(tmp_path / "blocks.dxf", 6, with_insert=True, with_hatch=True)
    doc = ezdxf.readfile(dxf)
    doc.modelspace().add_text("Block A", dxfattribs={"layer": "ANNOTATION"})
    doc.modelspace().add_text("Block B", dxfattribs={"layer": "ANNOTATION"})
    doc.saveas(dxf)
    r = upload(client, auth, pid, "blocks.dxf", "urban_plan", content=dxf.read_bytes(), epsg="32636")
    assert r.status_code == 201, r.text
    out = r.json()["outcome"]
    assert out["features_extracted"] == 6  # 4 polylines + 1 exploded block + 1 hatch
    assert out["by_type"]["building"] == 2 and out["by_type"]["green_space"] == 2
    assert out["skipped_entities"] == {"TEXT": 2}
    assert any("2 TEXT" in n for n in out["notes"])
    hatch = next(f for f in client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["features"] if f["original_reference"].startswith("HATCH"))
    from shapely.geometry import shape
    from urbanova import geo
    assert geo.to_local(shape(hatch["geometry"]), geo.utm_crs_for(LAT, LON)).area == pytest.approx(225, rel=0.02)


# ------------------------------------------------------------------ A5 attribute units
@pytest.mark.parametrize("raw, expected", [(12, 12.0), ("12", 12.0), ("12 m", 12.0), ("12.5m", 12.5), ("12 metres", 12.0),
                                           ("30ft", None), ("1200cm", None), ("12mm", None), ("tall", None)])
def test_heights_must_be_metres(raw, expected):
    from urbanova import plans

    attrs, issues = plans._attributes({"height": raw})
    assert attrs.get("height_m") == expected
    assert bool(issues) == (expected is None)


def test_non_metre_height_marks_feature_for_review(client, auth):
    import json as _json

    pid = make_project(client, auth)
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"feature_type": "building", "height": "40 ft"},
                                                     "geometry": _local_box_wgs(10, 10, 30, 30)}]}
    r = upload(client, auth, pid, "ft.geojson", "urban_plan", content=_json.dumps(fc).encode())
    assert r.status_code == 201, r.text
    [f] = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["features"]
    assert "height_m" not in f["attributes"] and f["review_status"] == "needs_review"
    assert "not a number in metres" in " ".join(f["correction_history"][0]["issues"])


# ------------------------------------------------------------------ A6 re-upload choice
def test_plan_reupload_requires_explicit_replace_or_add(client, auth):
    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    n = len(client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["geojson"]["features"])
    r = upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan")
    assert r.status_code == 422 and "replace" in r.json()["detail"]
    assert len(client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["geojson"]["features"]) == n

    r = upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan", plan_mode="replace")
    assert r.status_code == 201 and r.json()["outcome"]["replaced_features"] == n
    data = client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()
    assert len(data["geojson"]["features"]) == n and len(data["features"]) == 2 * n  # old ones kept, inactive
    old = [f for f in data["features"] if not f["active"]]
    assert all(f["correction_history"][-1]["event"] == "deactivated" for f in old)

    r = upload(client, auth, pid, "sample_alternative_additions.geojson", "urban_plan", plan_mode="add")
    assert r.status_code == 201
    assert len(client.get(f"/api/v1/projects/{pid}/features", headers=auth).json()["geojson"]["features"]) > n


# ------------------------------------------------------------------ B1 approval wording (C1 decision of 2026-11-04)
def test_route_confirmation_and_unverified_commercial_terms_are_labelled(env, monkeypatch):
    from urbanova import orchestrator
    from urbanova.providers.base import availability, registry

    for pid_ in ("pc_landsat_c2_l2", "pc_sentinel2_l2a"):
        p = registry()[pid_]
        assert p["commercial_use"] == "unverified" and p["confirmation"]["on"] == "2026-11-04"
        d = orchestrator._with_approval({"source_id": pid_})
        assert "terms confirmed by Project decision maker (data science lead) on 2026-11-04" in d["approval_label"] and "commercial-use terms unverified" in d["approval_label"]
        assert d["terms_confirmed"] is True
        monkeypatch.setattr(env, "usage_context", "commercial")
        ok, kind, reason = availability(pid_)
        assert not ok and kind == "license_restricted" and "not been reviewed" in reason
        monkeypatch.setattr(env, "usage_context", "non_commercial_evaluation")
        assert availability(pid_)[0] is True
    # Without a recorded confirmation the label says it is pending.
    monkeypatch.delitem(registry()["pc_landsat_c2_l2"], "confirmation")
    info = orchestrator.approval_info("pc_landsat_c2_l2")
    assert "confirmation by the decision maker pending" in info["approval_label"] and info["terms_confirmed"] is False


# ------------------------------------------------------------------ B2 / B5 findings
def _metric(name, module, value, unit="", **kw):
    return {"id": f"met_{name}", "metric_name": name, "module": module, "state": "available", "value": value, "unit": unit,
            "label": name, "method": "test", "output_kind": "calculated", "quality": {}, "source_dataset_ids": [], **kw}


def _generate(metrics, project):
    from urbanova import findings

    mods = {m["module"]: {"status": "completed", "details": {}} for m in metrics}
    return findings.generate({"id": "run_x"}, project, mods, metrics, {})


def test_provisional_threshold_never_produces_high_severity(env):
    from urbanova import findings

    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    fs, recs = _generate([_metric("lst_site_hottest_decile_share_pct", "surface_heat", 60.0, "%")], project)
    [f] = fs
    assert f["threshold"]["status"] == "provisional_not_approved"
    assert f["severity"] == "moderate" and f["threshold"]["band_level"] == "high" and f["threshold"]["severity_capped"] is True
    assert "PROVISIONAL" in f["summary"] and "Capped at moderate" in f["severity_rule"]
    # Ordering: a provisional moderate finding ranks below a verified-rule finding of the same severity.
    verified = {"severity": "moderate", "threshold": {"limit": 1}}
    assert sorted([f, verified], key=findings.sort_key)[0] is verified


def test_regional_climate_finding_is_not_placed_on_the_site(env):
    project = {"id": "p", "boundary": _local_box_wgs(0, 0, 100, 100)}
    fs, recs = _generate([_metric("climate_mean_annual_TX35_days", "climate_baseline", 200.0, "days/yr")], project)
    [f] = fs
    assert f["geometry"] is None and f["affected_area"].startswith("Regional (~25 km")
    assert any("regional climate statistic" in x for x in f["limitations"])
    assert all(r["target"]["description"].startswith("Regional") and r["target"]["geometry"] is None for r in recs)
    # A site-scale indicator keeps the boundary.
    fs2, _ = _generate([_metric("ndvi_site_vegetated_share_pct", "vegetation", 0.0, "%")], project)
    assert fs2 and fs2[0]["geometry"] == project["boundary"] and fs2[0]["affected_area"] == "Site boundary"


def test_finding_affected_area_column_is_migrated_on_existing_databases(env):
    import sqlite3

    from urbanova import db

    db.reset_connection()
    path = env.db_path
    env.ensure_dirs()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, category TEXT NOT NULL, classification TEXT NOT NULL, "
              "title TEXT NOT NULL, summary TEXT, geometry TEXT, metric_ids TEXT, severity TEXT NOT NULL, severity_rule TEXT, rule_id TEXT, "
              "threshold TEXT, evidence TEXT, sources TEXT, confidence TEXT, limitations TEXT, created_at TEXT NOT NULL)")
    c.commit()
    c.close()
    db._initialised_for.discard(str(path))
    cols = {r["name"] for r in db.conn().execute("PRAGMA table_info(findings)")}
    assert "affected_area" in cols


# ------------------------------------------------------------------ B3 PDF report sections
def test_pdf_report_contains_every_section_heading(client, auth):
    import io

    from pypdf import PdfReader

    from urbanova import report

    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    b = run(client, auth, pid, base["id"])
    rep = client.post(f"/api/v1/analysis-runs/{b['run']['id']}/reports", headers=auth, json={}).json()
    pdf = client.get(f"/api/v1/reports/{rep['id']}/download?format=pdf", headers=auth).content
    text = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    flat = " ".join(text.split())
    html = client.get(f"/api/v1/reports/{rep['id']}/download?format=html", headers=auth).text
    # Five main sections around the supervisors' questions, then the 14 technical parts as appendices (S6).
    assert len(report.SECTIONS) == 19 and len(report.APPX) == 14
    for title in report.SECTIONS:
        assert title in flat, title
        assert f"<h2>{title}</h2>" in html, title
    # Methodology and assumptions carry content, not only a heading.
    assert "Analysis methods version" in flat and "never as zero" in flat


# ------------------------------------------------------------------ B4 two-scenario scoring
def test_two_scenario_scoring_shows_raw_values_and_warns(client, auth):
    pid = make_project(client, auth)
    assert upload(client, auth, pid, "sample_plan_baseline.geojson", "urban_plan").status_code == 201
    base = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "baseline"}).json()
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "A"}).json()
    assert client.post(f"/api/v1/scenarios/{alt['id']}/import", headers=auth,
                       files={"file": ("a.geojson", (SAMPLES / "sample_alternative_additions.geojson").read_bytes())}).status_code in (200, 201)
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form"]}})
    c = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth,
                    json={"baseline_id": base["id"], "alternative_ids": [alt["id"]], "criteria": [{"metric": "green_space_pct", "weight": 1}]}).json()
    rk = c["result"]["ranking"]
    assert rk["scenario_count"] == 2 and "however small" in rk["small_set_warning"]
    [cr] = rk["criteria"]
    assert set(cr["values"]) == {base["id"], alt["id"]} and cr["unit"] == "%"
    assert cr["difference_vs_baseline"][base["id"]] == 0
    assert cr["difference_vs_baseline"][alt["id"]] == pytest.approx(cr["values"][alt["id"]] - cr["values"][base["id"]], abs=1e-3)
