"""Analysis orchestration, background jobs, reproducibility and staleness (FR-32, FR-36, FR-47, FR-49, B.5)."""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import ANALYSIS_VERSION, __version__, db, documents, files, findings
from .analysis.common import SKIPPED, Context
from .engine.core import CONTEXT_MODULES, MODULES, run_design
from .readiness import ANALYSES, RUNNABLE
from .settings import settings

log = logging.getLogger("urbanova.orchestrator")
OBJECTIVE_MODULES = {
    "main_concerns": set(RUNNABLE),
    "surface_heat": {"climate_baseline", "climate_trends", "site_trends", "surface_heat", "urban_form", "vegetation", "solar_shade", "heat_stress", "hotspots"},
    "vegetation": {"vegetation", "urban_form", "site_trends"},
    "shade": {"climate_baseline", "solar_shade", "urban_form", "heat_stress", "hotspots", "interventions"},
    "compare_alternatives": {"urban_form", "solar_shade", "planning_compliance", "climate_baseline"},
    "planning_compliance": {"urban_form", "planning_compliance"},
    "data_gaps": set(),
}
# Which inputs each module depends on — drives stale-result detection (B.5).
DEPENDS = {
    "climate_baseline": {"location", "period"},
    "climate_trends": {"location"},
    "site_context": {"location", "boundary"},
    "land_cover": {"location", "boundary"},
    "urban_form": {"design", "boundary", "site_area_basis"},
    "vegetation": {"location", "boundary", "design"},
    "surface_heat": {"location", "boundary"},
    "site_trends": {"location", "boundary", "period"},
    "solar_shade": {"design", "boundary", "period"},
    "heat_stress": {"location", "design", "boundary", "period"},
    "planning_compliance": {"design", "boundary", "rules", "site_area_basis"},
    "hotspots": {"location", "design", "boundary", "period", "rules", "site_area_basis"},
    "interventions": {"location", "design", "boundary", "period", "rules", "site_area_basis", "rates"},
}
# Modules whose results a module consumes (added automatically when it is selected).
NEEDS = {
    "planning_compliance": ("urban_form",),
    "heat_stress": ("climate_baseline", "solar_shade"),
    "hotspots": ("urban_form", "solar_shade", "heat_stress", "climate_baseline"),  # H4 uses planning_compliance only when selected
    "interventions": ("hotspots",),
}
# Extra inputs when LST downscaling is on (S7): the Sentinel-2 predictors and the land-cover classes.
DOWNSCALE_NEEDS = ("vegetation", "land_cover")
DEFAULT_CONFIG = {
    "context_buffer_m": 500, "lst_buffer_m": 1000, "ndvi_buffer_m": 500, "satellite_years": 3, "lst_max_scenes": 6,
    "ndvi_max_scenes": 4, "ndvi_lookback_days": 365, "max_scene_cloud_pct": 40, "min_valid_site_fraction": 0.6, "site_trend_scenes_per_year": 2,
}
ALLOWED_CONFIG = set(DEFAULT_CONFIG) | {"modules", "hot_months", "solar_date", "climate_period", "budget_amount", "budget_currency", "lst_downscale"}

_executor = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="urbanova-job")


class RunError(ValueError):
    pass


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fingerprint(project: dict, scenario: dict | None, config: dict) -> dict:
    rules = sorted((r["rule_type"], r["value"], r["value_text"]) for r in documents.verified_rules(project["id"]).values())
    return {
        "location": _h([project["latitude"], project["longitude"]]),
        "boundary": _h(project.get("boundary")),
        "design": scenario["design_hash"] if scenario else None,
        "rules": _h(rules),
        "rates": _h([sorted((r["quantity_type"], r["rate"], r["currency"], r["price_date"], r.get("maintenance_per_year")) for r in db.select("rate_items", "project_id = ?", [project["id"]])),
                     config.get("budget_amount"), config.get("budget_currency")]),
        "site_area_basis": _h(project.get("verified_site_area_m2")),
        "period": _h([project.get("analysis_period"), config.get("climate_period"), config.get("hot_months"), config.get("solar_date")]),
        "analysis_version": ANALYSIS_VERSION,
    }


def stale_modules(run: dict) -> dict[str, list[str]]:
    """Modules of a run whose inputs have changed since it ran, with the changed inputs."""
    project = db.get("projects", run["project_id"])
    scenario = db.get("scenarios", run["scenario_id"]) if run.get("scenario_id") else None
    now_fp = fingerprint(project, scenario, run["configuration"])
    old = run.get("fingerprint") or {}
    changed = {k for k in now_fp if now_fp[k] != old.get(k)}
    out = {}
    for mod, st in (run.get("module_status") or {}).items():
        if st.get("status") == SKIPPED:
            continue
        hit = sorted((DEPENDS.get(mod, set()) | {"analysis_version"}) & changed)
        if hit:
            out[mod] = hit
    return out


