"""Stage S6 of the smart upgrade (PHASE2_SPEC rev. 2 §8): report around the six questions (one section per hotspot,
executive summary with source ids, synthetic label on every page), export of hotspots, site dashboard, apply-recommendation
as a draft, map editing of geometry, Leaflet-Geoman pinned with SRI."""

import io
import json
import re
import zipfile

import pytest

from conftest import ROOT, login, make_project, run, upload
from test_acceptance import baseline_with_plan
from test_stage_s2 import hot_day


def _fake_climate(monkeypatch):
    from urbanova.analysis.common import COMPLETED, result
    from urbanova.engine import core

    def fake(ctx):
        ctx.shared["hot_day"] = hot_day()
        ctx.shared["hottest_months"] = [6, 7, 8]
        return result(COMPLETED)

    monkeypatch.setitem(core.MODULES, "climate_baseline", fake)


@pytest.fixture()
def analysed(client, auth, monkeypatch):
    """A project with TEST rates and a baseline analysed with hotspots and measured options."""
    _fake_climate(monkeypatch)
    pid = make_project(client, auth, objectives=["main_concerns", "shade"])
    assert upload(client, auth, pid, "sample_rates_TEST_ONLY.csv", "rate_table").status_code in (200, 201)
    sid = baseline_with_plan(client, auth, pid)["id"]
    b = run(client, auth, pid, sid, {"modules": ["interventions"], "solar_date": "2025-07-21"})
    return pid, sid, b


def _report(client, auth, rid, fmt="html"):
    rep = client.post(f"/api/v1/analysis-runs/{rid}/reports", headers=auth, json={})
    assert rep.status_code == 201, rep.text
    return rep.json()["id"], client.get(f"/api/v1/reports/{rep.json()['id']}/download?format={fmt}", headers=auth)


# ------------------------------------------------------------------ report (§8.1 acceptance)
def test_every_hotspot_section_has_the_six_parts_in_priority_order(client, auth, analysed):
    from urbanova import report_front

    pid, sid, b = analysed
    _, r = _report(client, auth, b["run"]["id"])
    html = r.text
    assert "<h2>1. Executive summary</h2>" in html and "<h2>2. Design hotspots</h2>" in html and "<h2>Appendix A. Project and site</h2>" in html
    body = html.split("<h2>2. Design hotspots</h2>", 1)[1].split("<h2>3. Site context", 1)[0]
    sections = re.split(r"<h3>Hotspot (\d+) — ", body)[1:]
    numbers = [int(n) for n in sections[0::2]]
    assert numbers == sorted(numbers) and numbers == [h["number"] for h in b["hotspots"]]
    for text in sections[1::2]:
        positions = [text.find(f"<h4>{p}</h4>") for p in report_front.PARTS]
        assert all(p >= 0 for p in positions) and positions == sorted(positions)


def test_executive_summary_cites_sources_and_matches_the_sections(client, auth, analysed):
    pid, sid, b = analysed
    _, r = _report(client, auth, b["run"]["id"])
    ex = r.text.split("<h2>1. Executive summary</h2>", 1)[1].split("<h2>2. Design hotspots</h2>", 1)[0]
    items = re.findall(r"<li>(.*?)</li>", ex, re.S)
    assert items and all(re.search(r"\[[^\]]+\]$", i.strip()) for i in items), [i for i in items if not re.search(r"\[[^\]]+\]$", i.strip())]
    h1 = b["hotspots"][0]
    assert f"hotspot {h1['number']}" in ex and f"{h1['area_m2']:,.0f} m²" in ex and f"severity {h1['severity']}" in ex
    sec = r.text.split("<h2>2. Design hotspots</h2>", 1)[1]
    assert f"{h1['area_m2']:,.0f} m²" in sec


