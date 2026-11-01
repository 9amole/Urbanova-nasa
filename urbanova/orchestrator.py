"""Analysis orchestration, background jobs, reproducibility and staleness (FR-32, FR-36, FR-47, FR-49, B.5)."""

from __future__ import annotations

import hashlib
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import ANALYSIS_VERSION, __version__, db, documents, files, findings
from .analysis import climate, compliance, context, solar, surface_heat, urban_form, vegetation
from .analysis.common import FAILED, SKIPPED, Context
from .readiness import ANALYSES, RUNNABLE
from .settings import settings

log = logging.getLogger("urbanova.orchestrator")

MODULES = {
    "climate_baseline": climate.run, "site_context": context.run, "urban_form": urban_form.run, "vegetation": vegetation.run,
    "surface_heat": surface_heat.run, "solar_shade": solar.run, "planning_compliance": compliance.run,
}
OBJECTIVE_MODULES = {
    "main_concerns": set(RUNNABLE),
    "surface_heat": {"climate_baseline", "surface_heat", "urban_form", "vegetation"},
    "vegetation": {"vegetation", "urban_form"},
    "shade": {"climate_baseline", "solar_shade", "urban_form"},
    "compare_alternatives": {"urban_form", "solar_shade", "planning_compliance", "climate_baseline"},
    "planning_compliance": {"urban_form", "planning_compliance"},
    "data_gaps": set(),
}
# Which inputs each module depends on — drives stale-result detection (B.5).
DEPENDS = {
    "climate_baseline": {"location", "period"},
    "site_context": {"location", "boundary"},
    "urban_form": {"design", "boundary", "site_area_basis"},
    "vegetation": {"location", "boundary", "design"},
    "surface_heat": {"location", "boundary"},
    "solar_shade": {"design", "boundary", "period"},
    "planning_compliance": {"design", "boundary", "rules", "site_area_basis"},
}
DEFAULT_CONFIG = {
    "context_buffer_m": 500, "lst_buffer_m": 1000, "ndvi_buffer_m": 500, "satellite_years": 3, "lst_max_scenes": 6,
    "ndvi_max_scenes": 4, "ndvi_lookback_days": 365, "max_scene_cloud_pct": 40, "min_valid_site_fraction": 0.6,
}
ALLOWED_CONFIG = set(DEFAULT_CONFIG) | {"modules", "hot_months", "solar_date", "climate_period"}

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
              "satellite_years": (1, 10), "lst_max_scenes": (1, 12), "ndvi_max_scenes": (1, 8), "ndvi_lookback_days": (30, 1095), "min_valid_site_fraction": (0.3, 1)}
    for key, (lo, hi) in limits.items():
        try:
            val = float(cfg[key])
        except (TypeError, ValueError):
            raise RunError(f"{key} must be a number.")
        if not lo <= val <= hi:
            raise RunError(f"{key} must be between {lo} and {hi}.")
        cfg[key] = int(val) if key in ("satellite_years", "lst_max_scenes", "ndvi_max_scenes", "ndvi_lookback_days") else val
    if cfg.get("solar_date"):
        try:
            from datetime import date as _d

            _d.fromisoformat(str(cfg["solar_date"]))
        except ValueError:
            raise RunError("solar_date must be a date in YYYY-MM-DD format.")
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
    if "planning_compliance" in selected and "urban_form" not in selected:
        selected.append("urban_form")  # compliance consumes urban-form measurements
    cfg["modules"] = [m for m in RUNNABLE if m in selected]
    return cfg


def start_run(project: dict, scenario: dict | None, user: dict, payload: dict, *, background: bool = True) -> dict:
    if not project.get("location_confirmed"):
        raise RunError("Confirm the site location on the map before running analyses (avoids costly runs on the wrong site).")
    if scenario and (scenario["project_id"] != project["id"] or scenario["status"] != "active"):
        raise RunError("The selected scenario is not an active scenario of this project.")
    cfg = resolve_config(project, payload or {})
    now = db.now()
    job = {"id": db.new_id("job"), "project_id": project["id"], "job_type": "analysis_run", "status": "queued", "progress": 0, "stage": "queued",
           "input": {"scenario_id": (scenario or {}).get("id"), "modules": cfg["modules"]}, "created_at": now}
    db.insert("jobs", job)
    run = {
        "id": db.new_id("run"), "project_id": project["id"], "scenario_id": (scenario or {}).get("id"), "job_id": job["id"],
        "configuration": cfg, "fingerprint": fingerprint(project, scenario, cfg), "input_dataset_ids": [], "analysis_version": ANALYSIS_VERSION,
        "software_version": __version__, "status": "queued", "module_status": {}, "warnings": [], "created_by": user["id"],
    }
    db.insert("runs", run)
    if background:
        _executor.submit(_safe_execute, run["id"])
    else:
        _safe_execute(run["id"])
    return db.get("runs", run["id"])


def _safe_execute(run_id: str) -> None:
    try:
        execute(run_id)
    except Exception:  # never leave a job "running" forever
        log.exception("run %s crashed", run_id)
        run = db.get("runs", run_id)
        db.update("runs", "id", run_id, {"status": "failed", "completed_at": db.now()})
        db.update("jobs", "id", run["job_id"], {"status": "failed", "completed_at": db.now(), "error_code": "internal_error",
                                                "error_details": {"message": "Unexpected internal error; see server log."}})