def resolve_config(project: dict, payload: dict) -> dict:
    unknown = set(payload) - ALLOWED_CONFIG
    if unknown:
        raise RunError(f"Unknown analysis settings: {', '.join(sorted(unknown))}.")
    cfg = {**DEFAULT_CONFIG, **{k: v for k, v in payload.items() if k != "modules"}}
    limits = {"context_buffer_m": (100, 3000), "lst_buffer_m": (100, 3000), "ndvi_buffer_m": (100, 3000), "max_scene_cloud_pct": (1, 80),
              "satellite_years": (1, 10), "lst_max_scenes": (1, 12), "ndvi_max_scenes": (1, 8), "ndvi_lookback_days": (30, 1095), "min_valid_site_fraction": (0.3, 1),
              "site_trend_scenes_per_year": (1, 3)}
    for key, (lo, hi) in limits.items():
        try:
            val = float(cfg[key])
        except (TypeError, ValueError):
            raise RunError(f"{key} must be a number.")
        if not lo <= val <= hi:
            raise RunError(f"{key} must be between {lo} and {hi}.")
        cfg[key] = int(val) if key in ("satellite_years", "lst_max_scenes", "ndvi_max_scenes", "ndvi_lookback_days", "site_trend_scenes_per_year") else val
    if cfg.get("solar_date"):
        try:
            from datetime import date as _d

            _d.fromisoformat(str(cfg["solar_date"]))
        except ValueError:
            raise RunError("solar_date must be a date in YYYY-MM-DD format.")
    if (cfg.get("budget_amount") is None) != (not cfg.get("budget_currency")):
        raise RunError("Give both budget_amount and budget_currency, or neither.")
    if cfg.get("budget_amount") is not None:
        try:
            cfg["budget_amount"] = float(cfg["budget_amount"])
        except (TypeError, ValueError):
            raise RunError("budget_amount must be a number.")
        cur = str(cfg["budget_currency"]).strip().upper()
        if cfg["budget_amount"] <= 0 or len(cur) != 3 or not cur.isalpha():
            raise RunError("budget_amount must be positive and budget_currency a 3-letter ISO code.")
        cfg["budget_currency"] = cur
    if "lst_downscale" in cfg and not isinstance(cfg["lst_downscale"], bool):
        raise RunError("lst_downscale must be true or false.")
    if cfg.get("hot_months") and not all(isinstance(m, int) and 1 <= m <= 12 for m in cfg["hot_months"]):
        raise RunError("hot_months must be month numbers 1–12.")
    modules = payload.get("modules")
    if modules:
        bad = [m for m in modules if m not in MODULES]
        if bad:
            raise RunError(f"Unknown analysis module(s): {', '.join(bad)}.")
        selected = list(modules)
    else:
        selected = sorted(set().union(*(OBJECTIVE_MODULES.get(o, set()) for o in project.get("objectives") or ["main_concerns"])) or set(RUNNABLE))
    # Modules consume the results of earlier ones; add what a selected module needs (transitively).
    todo = list(selected)
    while todo:
        mod = todo.pop()
        for m in NEEDS.get(mod, ()) + (DOWNSCALE_NEEDS if mod == "surface_heat" and cfg.get("lst_downscale") else ()):
            if m not in selected:
                selected.append(m)
                todo.append(m)
    cfg["modules"] = [m for m in RUNNABLE if m in selected]
    return cfg


def start_run(project: dict, scenario: dict | None, user: dict, payload: dict, *, background: bool = True) -> dict:
    return start_runs(project, [scenario], user, payload, background=background)[0]


def start_runs(project: dict, scenarios: list[dict | None], user: dict, payload: dict, *, background: bool = True) -> list[dict]:
    """Create one run per scenario with identical settings and execute them in one job, sharing site
    data, so the scenarios are compared on the same observations (remediation D4)."""
    if not project.get("location_confirmed"):
        raise RunError("Confirm the site location on the map before running analyses (avoids costly runs on the wrong site).")
    for scenario in scenarios:
        if scenario and (scenario["project_id"] != project["id"] or scenario["status"] != "active"):
            raise RunError("The selected scenario is not an active scenario of this project.")
    cfg = resolve_config(project, payload or {})
    run_ids = []
    for scenario in scenarios:
        job = {"id": db.new_id("job"), "project_id": project["id"], "job_type": "analysis_run", "status": "queued", "progress": 0, "stage": "queued",
               "input": {"scenario_id": (scenario or {}).get("id"), "modules": cfg["modules"]}, "created_at": db.now()}
        db.insert("jobs", job)
        run = {
            "id": db.new_id("run"), "project_id": project["id"], "scenario_id": (scenario or {}).get("id"), "job_id": job["id"],
            "configuration": cfg, "fingerprint": fingerprint(project, scenario, cfg), "input_dataset_ids": [], "analysis_version": ANALYSIS_VERSION,
            "software_version": __version__, "status": "queued", "module_status": {}, "warnings": [], "created_by": user["id"],
        }
        db.insert("runs", run)
        run_ids.append(run["id"])
    if background:
        _executor.submit(_execute_group, run_ids)
    else:
        _execute_group(run_ids)
    return [db.get("runs", r) for r in run_ids]