def test_synthetic_label_is_on_every_pdf_page_and_in_the_html(client, auth, analysed):
    from pypdf import PdfReader

    from urbanova import report_front

    pid, sid, b = analysed
    _, pdf = _report(client, auth, b["run"]["id"], "pdf")
    pages = PdfReader(io.BytesIO(pdf.content)).pages
    assert len(pages) > 2
    for page in pages:
        assert "synthetic or test data" in " ".join((page.extract_text() or "").split())
    _, html = _report(client, auth, b["run"]["id"])
    assert report_front.SYNTHETIC_LABEL.split(" (")[0] in html.text


def test_no_synthetic_label_without_test_data_unless_flagged(client, auth, monkeypatch):
    _fake_climate(monkeypatch)
    pid = make_project(client, auth)
    sid = baseline_with_plan(client, auth, pid)["id"]
    b = run(client, auth, pid, sid, {"modules": ["urban_form"]})
    _, html = _report(client, auth, b["run"]["id"])
    assert 'class="banner synthetic"' not in html.text
    assert client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"synthetic_data": True}).json()["project"]["synthetic_data"] is True
    _, html = _report(client, auth, b["run"]["id"])
    assert 'class="banner synthetic"' in html.text


def test_export_contains_hotspots_geojson_and_csv(client, auth, analysed):
    pid, sid, b = analysed
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v1/analysis-runs/{b['run']['id']}/export", headers=auth).content))
    fc = json.loads(z.read("hotspots.geojson"))
    assert len(fc["features"]) == len(b["hotspots"]) and fc["features"][0]["properties"]["number"] == 1
    rows = z.read("hotspots.csv").decode("utf-8").splitlines()
    assert rows[0].startswith("hotspot_id,number") and len(rows) == len(b["hotspots"]) + 1


# ------------------------------------------------------------------ apply recommendation (§8.2)
def test_apply_an_option_as_a_draft_then_approve_and_analyse(client, auth, analysed):
    from shapely.geometry import shape

    from urbanova import db, geo
    from urbanova import interventions as I

    pid, sid, b = analysed
    rid = b["run"]["id"]
    opt = next(o for r in b["recommendations"] for o in r.get("options") or [] if o["intervention_id"] == "I1")
    viewer = login(client, "viewer@example.com")
    assert client.post(f"/api/v1/analysis-runs/{rid}/options/{opt['id']}/draft", headers=viewer).status_code in (403, 404)
    r = client.post(f"/api/v1/analysis-runs/{rid}/options/{opt['id']}/draft", headers=auth)
    assert r.status_code == 201, r.text
    draft = db.get("scenarios", r.json()["id"])
    assert draft["status"] == "draft" and draft["modifications"][0]["option_id"] == opt["id"]
    # The draft equals the recipe replayed on the baseline design.
    base = db.get("scenarios", sid)
    project = db.get("projects", pid)
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    hs = next(h for h in db.select("hotspots", "run_id = ?", [rid]) if h["number"] == opt["recipe"]["hotspot_number"])
    d = I.Design(base["features"], local)
    I.apply_recipe(d, opt["recipe"], geo.to_local(shape(hs["geometry"]), local), hs, geo.to_local(shape(project["boundary"]), local))
    strip = lambda fc: sorted((f["properties"].get("feature_type"), round(shape(f["geometry"]).centroid.x, 7), round(shape(f["geometry"]).centroid.y, 7)) for f in fc["features"])
    assert strip(draft["features"]) == strip(d.fc)
    assert client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": draft["id"], "config": {}}).status_code >= 400
    assert client.post(f"/api/v1/scenarios/{draft['id']}/approve", headers=auth).status_code == 200
    assert client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": draft["id"], "config": {"modules": ["urban_form"]}}).status_code == 202
    assert client.post(f"/api/v1/analysis-runs/{rid}/options/opt_doesnotexist/draft", headers=auth).status_code == 422


