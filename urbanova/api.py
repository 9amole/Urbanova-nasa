"""REST API (B.2) and static web UI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from starlette.concurrency import run_in_threadpool

from .ai import insights as ai_insights
from .ai import llm as ai_llm
from .ai import suggest as ai_suggest
from . import __version__, comparison, cost, db, documents, explain, files, geo, orchestrator, plans, projects, readiness, report, scenarios, security, supplementary
from .catalog import public_dataset
from .providers import geocode
from .providers.base import ProviderError, registry
from .settings import ROOT, settings

log = logging.getLogger("urbanova.api")
app = FastAPI(title="URBANOVA Phase 1 API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
API = "/api/v1"

DOMAIN_ERRORS = (projects.ProjectError, geo.GeometryError, plans.PlanError, documents.DocumentError, scenarios.ScenarioError,
                 orchestrator.RunError, comparison.ComparisonError, cost.CostError, supplementary.SupplementaryError, geocode.GeocodeError,
                 ai_suggest.SuggestError)


class NotFound(Exception):
    pass


@app.exception_handler(security.AuthError)
async def _auth_err(_: Request, exc: security.AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(files.UploadError)
async def _upload_err(_: Request, exc: files.UploadError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(NotFound)
async def _nf(_: Request, exc: NotFound):
    return JSONResponse({"detail": str(exc) or "Not found."}, status_code=404)


for _cls in DOMAIN_ERRORS:
    app.add_exception_handler(_cls, lambda _r, exc: JSONResponse({"detail": str(exc)}, status_code=422))


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Send only the origin (never the path) to other sites. OSM's tile servers reject requests
    # without a Referer under their tile usage policy, so "no-referrer" broke the basemap.
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp


def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
    return security.user_from_token(token)


def _project(pid: str, user: dict, minimum: str = "viewer") -> tuple[dict, str]:
    role = security.require_role(pid, user, minimum)
    return db.get("projects", pid), role


def _owned(table: str, id_: str, user: dict, minimum: str = "viewer") -> tuple[dict, dict]:
    rec = db.get(table, id_)
    if not rec:
        raise NotFound(f"{table[:-1].capitalize()} not found.")
    project, _ = _project(rec["project_id"], user, minimum)
    return rec, project


def _run(run_id: str, user: dict, minimum: str = "viewer") -> tuple[dict, dict]:
    return _owned("runs", run_id, user, minimum)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise projects.ProjectError("The request body must be valid JSON.")
    if not isinstance(body, dict):
        raise projects.ProjectError("The request body must be a JSON object.")
    return body


# ---------------------------------------------------------------- auth
@app.post(f"{API}/auth/register", status_code=201)
async def register(request: Request):
    b = await _json(request)
    return security.register(b.get("email"), b.get("name"), b.get("password"))


@app.post(f"{API}/auth/login")
async def login(request: Request):
    b = await _json(request)
    token, user = security.login(b.get("email"), b.get("password"))
    return {"token": token, "user": user}


@app.post(f"{API}/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        security.logout(authorization.split(" ", 1)[1])
    return {"ok": True}


@app.get(f"{API}/auth/me")
def me(user: dict = Depends(current_user)):
    return user


# ---------------------------------------------------------------- meta
@app.get(f"{API}/meta")
def meta():
    return {
        "version": __version__, "offline": settings.offline, "project_types": projects.PROJECT_TYPES, "objectives": projects.OBJECTIVES,
        "feature_types": plans.FEATURE_TYPES, "rule_types": documents.RULE_TYPES, "quantity_types": cost.QUANTITY_TYPES,
        "upload_categories": {k: {**v, "capabilities": {t: files.CAPABILITIES[t] for t in v["types"]}} for k, v in files.ALLOWLIST.items()},
        "max_upload_mb": settings.max_upload_mb, "analyses": readiness.ANALYSES, "runnable_modules": readiness.RUNNABLE,
        "default_config": orchestrator.DEFAULT_CONFIG, "module_condition": readiness.CONDITION, "ai": ai_llm.status(),
        "basemap": {"url": settings.tile_url, "attribution": settings.tile_attribution, "max_zoom": settings.tile_max_zoom,
                    "is_osm_volunteer_service": "tile.openstreetmap.org" in settings.tile_url},
    }


@app.get(f"{API}/providers")
def providers(user: dict = Depends(current_user)):
    return list(registry().values())


@app.get(f"{API}/geocode")
def geocode_search(q: str = "", user: dict = Depends(current_user)):
    """Address search for creating a project (Nominatim). Runs only when the user presses Search."""
    try:
        return geocode.search(q)
    except ProviderError as exc:
        return JSONResponse({"detail": f"Address search is unavailable: {exc.as_dict()['message']} Click the map or enter coordinates instead."}, status_code=503)


# Provider logs (FR-41) are kept in the `provider_logs` table for operators. They are not exposed
# through the API because request URLs contain other projects' coordinates; an admin view belongs
# to Module 14, which is not part of this build.


# ---------------------------------------------------------------- projects
@app.post(f"{API}/projects", status_code=201)
async def create_project(request: Request, user: dict = Depends(current_user)):
    p, warnings = projects.create_project(user, await _json(request))
    return {"project": projects.public_project(p, "owner"), "warnings": warnings}


@app.get(f"{API}/projects")
def list_projects(user: dict = Depends(current_user)):
    rows = db.select("projects", "id IN (SELECT project_id FROM project_members WHERE user_id = ?)", [user["id"]], order="updated_at DESC")
    return [projects.public_project(p, security.project_role(p["id"], user["id"])) for p in rows]


@app.get(f"{API}/projects/{{pid}}")
def get_project(pid: str, user: dict = Depends(current_user)):
    p, role = _project(pid, user)
    return {"project": projects.public_project(p, role), "missing_information": projects.missing_information(p), "readiness": readiness.assess(p),
            "history": p.get("history") or []}


@app.patch(f"{API}/projects/{{pid}}")
async def patch_project(pid: str, request: Request, user: dict = Depends(current_user)):
    p, role = _project(pid, user, "editor")
    p, warnings = projects.update_project(p, user, await _json(request))
    return {"project": projects.public_project(p, role), "warnings": warnings}


@app.post(f"{API}/projects/{{pid}}/confirm-location")
def confirm_location(pid: str, user: dict = Depends(current_user)):
    p, role = _project(pid, user, "editor")
    return projects.public_project(projects.confirm_location(p, user), role)


@app.post(f"{API}/projects/{{pid}}/site-area-basis")
async def site_area_basis(pid: str, request: Request, user: dict = Depends(current_user)):
    p, role = _project(pid, user, "editor")
    b = await _json(request)
    basis = b.get("basis")
    if basis == "boundary":
        val = None
    elif basis == "document":
        rule = documents.verified_rules(pid).get("plot_area_m2")
        if not rule:
            raise projects.ProjectError("Verify a plot-area rule from the planning document first.")
        val = rule["value"]
    else:
        raise projects.ProjectError("basis must be 'boundary' or 'document'.")
    hist = list(p.get("history") or []) + [{"at": db.now(), "by": user["id"], "event": "site_area_basis", "basis": basis, "value": val}]
    db.update("projects", "id", pid, {"verified_site_area_m2": val, "history": hist, "updated_at": db.now()})
    return projects.public_project(db.get("projects", pid), role)


@app.post(f"{API}/projects/{{pid}}/members")
async def add_member(pid: str, request: Request, user: dict = Depends(current_user)):
    _project(pid, user, "owner")
    b = await _json(request)
    if b.get("role") not in ("viewer", "editor"):
        raise projects.ProjectError("role must be 'viewer' or 'editor'.")
    other = db.get("users", (b.get("email") or "").strip().lower(), key="email")
    if not other:
        raise projects.ProjectError("No account exists for that email. Ask the person to register first.")
    if security.project_role(pid, other["id"]) == "owner":
        raise projects.ProjectError("The project owner's role cannot be changed.")
    db.execute("INSERT OR REPLACE INTO project_members (project_id, user_id, role, added_at) VALUES (?, ?, ?, ?)", [pid, other["id"], b["role"], db.now()])
    return {"ok": True}


@app.post(f"{API}/projects/{{pid}}/readiness-check")
async def readiness_check(pid: str, request: Request, user: dict = Depends(current_user)):
    p, _ = _project(pid, user)
    b = await _json(request) if (await request.body()) else {}
    scen = db.get("scenarios", b["scenario_id"]) if b.get("scenario_id") else None
    if scen and scen["project_id"] != pid:
        raise NotFound("Scenario not found.")
    return readiness.assess(p, scen)


# ---------------------------------------------------------------- uploads & features
@app.post(f"{API}/projects/{{pid}}/uploads", status_code=201)
async def upload(pid: str, file: UploadFile = File(...), category: str = Form(...), epsg: str | None = Form(None),
                 source: str | None = Form(None), variable: str | None = Form(None), unit: str | None = Form(None),
                 license: str | None = Form(None), observation_date: str | None = Form(None), plan_mode: str | None = Form(None),
                 user: dict = Depends(current_user)):
    p, _ = _project(pid, user, "editor")
    previous = plans.active_features(pid) if category == "urban_plan" else []
    if previous and plan_mode not in ("replace", "add"):
        # Never duplicate silently: a second plan needs an explicit choice.
        raise plans.PlanError(f"This project already has {len(previous)} active plan feature(s). Choose 'replace' (the previous "
                                 "features are deactivated and kept in history) or 'add' (the new features are added alongside them).")
    content = await file.read(int(settings.max_upload_mb * 1024 * 1024) + 1)
    detected, warnings = files.validate_upload(file.filename or "upload", content, category)
    rec = files.register_file(pid, user["id"], file.filename or "upload", file.content_type, content, category, detected, warnings)
    outcome: dict[str, Any]
    try:
        if category == "urban_plan":
            outcome = plans.import_plan(p, rec, content, epsg)
            if previous:
                outcome["plan_mode"] = plan_mode
                # A reference-only drawing (PDF/image) adds no features, so it never replaces any.
                if plan_mode == "replace" and outcome.get("features_extracted"):
                    outcome["replaced_features"] = plans.deactivate_features(previous, user, f"replaced by plan file {rec['original_filename']}")
        elif category == "planning_document":
            outcome = documents.ingest_document(p, rec)
        elif category == "rate_table":
            items = cost.import_csv(pid, content, rec["id"])
            outcome = {"status": "imported", "rates": len(items)}
        else:
            outcome = supplementary.register_raster(p, rec, {"source": source, "variable": variable, "unit": unit, "license": license, "observation_date": observation_date})
        status = outcome.get("status", "processed")
        db.update("files", "id", rec["id"], {"status": status, "validation": {"warnings": warnings, "errors": [], "outcome": outcome}})
    except DOMAIN_ERRORS as exc:
        # The original stays preserved; the file is marked failed with the actionable reason.
        db.update("files", "id", rec["id"], {"status": "failed", "validation": {"warnings": warnings, "errors": [str(exc)]}})
        return JSONResponse({"detail": str(exc), "file": files.public_file(db.get("files", rec["id"]))}, status_code=422)
    return {"file": files.public_file(db.get("files", rec["id"])), "outcome": outcome, "warnings": warnings}


@app.get(f"{API}/projects/{{pid}}/files")
def list_files(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return [files.public_file(f) for f in db.select("files", "project_id = ?", [pid], order="uploaded_at DESC")]


@app.get(f"{API}/files/{{fid}}/original")
def file_original(fid: str, user: dict = Depends(current_user)):
    f, _ = _owned("files", fid, user)
    return FileResponse(f["storage_path"], filename=f["original_filename"], media_type="application/octet-stream")


@app.post(f"{API}/files/{{fid}}/confirm-units")
def confirm_file_units(fid: str, user: dict = Depends(current_user)):
    f, _ = _owned("files", fid, user, "editor")
    return plans.confirm_units(f, user)


@app.get(f"{API}/projects/{{pid}}/features")
def list_features(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    feats = db.select("features", "project_id = ?", [pid], order="created_at")
    active = [f for f in feats if f["active"]]
    return {"geojson": plans.feature_collection(active), "features": feats, "quality": plans.geometry_quality(pid)}


@app.patch(f"{API}/features/{{fid}}")
async def patch_feature(fid: str, request: Request, user: dict = Depends(current_user)):
    f, _ = _owned("features", fid, user, "editor")
    return plans.correct_feature(f, user, await _json(request))


# ---------------------------------------------------------------- rules
@app.get(f"{API}/projects/{{pid}}/rules")
def list_rules(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return {"rules": db.select("rules", "project_id = ?", [pid], order="rule_type, page"), "ambiguities": documents.unresolved_ambiguities(pid),
            "rule_types": documents.RULE_TYPES}


@app.post(f"{API}/projects/{{pid}}/rules", status_code=201)
async def add_rule(pid: str, request: Request, user: dict = Depends(current_user)):
    p, _ = _project(pid, user, "editor")
    return documents.add_manual_rule(p, user, await _json(request))


@app.post(f"{API}/rules/{{rid}}/decision")
async def rule_decision(rid: str, request: Request, user: dict = Depends(current_user)):
    r, _ = _owned("rules", rid, user, "editor")
    b = await _json(request)
    return documents.decide_rule(r, user, b.get("decision"), b.get("value"), b.get("note"))


# ---------------------------------------------------------------- rates & datasets
@app.get(f"{API}/projects/{{pid}}/rates")
def list_rates(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return db.select("rate_items", "project_id = ?", [pid], order="quantity_type")


@app.post(f"{API}/projects/{{pid}}/rates", status_code=201)
async def add_rate(pid: str, request: Request, user: dict = Depends(current_user)):
    _project(pid, user, "editor")
    return cost.add_rate(pid, await _json(request))


@app.get(f"{API}/projects/{{pid}}/datasets")
def list_datasets(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return [public_dataset(d) for d in db.select("datasets", "project_id = ?", [pid], order="created_at DESC")]


# ---------------------------------------------------------------- scenarios
@app.get(f"{API}/projects/{{pid}}/scenarios")
def list_scenarios(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    out = []
    for s in db.select("scenarios", "project_id = ?", [pid], order="kind DESC, created_at"):
        r = comparison.latest_run(s["id"])
        out.append({**scenarios.public(s, include_features=False), "latest_run": {"id": r["id"], "status": r["status"], "completed_at": r["completed_at"], "stale": orchestrator.stale_modules(r)} if r else None})
    return out


@app.post(f"{API}/projects/{{pid}}/scenarios", status_code=201)
async def create_scenario(pid: str, request: Request, user: dict = Depends(current_user)):
    p, _ = _project(pid, user, "editor")
    b = await _json(request)
    if b.get("kind") == "baseline":
        return scenarios.create_baseline(p, user, b.get("name"))
    return scenarios.create_alternative(p, user, b.get("name"), b.get("description"), b.get("from_scenario_id"))


@app.get(f"{API}/scenarios/{{sid}}")
def get_scenario(sid: str, user: dict = Depends(current_user)):
    s, _ = _owned("scenarios", sid, user)
    return s


@app.patch(f"{API}/scenarios/{{sid}}")
async def patch_scenario(sid: str, request: Request, user: dict = Depends(current_user)):
    s, _ = _owned("scenarios", sid, user, "editor")
    return scenarios.modify(s, user, (await _json(request)).get("changes") or [])


@app.post(f"{API}/scenarios/{{sid}}/import")
async def import_scenario(sid: str, file: UploadFile = File(...), user: dict = Depends(current_user)):
    s, _ = _owned("scenarios", sid, user, "editor")
    content = await file.read(int(settings.max_upload_mb * 1024 * 1024) + 1)
    detected, _ = files.validate_upload(file.filename or "x.geojson", content, "urban_plan")
    if detected != "geojson":
        raise scenarios.ScenarioError("Only GeoJSON FeatureCollections can be imported into a scenario.")
    try:
        fc = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise scenarios.ScenarioError(f"The GeoJSON file could not be read: {exc}.")
    if fc.get("type") != "FeatureCollection":
        raise scenarios.ScenarioError("Import a GeoJSON FeatureCollection.")
    return scenarios.import_features_into(s, user, fc)


@app.post(f"{API}/scenarios/{{sid}}/approve")
def approve_scenario(sid: str, user: dict = Depends(current_user)):
    s, _ = _owned("scenarios", sid, user, "editor")
    return scenarios.approve_draft(s, user)


@app.post(f"{API}/analysis-runs/{{rid}}/package/draft", status_code=201)
def package_draft(rid: str, user: dict = Depends(current_user)):
    run, p = _owned("runs", rid, user, "editor")
    return scenarios.public(scenarios.create_draft_from_package(p, user, run), include_features=False)


@app.post(f"{API}/scenarios/{{sid}}/archive")
def archive_scenario(sid: str, user: dict = Depends(current_user)):
    s, _ = _owned("scenarios", sid, user, "editor")
    return scenarios.archive(s, user)


# ---------------------------------------------------------------- analysis runs
@app.post(f"{API}/projects/{{pid}}/analysis-runs", status_code=202)
async def start_run(pid: str, request: Request, user: dict = Depends(current_user)):
    p, _ = _project(pid, user, "editor")
    b = await _json(request)
    scen = db.get("scenarios", b["scenario_id"]) if b.get("scenario_id") else None
    if b.get("scenario_id") and not scen:
        raise NotFound("Scenario not found.")
    return orchestrator.start_run(p, scen, user, b.get("config") or {})


@app.post(f"{API}/projects/{{pid}}/analysis-runs/all-scenarios", status_code=202)
async def start_all(pid: str, request: Request, user: dict = Depends(current_user)):
    """Run the baseline and every active alternative with identical settings so they are comparable."""
    p, _ = _project(pid, user, "editor")
    cfg = (await _json(request)).get("config") or {}
    scens = db.select("scenarios", "project_id = ? AND status = 'active'", [pid], order="kind DESC, created_at")
    if not scens:
        raise scenarios.ScenarioError("Create the baseline scenario first.")
    return orchestrator.start_runs(p, scens, user, cfg)


@app.get(f"{API}/projects/{{pid}}/analysis-runs")
def list_runs(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return [{k: r[k] for k in ("id", "scenario_id", "status", "started_at", "completed_at", "job_id", "analysis_version")} | {"stale_modules": orchestrator.stale_modules(r) if r["status"] not in ("queued", "running") else {}}
            for r in db.select("runs", "project_id = ?", [pid], order="rowid DESC")]


@app.get(f"{API}/analysis-runs/{{rid}}")
def get_run(rid: str, user: dict = Depends(current_user)):
    r, _ = _run(rid, user)
    return orchestrator.run_bundle(r)


@app.get(f"{API}/analysis-runs/{{rid}}/metrics")
def run_metrics(rid: str, user: dict = Depends(current_user)):
    _run(rid, user)
    return db.select("metrics", "run_id = ?", [rid])


@app.get(f"{API}/analysis-runs/{{rid}}/findings")
def run_findings(rid: str, user: dict = Depends(current_user)):
    _run(rid, user)
    return db.select("findings", "run_id = ?", [rid])


@app.get(f"{API}/analysis-runs/{{rid}}/recommendations")
def run_recs(rid: str, user: dict = Depends(current_user)):
    _run(rid, user)
    return db.select("recommendations", "run_id = ?", [rid])


@app.get(f"{API}/jobs/{{jid}}")
def get_job(jid: str, user: dict = Depends(current_user)):
    j, _ = _owned("jobs", jid, user)
    return j


@app.get(f"{API}/layers/{{lid}}/data")
def layer_data(lid: str, user: dict = Depends(current_user)):
    lay = db.get("layers", lid)
    if not lay:
        raise NotFound("Layer not found.")
    _run(lay["run_id"], user)
    path = Path(lay["path"])
    media = "image/png" if path.suffix == ".png" else "application/geo+json"
    return FileResponse(path, media_type=media)


@app.get(f"{API}/analysis-runs/{{rid}}/export")
def export(rid: str, user: dict = Depends(current_user)):
    r, p = _run(rid, user)
    return Response(report.export_zip(p, r), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="urbanova_{rid}.zip"'})


# ---------------------------------------------------------------- comparisons & reports
@app.post(f"{API}/projects/{{pid}}/scenario-comparisons", status_code=201)
async def compare(pid: str, request: Request, user: dict = Depends(current_user)):
    p, _ = _project(pid, user, "editor")
    b = await _json(request)
    return comparison.compare(p, user, b.get("baseline_id"), b.get("alternative_ids") or [], b.get("criteria"))


@app.get(f"{API}/projects/{{pid}}/scenario-comparisons")
def list_comparisons(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    names = {s["id"]: s["name"] for s in db.select("scenarios", "project_id = ?", [pid])}
    return [{"id": c["id"], "created_at": c["created_at"], "comparable": (c["result"] or {}).get("comparable"), "run_ids": c["run_ids"],
             "label": f"{names.get(c['baseline_scenario_id'], '?')} vs " + ", ".join(names.get(a, "?") for a in c["alternative_scenario_ids"])}
            for c in db.select("comparisons", "project_id = ?", [pid], order="created_at DESC")]


@app.get(f"{API}/scenario-comparisons/{{cid}}")
def get_comparison(cid: str, user: dict = Depends(current_user)):
    c, _ = _owned("comparisons", cid, user)
    names = {s["id"]: s["name"] for s in db.select("scenarios", "project_id = ?", [c["project_id"]])}
    return {**c, "scenario_names": names}


@app.post(f"{API}/analysis-runs/{{rid}}/reports", status_code=201)
async def make_report(rid: str, request: Request, user: dict = Depends(current_user)):
    r, p = _run(rid, user, "editor")
    if r["status"] in ("queued", "running"):
        raise orchestrator.RunError("The run is still in progress.")
    b = await _json(request) if (await request.body()) else {}
    comp = None
    if b.get("comparison_id"):
        comp, _ = _owned("comparisons", b["comparison_id"], user)
        if comp["project_id"] != p["id"] or r["id"] not in (comp.get("run_ids") or {}).values():
            raise orchestrator.RunError("That comparison does not include this analysis run. Choose a comparison made from this run.")
        comp["scenario_names"] = {s["id"]: s["name"] for s in db.select("scenarios", "project_id = ?", [p["id"]])}
    rep = report.build(p, r, user, comp)
    return {k: v for k, v in rep.items() if not k.endswith("_path")}


@app.get(f"{API}/projects/{{pid}}/reports")
def list_reports(pid: str, user: dict = Depends(current_user)):
    _project(pid, user)
    return [{k: v for k, v in r.items() if not k.endswith("_path")} for r in db.select("reports", "project_id = ?", [pid], order="created_at DESC")]


@app.get(f"{API}/reports/{{rpid}}/download")
def download_report(rpid: str, format: str = "pdf", user: dict = Depends(current_user)):
    rep, p = _owned("reports", rpid, user)
    if format == "html":
        return FileResponse(rep["html_path"], media_type="text/html", filename=f"urbanova_report_{rpid}.html")
    return FileResponse(rep["pdf_path"], media_type="application/pdf", filename=f"urbanova_report_{rpid}.pdf")


# ---------------------------------------------------------------- explanations & feedback
@app.get(f"{API}/findings/{{fid}}/explanation")
def explain_finding(fid: str, user: dict = Depends(current_user)):
    f = db.get("findings", fid)
    if not f:
        raise NotFound("Finding not found.")
    _run(f["run_id"], user)
    return explain.explain_finding(f, db.select("recommendations", "finding_id = ?", [fid]))


def _ai_allowed(project: dict, role: str) -> bool:
    """The single AI policy: the project switched AI text on, and the user may edit (model calls spend the shared budget)."""
    return bool(project.get("ai_enabled")) and role != "viewer"


def _suggest_target(fid: str, user: dict, category: str) -> tuple[dict, dict]:
    f, p = _owned("files", fid, user, "editor")
    if f["category"] != category:
        raise ai_suggest.SuggestError(f"This needs a file of category '{category}'.")
    if not p.get("ai_enabled"):
        raise ai_suggest.SuggestError("Switch AI text on for this project first (Project settings).")
    return f, p


@app.post(f"{API}/analysis-runs/{{rid}}/insights", status_code=201)
def make_insights(rid: str, user: dict = Depends(current_user)):
    """Generate the run's insights (AI when the project switched it on and a provider is available, else the automatic summary)."""
    r, _ = _run(rid, user, "editor")
    p, role = _project(r["project_id"], user, "editor")
    out = ai_insights.generate(orchestrator.run_bundle(r), _ai_allowed(p, role))
    rec = {"id": db.new_id("ins"), "run_id": rid, "pack_hash": out["pack_hash"], "blocks": out["blocks"], "generated_by": out["generated_by"],
           "provider": out["provider"], "model": out["model"], "prompt_version": out["prompt_version"], "label": out["label"],
           "reason": out.get("reason"), "attempts": out["attempts"], "created_by": user["id"], "created_at": db.now()}
    db.insert("insights", rec)
    return rec