def _execute_group(run_ids: list[str]) -> None:
    """The first run fetches the site data into the shared cache; the remaining runs only add their
    design-specific work on top of it, so they run in parallel and read the cache without writing to it."""
    site_cache: dict = {}
    if not run_ids:
        return
    _safe_execute(run_ids[0], site_cache)
    if len(run_ids) > 1:
        with ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="urbanova-design") as pool:
            list(pool.map(lambda rid: _safe_execute(rid, site_cache), run_ids[1:]))


def _safe_execute(run_id: str, site_cache: dict | None = None) -> None:
    try:
        execute(run_id, site_cache)
    except Exception:  # never leave a job "running" forever
        log.exception("run %s crashed", run_id)
        run = db.get("runs", run_id)
        db.update("runs", "id", run_id, {"status": "failed", "completed_at": db.now()})
        db.update("jobs", "id", run["job_id"], {"status": "failed", "completed_at": db.now(), "error_code": "internal_error",
                                                "error_details": {"message": "Unexpected internal error; see server log."}})


def execute(run_id: str, site_cache: dict | None = None) -> None:
    """Run one stored run through the engine core and persist its records (prototype app only)."""
    run = db.get("runs", run_id)
    project = db.get("projects", run["project_id"])
    scenario = db.get("scenarios", run["scenario_id"]) if run["scenario_id"] else None
    cfg = run["configuration"]
    db.update("runs", "id", run_id, {"status": "running", "started_at": db.now()})
    db.update("jobs", "id", run["job_id"], {"status": "running", "started_at": db.now()})
    ctx = Context(project=project, scenario=scenario, config=cfg, run_id=run_id, out_dir=files.outputs_dir(project["id"], run_id),
                  rules=documents.rules_for_analysis(project["id"]), site_cache=site_cache if site_cache is not None else {},
                  rates=db.select("rate_items", "project_id = ?", [project["id"]]))

    def progress(mod: str, i: int) -> None:
        db.update("jobs", "id", run["job_id"], {"stage": ANALYSES[mod], "progress": round(i / len(RUNNABLE), 2)})

    def sub_progress(text: str) -> None:  # finer stage text from long modules (the optimiser), never persisted in the run
        db.update("jobs", "id", run["job_id"], {"stage": text})

    ctx.progress = sub_progress

    out = run_design(ctx, cfg["modules"], on_module=progress)
    for row in out["metrics"]:
        db.insert("metrics", row)
    for lay in out["layers"]:
        db.insert("layers", lay)
    for f in out["findings"]:
        db.insert("findings", f)
    for r in out["recommendations"]:
        db.insert("recommendations", r)
    for h in out["hotspots"]:
        db.insert("hotspots", h)
    db.update("runs", "id", run_id, {"status": out["status"], "completed_at": db.now(), "module_status": out["module_status"],
                                     "input_dataset_ids": out["dataset_ids"], "warnings": out["warnings"]})
    db.update("jobs", "id", run["job_id"], {"status": out["status"], "progress": 1.0, "stage": "done", "completed_at": db.now(),
                                            "output": {"run_id": run_id, "findings": len(out["findings"]), "recommendations": len(out["recommendations"])}})


def run_bundle(run: dict) -> dict:
    """Everything needed to present or export a run."""
    metrics = db.select("metrics", "run_id = ?", [run["id"]], order="module, metric_name")
    return {
        "run": {**run, "stale_modules": stale_modules(run)},
        "metrics": metrics,
        "layers": [{k: v for k, v in lay.items() if k != "path"} for lay in db.select("layers", "run_id = ?", [run["id"]])],
        "findings": sorted(db.select("findings", "run_id = ?", [run["id"]]), key=findings.sort_key),
        "recommendations": db.select("recommendations", "run_id = ?", [run["id"]]),
        "hotspots": db.select("hotspots", "run_id = ?", [run["id"]], order="number"),
        "site_context": site_context(run),
        "datasets": [_with_approval(db.get("datasets", d)) for d in run.get("input_dataset_ids") or []],
        "objectives": objective_answers(run),
    }