# ------------------------------------------------------------------ map editing (§8.2)
def test_geometry_edits_are_validated_and_logged(client, auth):
    pid = make_project(client, auth)
    base = baseline_with_plan(client, auth, pid)
    alt = client.post(f"/api/v1/projects/{pid}/scenarios", headers=auth, json={"kind": "alternative", "name": "Edited"}).json()
    full = client.get(f"/api/v1/scenarios/{alt['id']}", headers=auth).json()
    f = next(x for x in full["features"]["features"] if x["properties"]["feature_type"] == "building")
    moved = {"type": "Polygon", "coordinates": [[[x + 0.0001, y] for x, y in f["geometry"]["coordinates"][0]]]}
    r = client.patch(f"/api/v1/scenarios/{alt['id']}", headers=auth, json={"changes": [{"op": "update", "feature_id": f["id"], "geometry": moved}]})
    assert r.status_code == 200, r.text
    after = next(x for x in r.json()["features"]["features"] if x["id"] == f["id"])
    assert after["geometry"]["coordinates"][0][0][0] == pytest.approx(moved["coordinates"][0][0][0])
    assert r.json()["modifications"][-1]["changes"][0]["op"] == "update_geometry"
    far = {"type": "Polygon", "coordinates": [[[32.5, 30.5], [32.501, 30.5], [32.501, 30.501], [32.5, 30.5]]]}
    assert client.patch(f"/api/v1/scenarios/{alt['id']}", headers=auth, json={"changes": [{"op": "update", "feature_id": f["id"], "geometry": far}]}).status_code == 422
    bow = {"type": "Polygon", "coordinates": [[[31.85, 30.12], [31.851, 30.121], [31.851, 30.12], [31.85, 30.121], [31.85, 30.12]]]}
    assert client.patch(f"/api/v1/scenarios/{alt['id']}", headers=auth, json={"changes": [{"op": "update", "feature_id": f["id"], "geometry": bow}]}).status_code == 422
    assert client.patch(f"/api/v1/scenarios/{base['id']}", headers=auth, json={"changes": [{"op": "update", "feature_id": f["id"], "geometry": moved}]}).status_code == 422


# ------------------------------------------------------------------ dashboard (§8.2)
def test_dashboard_summarises_the_latest_baseline_run(client, auth, analysed):
    pid, sid, b = analysed
    d = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()
    assert d["state"] == "ready" and d["run_id"] == b["run"]["id"]
    assert d["condition"]["design"]["hotspots"] == len(b["hotspots"])
    assert [h["number"] for h in d["top_hotspots"]] == [h["number"] for h in b["hotspots"][:3]]
    assert any(h["best_option"] for h in d["top_hotspots"])
    assert {q["objective"] for q in d["questions"]} == {"main_concerns", "shade"} and all(q["answer"] for q in d["questions"])
    assert d["completeness"]["modules"]["hotspots"]["status"] in ("completed", "partial")


def test_dashboard_empty_state_names_the_next_step(client, auth):
    pid = make_project(client, auth)
    d = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()
    assert d["state"] == "empty" and "baseline" in d["next_step"].lower()


# ------------------------------------------------------------------ assets
def test_geoman_is_pinned_with_subresource_integrity():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for asset in ("leaflet-geoman.css", "leaflet-geoman.js"):
        tag = next(t for t in re.findall(r"<(?:script|link)[^>]+>", html) if asset in t)
        assert "@geoman-io/leaflet-geoman-free@2.20.3/" in tag and 'integrity="sha384-' in tag and "crossorigin" in tag


def test_dashboard_says_not_assessed_when_hotspots_did_not_run(client, auth, monkeypatch):
    """Found in the S7 walkthrough: a run without the hotspot module showed '0 design hotspots'."""
    _fake_climate(monkeypatch)
    pid = make_project(client, auth)
    sid = baseline_with_plan(client, auth, pid)["id"]
    run(client, auth, pid, sid, {"modules": ["urban_form"]})
    c = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()["condition"]["design"]
    assert c["assessed"] is False and c["not_assessed_reason"]