@app.get(f"{API}/analysis-runs/{{rid}}/insights")
def get_insights(rid: str, user: dict = Depends(current_user)):
    _run(rid, user)
    rows = db.select("insights", "run_id = ?", [rid], order="created_at DESC")
    return rows[0] if rows else None


@app.post(f"{API}/files/{{fid}}/suggest-rules")
def suggest_rules(fid: str, user: dict = Depends(current_user)):
    f, p = _suggest_target(fid, user, "planning_document")
    return ai_suggest.suggest_rules(p, f)


@app.post(f"{API}/files/{{fid}}/suggest-layers")
def suggest_layers(fid: str, user: dict = Depends(current_user)):
    f, p = _suggest_target(fid, user, "urban_plan")
    return ai_suggest.suggest_layers(p, f)


@app.post(f"{API}/analysis-runs/{{rid}}/ask")
async def ask(rid: str, request: Request, user: dict = Depends(current_user)):
    r, _ = _run(rid, user)
    p, role = _project(r["project_id"], user)
    question = (await _json(request)).get("question", "")
    if explain.PREDICTION.search(question or ""):
        return explain.answer(question, {})  # prediction questions get the fixed refusal, never a model
    bundle = await run_in_threadpool(orchestrator.run_bundle, r)
    # The model call is blocking network I/O: keep it off the event loop.
    ai = await run_in_threadpool(ai_insights.answer, bundle, question, _ai_allowed(p, role))
    if ai and ai.get("answer"):
        return ai
    out = explain.answer(question, bundle)
    return {**out, "generated_by": "fallback"} if ai is not None else out


