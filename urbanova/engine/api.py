"""The engine's public functions. No database, web framework or user accounts are involved:
inputs are plain JSON-compatible dicts and bytes, outputs are dicts that match `contract.py`."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import shapely
from shapely.geometry import shape

from .. import ANALYSIS_VERSION, __version__, comparison, cost, documents, files, findings, geo, plans, recording
from .. import values as V
from ..analysis.common import Context
from ..orchestrator import OBJECTIVE_MODULES, RunError, answer_objectives, approval_info, resolve_config
from ..settings import settings
from .contract import ACCEPTED_REQUEST_VERSIONS, CONTRACT_VERSION, AnalysisRequest
from .core import jsonable, run_design

MAX_FEATURES_PER_DESIGN = 5000
MAX_VERTICES_PER_REQUEST = 500_000
GLOBAL_LIMITATIONS = [
    "Decision support only: results are not statutory approvals, engineering design or professional sign-off.",
    "Severity bands marked provisional_not_approved have not been approved; such findings are capped at 'moderate'.",
    "Climate statistics describe a ~25 km reanalysis grid cell, not conditions within the site.",
    "No validated model predicts how a design changes air temperature or thermal comfort; the engine reports indicators only.",
    "Unknown, unavailable, blocked and not-applicable values are reported as such and never as zero.",
]


class EngineInputError(ValueError):
    """The request is invalid. `errors` lists {"loc": path, "msg": message} entries the caller can show to a user."""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__("; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errors))


def _err(loc: list, msg: str) -> EngineInputError:
    return EngineInputError([{"loc": loc, "msg": msg}])


# ------------------------------------------------------------------ analyse
def analyse(request: dict[str, Any], output_dir: str | os.PathLike) -> dict[str, Any]:
    """Run the analysis described by `request` (see contract.AnalysisRequest) and return an
    AnalysisResult dict. Map overlays and GeoTIFFs are written under `output_dir`; the result refers
    to them by relative path. Raises EngineInputError for invalid input; provider outages never
    raise, they are reported per module."""
    req = _parse(request)
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    project, warnings = _project(req)
    rules = _rules(req)
    rates = _rates(req)
    designs = _designs(req, project)
    cfg = _settings(req, project)
    compare_designs = _check_compare(req, designs) if req.compare else None  # before any analysis runs
    fingerprint = _fingerprint(request)
    site_cache: dict = {}
    design_results, design_raw = [], {}
    with recording.use(recording.MemoryRecorder()) as rec:
        for index, d in enumerate(designs or [None]):
            key = d["id"] if d else "site"
            # The index keeps folders unique even when two ids sanitise to the same name ("a:b", "a_b").
            run_dir = out_root / f"{index:02d}_{_safe(key)}"
            run_dir.mkdir(parents=True, exist_ok=True)
            run_id = recording.new_id("run")
            ctx = Context(project=project, scenario=d, config=cfg, run_id=run_id, out_dir=run_dir, rules=rules, site_cache=site_cache, rates=rates)
            out = run_design(ctx, cfg["modules"])
            design_raw[key] = out
            available = {m["metric_name"] for m in out["metrics"] if m["state"] == "available"}
            design_results.append({
                "design_id": d["id"] if d else None, "design_name": d["name"] if d else None, "kind": d["kind"] if d else None,
                "status": out["status"], "modules": out["module_status"],
                "metrics": [_metric(m) for m in out["metrics"]],
                "findings": [_finding(f) for f in sorted(out["findings"], key=findings.sort_key)],
                "recommendations": [_rec(r) for r in out["recommendations"]],
                "layers": [_layer(lay, out_root) for lay in out["layers"]],
                "objectives": answer_objectives(req.objectives, out["module_status"], available),
                "warnings": out["warnings"],
                "hotspots": [{k: v for k, v in h.items() if k != "run_id"} for h in out["hotspots"]],
                "site_context": out["site_context"],
                "package": ((out["module_status"].get("interventions") or {}).get("details") or {}).get("package"),
            })
        datasets = [_dataset(ds, out_root) for ds in rec.datasets.values()]
        requests = [dict(r) for r in rec.requests]
    comp = _compare(req, project, compare_designs, design_raw, rates) if compare_designs else None
    statuses = [d["status"] for d in design_results]
    status = "succeeded" if all(s == "succeeded" for s in statuses) else "failed" if all(s == "failed" for s in statuses) else "partially_succeeded"
    return jsonable({
        "contract_version": CONTRACT_VERSION, "engine_version": __version__, "analysis_version": ANALYSIS_VERSION,
        "request_id": req.request_id, "input_fingerprint": fingerprint, "generated_at": recording.now(),
        "usage_context": settings.usage_context, "status": status, "designs": design_results, "datasets": datasets,
        "provider_requests": requests, "comparison": comp, "warnings": warnings, "limitations": GLOBAL_LIMITATIONS,
    })


def _parse(request: dict[str, Any]) -> AnalysisRequest:
    if not isinstance(request, dict):
        raise _err([], "The request must be a JSON object.")
    if request.get("contract_version") not in (None, *ACCEPTED_REQUEST_VERSIONS):
        raise _err(["contract_version"], f"This engine implements contract {CONTRACT_VERSION} (accepts requests {', '.join(ACCEPTED_REQUEST_VERSIONS)}); the request uses {request.get('contract_version')}.")
    try:
        return AnalysisRequest.model_validate(request)
    except ValidationError as exc:
        raise EngineInputError([{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()])


def _project(req: AnalysisRequest) -> tuple[dict, list[str]]:
    s = req.site
    try:
        lat, lon = geo.validate_coordinates(s.latitude, s.longitude)
    except geo.GeometryError as exc:
        raise _err(["site"], str(exc))
    warnings: list[str] = []
    boundary = None
    if s.boundary is not None:
        try:
            boundary, warnings = geo.validate_boundary(s.boundary, lat, lon)
        except geo.GeometryError as exc:
            raise _err(["site", "boundary"], str(exc))
    period = None
    if s.climate_period:
        from ..projects import ProjectError, _clean_period

        try:
            period = _clean_period(s.climate_period.model_dump())
        except ProjectError as exc:
            raise _err(["site", "climate_period"], str(exc))
    project = {"id": req.request_id or "engine_request", "latitude": lat, "longitude": lon, "boundary": boundary,
               "verified_site_area_m2": s.verified_site_area_m2, "analysis_period": period, "objectives": list(req.objectives)}
    return project, warnings


def _rules(req: AnalysisRequest) -> dict:
    verified: dict[str, dict] = {}
    for i, r in enumerate(req.rules.verified):
        loc = ["rules", "verified", i]
        if r.rule_type in verified:
            raise _err(loc, f"More than one verified rule of type {r.rule_type}; verify exactly one.")
        spec = documents.RULE_TYPES[r.rule_type]
        if spec["operator"] != "text":
            if r.value is None:
                raise _err(loc + ["value"], "A numeric value is required for this rule type.")
            try:
                documents.check_value(r.rule_type, r.value)
            except documents.DocumentError as exc:
                raise _err(loc + ["value"], str(exc))
        verified[r.rule_type] = {"id": r.id or recording.new_id("rul"), "rule_type": r.rule_type, "operator": spec["operator"], "value": r.value,
                                 "unit": spec["unit"], "value_text": r.value_text, "document_name": r.source.document,
                                 "page": r.source.page, "exact_text": r.source.text}
    conflicts = sorted({round(v, 2) for v in req.rules.conflicting_plot_areas_m2})
    ambiguities = ([{"rule_type": "plot_area_m2", "values": conflicts, "message": "Conflicting values for stated plot / site area are unresolved."}]
                   if len(conflicts) > 1 and "plot_area_m2" not in verified else [])
    return {"verified": verified, "unverified_count": req.rules.unverified_count, "ambiguities": ambiguities}


def _rates(req: AnalysisRequest) -> list[dict]:
    out = []
    for i, r in enumerate(req.rates):
        try:
            v = cost.validate_rate(r.model_dump(exclude={"id"}))
        except cost.CostError as exc:
            raise _err(["rates", i], str(exc))
        out.append({**v, "id": r.id or recording.new_id("rat")})
    return out


def _designs(req: AnalysisRequest, project: dict) -> list[dict]:
    kinds = [d.kind for d in req.designs]
    if kinds.count("baseline") > 1:
        raise _err(["designs"], "At most one design may be the baseline.")
    if len({d.id for d in req.designs}) != len(req.designs):
        raise _err(["designs"], "Design ids must be unique.")
    out, vertices = [], 0
    for i, d in enumerate(req.designs):
        fc = d.features
        if fc.get("type") != "FeatureCollection" or not isinstance(fc.get("features"), list):
            raise _err(["designs", i, "features"], "features must be a GeoJSON FeatureCollection.")
        if len(fc["features"]) > MAX_FEATURES_PER_DESIGN:
            raise _err(["designs", i, "features"], f"At most {MAX_FEATURES_PER_DESIGN} features per design.")
        clean = []
        for j, f in enumerate(fc["features"]):
            loc = ["designs", i, "features", "features", j]
            if not isinstance(f, dict):
                raise _err(loc, "Each feature must be a GeoJSON Feature object.")
            props = f.get("properties") or {}
            ftype = props.get("feature_type")
            if ftype not in plans.FEATURE_TYPES:
                raise _err(loc + ["properties", "feature_type"], f"Unknown or missing feature_type {ftype!r}. Use one of: {', '.join(plans.FEATURE_TYPES)}.")
            try:
                g = shape(f["geometry"])
            except Exception:
                raise _err(loc + ["geometry"], "Missing or unreadable GeoJSON geometry.")
            try:
                geo.check_wgs84_bounds(g)
            except geo.GeometryError as exc:
                raise _err(loc + ["geometry"], str(exc))
            if g.is_empty or not g.is_valid:
                raise _err(loc + ["geometry"], "The geometry is empty or invalid (for example self-intersecting). Run extract_plan(), which repairs and flags such shapes.")
            if plans.has_unconfirmed_units([props]):
                raise _err(loc + ["properties", "units_unconfirmed"], "The feature comes from a drawing with unconfirmed units; confirm the units are metres first.")
            vertices += shapely.get_num_coordinates(g)  # works for every geometry type, incl. GeometryCollection
            attrs, issues = plans._attributes(props)
            if issues:
                raise _err(loc + ["properties"], " ".join(issues))
            keep = {k: props[k] for k in ("name", "land_use", "species", "surface_material", "albedo") if props.get(k) not in (None, "")}
            clean.append({"type": "Feature", "id": f.get("id") or f"{d.id}:{j}", "geometry": f["geometry"],
                          "properties": {"feature_type": ftype, **keep, **{k: v for k, v in attrs.items() if k in ("height_m", "floors", "canopy_radius_m")}}})
        out.append({"id": d.id, "name": d.name, "kind": d.kind, "features": {"type": "FeatureCollection", "features": clean}})
    if vertices > MAX_VERTICES_PER_REQUEST:
        raise _err(["designs"], "The designs are too large for one request; simplify the geometry or split the request.")
    return out


def _settings(req: AnalysisRequest, project: dict) -> dict:
    payload = {k: v for k, v in req.settings.model_dump().items() if v is not None}
    try:
        return resolve_config(project, payload)
    except RunError as exc:
        raise _err(["settings"], str(exc))


def _fingerprint(request: dict) -> str:
    norm = {k: v for k, v in request.items() if k != "request_id"}
    return hashlib.sha256(json.dumps(norm, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:64] or "design"


def _rel(path: str | None, root: Path) -> str | None:
    if not path:
        return None
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).name


def _metric(m: dict) -> dict:
    keys = ("id", "metric_name", "label", "module", "state", "value", "unit", "reason", "output_kind", "method", "design_dependent",
            "source_dataset_ids", "quality", "uncertainty", "extra")
    return {k: m.get(k) for k in keys} | {"source_dataset_ids": m.get("source_dataset_ids") or [], "condition": V.condition_of(m)}


def _finding(f: dict) -> dict:
    keys = ("id", "category", "classification", "title", "summary", "severity", "severity_rule", "affected_area", "geometry", "rule_id",
            "threshold", "evidence", "sources", "confidence", "limitations")
    return {k: f.get(k) for k in keys} | {"hotspot_ids": f.get("hotspot_ids") or [], "checks": f.get("checks") or []}


def _rec(r: dict) -> dict:
    keys = ("id", "finding_id", "title", "problem", "intervention", "expected_direction", "required_inputs", "tradeoffs", "evaluation_method",
            "target", "review_status", "intervention_id", "hotspot_id", "confidence", "cost")
    return {k: r.get(k) for k in keys} | {"options": r.get("options") or [], "literature": r.get("literature") or []}


def _layer(lay: dict, root: Path) -> dict:
    meta = dict(lay.get("meta") or {})
    if meta.get("geotiff"):
        meta["geotiff"] = _rel(meta["geotiff"], root)
    return {"id": lay["id"], "module": lay["module"], "name": lay["name"], "layer_kind": lay["layer_kind"], "output_kind": lay["output_kind"],
            "file": _rel(lay["path"], root), "bounds": lay.get("bounds"), "legend": lay.get("legend"), "meta": meta}


def _dataset(ds: dict, root: Path) -> dict:
    out = {k: v for k, v in ds.items() if k not in ("raw_path", "processed_path", "cache_key", "project_id")}
    out["processed_file"] = _rel(ds.get("processed_path"), root)
    return out | approval_info(ds["source_id"])


def _budget(amount, currency) -> dict | None:
    if amount is None and not currency:
        return None
    if amount is None or not currency or not str(currency).isalpha():
        raise _err(["compare", "budget_amount"], "Give both budget_amount and a 3-letter budget_currency, or neither.")
    return {"amount": float(amount), "currency": str(currency).upper()}


def _check_compare(req: AnalysisRequest, designs: list[dict]) -> tuple[dict, list[dict]]:
    """Validate the comparison block up front, so a bad request fails before any analysis runs."""
    c = req.compare
    by_id = {d["id"]: d for d in designs}
    base = by_id.get(c.baseline_id)
    if not base or base["kind"] != "baseline":
        raise _err(["compare", "baseline_id"], "baseline_id must name the design whose kind is 'baseline'.")
    alts = []
    for i, aid in enumerate(c.alternative_ids):
        a = by_id.get(aid)
        if not a or a["kind"] != "alternative":
            raise _err(["compare", "alternative_ids", i], f"{aid} is not an alternative design of this request.")
        alts.append(a)
    for i, cr in enumerate(c.criteria or []):
        if cr.metric not in comparison.DIRECTIONS:
            raise _err(["compare", "criteria", i, "metric"], f"'{cr.metric}' has no agreed preferred direction and cannot be used as a ranking criterion.")
    return base, alts


def _compare(req: AnalysisRequest, project: dict, compare_designs: tuple[dict, list[dict]], raw: dict, rates: list[dict]) -> dict:
    c = req.compare
    base, alts = compare_designs
    ids = [base["id"]] + [a["id"] for a in alts]
    metrics = {i: {m["metric_name"]: m for m in raw[i]["metrics"]} for i in ids}
    checks = {i: ((raw[i]["module_status"].get("planning_compliance") or {}).get("details") or {}).get("checks", []) for i in ids}
    findings_by = {i: raw[i]["findings"] for i in ids}
    costs = {a["id"]: cost.estimate(project, base, a, rates=rates) for a in alts}
    hotspots = {i: raw[i].get("hotspots") or [] for i in ids}
    criteria = [x.model_dump() for x in c.criteria] if c.criteria else None
    try:
        out = comparison.compare_core(base, alts, metrics, checks, findings_by, costs, criteria, hotspots=hotspots, budget=_budget(c.budget_amount, c.budget_currency))
    except comparison.ComparisonError as exc:
        raise _err(["compare", "criteria"], str(exc))
    return {"baseline_id": base["id"], "alternative_ids": [a["id"] for a in alts], **out,
            "note": "Every design in this request used the same site data and settings, so the comparison is like-for-like."}


# ------------------------------------------------------------------ file extraction
def extract_plan(filename: str, content: bytes, site: dict[str, Any], epsg: str | None = None) -> dict[str, Any]:
    """Turn an uploaded plan (GeoJSON, or DXF with its EPSG code) into reviewable features.

    `site` needs latitude and longitude (and optionally boundary for quality checks). Returns
    {"detected_type", "warnings", "features" (GeoJSON FeatureCollection with feature_type, classification
    source/confidence and review_status per feature), "summary"}. PDF/PNG/JPEG plans are accepted as
    reference-only. Features with review_status 'needs_review' or units_unconfirmed must be reviewed by
    a person before they are sent to analyse()."""
    try:
        detected, warnings = files.validate_upload(filename, content, "urban_plan")
    except files.UploadError as exc:
        raise _err(["file"], str(exc))
    # Validate the site the same way analyse() does, so a Feature / FeatureCollection boundary works
    # and a bad one is a located input error rather than a crash.
    try:
        lat, lon = geo.validate_coordinates(site.get("latitude"), site.get("longitude"))
        boundary = geo.validate_boundary(site["boundary"], lat, lon)[0] if site.get("boundary") is not None else None
    except geo.GeometryError as exc:
        raise _err(["site"], str(exc))
    proj = {"id": None, "latitude": lat, "longitude": lon, "boundary": boundary}
    with tempfile.TemporaryDirectory(prefix="urbanova_plan_") as tmp:
        path = Path(tmp) / ("plan" + Path(filename).suffix.lower())
        path.write_bytes(content)
        try:
            out = plans.extract_features(proj, None, detected, content, str(path), epsg)
        except (plans.PlanError, geo.GeometryError) as exc:
            raise _err(["file"], str(exc))
    for r in out["records"]:
        r["active"] = 1
    fc = plans.feature_collection(out["records"])
    for f, r in zip(fc["features"], out["records"]):
        f["properties"]["issues"] = r["correction_history"][0]["issues"]
    return jsonable({"detected_type": detected, "warnings": warnings, "features": fc, "summary": out["summary"]})


def extract_rules(filename: str, content: bytes) -> dict[str, Any]:
    """Extract candidate planning conditions from a PDF with a text layer (English or Arabic).

    Returns {"pages", "pages_with_text", "candidates", "conflicts", "issues"}. Every candidate keeps its
    exact wording and page and must be verified by a person before it is sent to analyse() as a
    verified rule. Values and notes are in English; the wording is kept in the document's language."""
    try:
        files.validate_upload(filename, content, "planning_document")
    except files.UploadError as exc:
        raise _err(["file"], str(exc))
    with tempfile.TemporaryDirectory(prefix="urbanova_doc_") as tmp:
        path = Path(tmp) / "document.pdf"
        path.write_bytes(content)
        try:
            pages, issues = documents.extract_text(str(path))
        except documents.DocumentError as exc:
            raise _err(["file"], str(exc))
    cands = documents.find_candidates(pages)
    conflicts = documents.detect_conflicts(cands)
    conflict_types = {c["rule_type"] for c in conflicts}
    for c in cands:
        if c["rule_type"] in conflict_types:
            c["notes"] = list(c["notes"]) + ["Conflicts with another value in the same document."]
            c["confidence"] = "low"
        c["unit"] = documents.RULE_TYPES[c["rule_type"]]["unit"]
    return {"pages": len(pages), "pages_with_text": sum(1 for p in pages if len(p.strip()) >= 20), "candidates": cands,
            "conflicts": conflicts, "issues": issues}


def objectives_modules() -> dict[str, list[str]]:
    """Which modules each objective runs by default."""
    return {k: sorted(v) for k, v in OBJECTIVE_MODULES.items()}