def execute(run_id: str) -> None:
    run = db.get("runs", run_id)
    project = db.get("projects", run["project_id"])
    scenario = db.get("scenarios", run["scenario_id"]) if run["scenario_id"] else None
    cfg = run["configuration"]
    db.update("runs", "id", run_id, {"status": "running", "started_at": db.now()})
    db.update("jobs", "id", run["job_id"], {"status": "running", "started_at": db.now()})
    ctx = Context(project=project, scenario=scenario, config=cfg, run_id=run_id, out_dir=files.outputs_dir(project["id"], run_id))
    results: dict[str, dict] = {}
    module_status: dict[str, dict] = {}
    metric_rows: list[dict] = []
    dataset_ids: list[str] = []
    selected = cfg["modules"]
    for i, mod in enumerate(RUNNABLE):
        if mod not in selected:
            module_status[mod] = {"status": SKIPPED, "label": ANALYSES[mod], "reason": "Not selected for this run."}
            continue
        db.update("jobs", "id", run["job_id"], {"stage": ANALYSES[mod], "progress": round(i / len(RUNNABLE), 2)})
        try:
            res = MODULES[mod](ctx)
        except Exception as exc:
            log.error("module %s failed in run %s: %s", mod, run_id, traceback.format_exc())
            res = {"status": FAILED, "metrics": [], "layers": [], "datasets": [], "warnings": [], "limitations": [], "details": {},
                   "reason": f"The module failed unexpectedly ({type(exc).__name__}). Other modules were not affected.", "provider_failures": []}
        results[mod] = res
        for m in res["metrics"]:
            row = {**m, "id": db.new_id("met"), "run_id": run_id, "module": mod, "calculated_at": db.now(), "design_dependent": int(m["design_dependent"])}
            db.insert("metrics", row)
            metric_rows.append(row)
        for lay in res["layers"]:
            db.insert("layers", {"id": db.new_id("lyr"), "run_id": run_id, "module": mod, **lay})
        dataset_ids.extend(res["datasets"])
        module_status[mod] = {"status": res["status"], "label": ANALYSES[mod], "reason": res.get("reason"), "warnings": res["warnings"],
                              "limitations": res["limitations"], "details": _jsonable(res["details"]), "provider_failures": res.get("provider_failures", [])}
        db.update("runs", "id", run_id, {"module_status": module_status})
    db.update("jobs", "id", run["job_id"], {"stage": "Findings and recommendations", "progress": 0.95})
    fnds, recs = findings.generate(run, project, results, metric_rows, ctx.shared)
    for f in fnds:
        db.insert("findings", f)
    for r in recs:
        db.insert("recommendations", r)
    ran = [s["status"] for m, s in module_status.items() if s["status"] != SKIPPED]
    if ran and all(s == "completed" for s in ran):
        status = "succeeded"
    elif any(s in ("completed", "partial") for s in ran):
        status = "partially_succeeded"
    else:
        status = "failed"
    warnings = [f"{ANALYSES[m]}: {w}" for m, s in module_status.items() for w in s.get("warnings", [])]
    db.update("runs", "id", run_id, {"status": status, "completed_at": db.now(), "module_status": module_status, "input_dataset_ids": dataset_ids, "warnings": warnings})
    db.update("jobs", "id", run["job_id"], {"status": status, "progress": 1.0, "stage": "done", "completed_at": db.now(),
                                            "output": {"run_id": run_id, "findings": len(fnds), "recommendations": len(recs)}})


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=_default))


def _default(o):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return None
    except ImportError:
        pass
    return str(o)


def run_bundle(run: dict) -> dict:
    """Everything needed to present or export a run."""
    metrics = db.select("metrics", "run_id = ?", [run["id"]], order="module, metric_name")
    return {
        "run": {**run, "stale_modules": stale_modules(run)},
        "metrics": metrics,
        "layers": [{k: v for k, v in lay.items() if k != "path"} for lay in db.select("layers", "run_id = ?", [run["id"]])],
        "findings": sorted(db.select("findings", "run_id = ?", [run["id"]]), key=lambda f: -findings.SEVERITY_ORDER.get(f["severity"], 0)),
        "recommendations": db.select("recommendations", "run_id = ?", [run["id"]]),
        "datasets": [_with_approval(db.get("datasets", d)) for d in run.get("input_dataset_ids") or []],
        "objectives": objective_answers(run),
    }


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
    from .providers.base import registry

    p = registry().get(d["source_id"], {})
    status = p.get("approval_status")
    label = APPROVAL_LABELS.get(status, status)
    if status == "approved":
        label = f"Approved by {p.get('approved_by', 'unknown')} on {p.get('approved_on', '?')}"
    return {**d, "approval_status": status, "approval_label": label, "approval_note": p.get("approval_note"), "usage_context": settings.usage_context}


def objective_answers(run: dict) -> list[dict]:
    """Which selected questions this run can answer, and why not (FR-49)."""
    from .projects import OBJECTIVES

    project = db.get("projects", run["project_id"])
    ms = run.get("module_status") or {}
    available = {m["metric_name"] for m in db.select("metrics", "run_id = ? AND state = 'available'", [run["id"]])}
    out = []
    for obj in project.get("objectives") or []:
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