def site_context(run: dict) -> list[dict]:
    """Existing-land context records (H2 hot cluster, land cover), kept apart from the design hotspots."""
    ms = run.get("module_status") or {}
    return [sc for m in CONTEXT_MODULES if (sc := ((ms.get(m) or {}).get("details") or {}).get("site_context"))]


APPROVAL_LABELS = {
    "fr_recommended": "Recommended in the FR; licence terms recorded but not yet confirmed by the team; used for non-commercial evaluation",
    "fr_required": "Required by the FR (user-supplied data)",
    "approved": "Approved",
    "pending_supervisor_approval": "Awaiting supervisor approval",
}


def _with_approval(d: dict | None) -> dict | None:
    """Attach the provider's approval status so every displayed source shows it (FR-09, user requirement)."""
    if not d:
        return d
    return {**d, **approval_info(d["source_id"]), "usage_context": settings.usage_context}


def approval_info(source_id: str) -> dict:
    """Approval status, a human-readable label and whether the decision maker confirmed the terms (FR-09)."""
    from .providers.base import registry

    p = registry().get(source_id, {})
    status = p.get("approval_status")
    conf = p.get("confirmation") or {}
    if status == "approved":
        label = f"Approved by {p.get('approved_by', 'unknown')} on {p.get('approved_on', '?')}"
    elif status == "fr_recommended":
        label = "Recommended in the FR" if conf else APPROVAL_LABELS[status]
    else:
        label = APPROVAL_LABELS.get(status, status)
    if status in ("approved", "fr_recommended"):
        label += (f"; terms confirmed by {conf['by']} on {conf['on']} for {conf['scope']}" if conf
                  else " — confirmation by the decision maker pending")
    if p.get("commercial_use") == "unverified":
        label += "; commercial-use terms unverified"
    return {"approval_status": status, "approval_label": label, "approval_note": p.get("approval_note"), "terms_confirmed": bool(conf)}


def objective_answers(run: dict) -> list[dict]:
    """Which selected questions this run can answer, and why not (FR-49)."""
    project = db.get("projects", run["project_id"])
    available = {m["metric_name"] for m in db.select("metrics", "run_id = ? AND state = 'available'", [run["id"]])}
    return answer_objectives(project.get("objectives") or [], run.get("module_status") or {}, available)


def answer_objectives(objectives: list[str], ms: dict, available: set[str]) -> list[dict]:
    """Database-free core of objective_answers: module statuses + names of available metrics."""
    from .projects import OBJECTIVES

    out = []
    for obj in objectives:
        needed = OBJECTIVE_MODULES.get(obj, set())
        if obj == "data_gaps":
            out.append({"objective": obj, "question": OBJECTIVES[obj], "answerable": True, "explanation": "See the readiness check and data-quality findings."})
            continue
        if obj == "compare_alternatives":
            out.append({"objective": obj, "question": OBJECTIVES[obj], "answerable": False, "supported_by": [],
                        "not_available": ["Answered on the Compare tab once the baseline and alternatives are analysed with the same settings."]})
            continue
        ok = [m for m in needed if ms.get(m, {}).get("status") in ("completed", "partial")]
        missing = [f"{ANALYSES[m]} ({ms.get(m, {}).get('status', 'not run')}: {ms.get(m, {}).get('reason') or 'see warnings'})" for m in needed if m not in ok]
        # A question is answered only if its essential evidence exists, not merely a related module.
        essential = OBJECTIVE_ESSENTIAL.get(obj)
        if obj == "planning_compliance":
            checks = ((ms.get("planning_compliance") or {}).get("details") or {}).get("checks") or []
            answerable = any(c["status"] in ("passes", "fails") for c in checks)
            if not answerable and "planning_compliance" in ok:
                missing.append("No verified rule could be checked; compliance is not assessed.")
        else:
            answerable = bool(ok) if essential is None else essential in available
        if essential and not answerable and not missing:
            missing.append(f"The required result ({essential}) was not produced; see module warnings.")
        out.append({"objective": obj, "question": OBJECTIVES[obj], "answerable": answerable, "supported_by": [ANALYSES[m] for m in ok], "not_available": missing})
    return out


# The metric that must exist for each question to count as answered.
OBJECTIVE_ESSENTIAL = {
    "surface_heat": "lst_site_hottest_decile_share_pct",
    "vegetation": "ndvi_site_vegetated_share_pct",
    "shade": "solar_open_space_shade_hours",
}