@app.post(f"{API}/recommendations/{{rec_id}}/review")
async def review_recommendation(rec_id: str, request: Request, user: dict = Depends(current_user)):
    rec = db.get("recommendations", rec_id)
    if not rec:
        raise NotFound("Recommendation not found.")
    run, p = _run(rec["run_id"], user, "editor")
    b = await _json(request)
    status = b.get("status")
    if status not in ("accepted", "rejected", "flagged_incorrect", "proposed"):
        raise projects.ProjectError("status must be accepted, rejected, flagged_incorrect or proposed.")
    db.update("recommendations", "id", rec_id, {"review_status": status})
    db.insert("feedback", {"id": db.new_id("fbk"), "project_id": p["id"], "target_type": "recommendation", "target_id": rec_id, "user_id": user["id"],
                           "kind": status, "comment": (b.get("comment") or "")[:2000] or None, "created_at": db.now()})
    return db.get("recommendations", rec_id)


@app.post(f"{API}/projects/{{pid}}/feedback", status_code=201)
async def feedback(pid: str, request: Request, user: dict = Depends(current_user)):
    _project(pid, user)
    b = await _json(request)
    if b.get("target_type") not in ("finding", "recommendation", "metric", "report", "explanation", "comparison"):
        raise projects.ProjectError("Unknown feedback target.")
    if b.get("kind") not in ("useful", "not_useful", "incorrect", "unclear"):
        raise projects.ProjectError("kind must be useful, not_useful, incorrect or unclear.")
    rec = {"id": db.new_id("fbk"), "project_id": pid, "target_type": b["target_type"], "target_id": str(b.get("target_id"))[:64], "user_id": user["id"],
           "kind": b["kind"], "comment": (b.get("comment") or "")[:2000] or None, "created_at": db.now()}
    db.insert("feedback", rec)
    # Feedback is recorded for review only; it never alters results or trains a model (FR-37).
    return rec


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__, "offline": settings.offline}


def _index_html() -> str:
    """index.html with each asset link keyed by a hash of the file, so browsers fetch new code after any change."""
    import hashlib
    import re

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        digest = hashlib.sha256((ROOT / "web" / name).read_bytes()).hexdigest()[:12]
        html = re.sub(rf'{re.escape(name)}\?v=[^"]*', f"{name}?v={digest}", html)
    return html


INDEX_HTML = _index_html()


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
