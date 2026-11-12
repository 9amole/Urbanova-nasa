"""Project service (FR-01, FR-35, Module 2)."""

from __future__ import annotations

from typing import Any

from . import db, geo

PROJECT_TYPES = ["residential", "commercial", "mixed_use", "public_space", "industrial", "institutional", "other"]
OBJECTIVES = {
    "main_concerns": "What are the site's main environmental concerns?",
    "surface_heat": "Which areas appear most exposed to surface heat?",
    "vegetation": "Where is vegetation limited?",
    "shade": "How does the proposed layout affect supported shade indicators?",
    "compare_alternatives": "Which alternative performs better across selected metrics?",
    "planning_compliance": "Does the design comply with the verified planning rules?",
    "data_gaps": "What data is still needed for a reliable assessment?",
}
EDITABLE = {"name", "description", "project_type", "country", "city_or_address", "objectives", "analysis_period", "ai_enabled", "synthetic_data"}


class ProjectError(ValueError):
    pass


def _clean_objectives(values: Any) -> list[str]:
    if values is None:
        return ["main_concerns"]
    if not isinstance(values, list):
        raise ProjectError("Objectives must be a list.")
    unknown = [v for v in values if v not in OBJECTIVES]
    if unknown:
        raise ProjectError(f"Unknown analysis objective(s): {', '.join(map(str, unknown))}.")
    return values or ["main_concerns"]


def _clean_period(period: Any) -> dict | None:
    if not period:
        return None
    try:
        start, end = int(period["start_year"]), int(period["end_year"])
    except (KeyError, TypeError, ValueError):
        raise ProjectError("The analysis period needs a start_year and end_year.")
    if not 1981 <= start <= end:
        raise ProjectError("The analysis period must start in 1981 or later and end on or after its start.")
    from datetime import date
    if end >= date.today().year:
        raise ProjectError("The climate analysis period must end with a completed calendar year.")
    if end - start > 29:
        raise ProjectError("The analysis period may span at most 30 years.")
    return {"start_year": start, "end_year": end}


def create_project(user: dict, payload: dict) -> tuple[dict, list[str]]:
    name = (payload.get("name") or "").strip()
    if not name:
        raise ProjectError("A project name is required.")
    if len(name) > 160:
        raise ProjectError("The project name must be 160 characters or fewer.")
    lat, lon = geo.validate_coordinates(payload.get("latitude"), payload.get("longitude"))
    ptype = payload.get("project_type") or None
    if ptype and ptype not in PROJECT_TYPES:
        raise ProjectError(f"Project type must be one of: {', '.join(PROJECT_TYPES)}.")
    warnings: list[str] = []
    boundary = None
    if payload.get("boundary"):
        boundary, warnings = geo.validate_boundary(payload["boundary"], lat, lon)
    now = db.now()
    project = {
        "id": db.new_id("prj"), "owner_id": user["id"], "name": name,
        "description": (payload.get("description") or "").strip() or None,
        "project_type": ptype, "latitude": lat, "longitude": lon, "boundary": boundary,
        "country": (payload.get("country") or "").strip() or None,
        "city_or_address": (payload.get("city_or_address") or "").strip() or None,
        "objectives": _clean_objectives(payload.get("objectives")),
        "analysis_period": _clean_period(payload.get("analysis_period")),
        "location_confirmed": 0, "history": [{"at": now, "by": user["id"], "event": "created"}],
        "created_at": now, "updated_at": now,
    }
    db.insert("projects", project)
    db.insert("project_members", {"project_id": project["id"], "user_id": user["id"], "role": "owner", "added_at": now})
    return project, warnings


def update_project(project: dict, user: dict, payload: dict) -> tuple[dict, list[str]]:
    changes: dict[str, Any] = {}
    warnings: list[str] = []
    for key in EDITABLE & payload.keys():
        val = payload[key]
        if key == "name":
            val = (val or "").strip()
            if not val:
                raise ProjectError("A project name is required.")
        elif key in ("ai_enabled", "synthetic_data"):
            if not isinstance(val, bool):
                raise ProjectError(f"{key} must be true or false.")
            val = int(val)
        elif key == "project_type" and val and val not in PROJECT_TYPES:
            raise ProjectError(f"Project type must be one of: {', '.join(PROJECT_TYPES)}.")
        elif key == "objectives":
            val = _clean_objectives(val)
        elif key == "analysis_period":
            val = _clean_period(val)
        changes[key] = val
    location_changed = False
    if "latitude" in payload or "longitude" in payload or "boundary" in payload:
        lat, lon = geo.validate_coordinates(payload.get("latitude", project["latitude"]), payload.get("longitude", project["longitude"]))
        boundary = payload.get("boundary", project["boundary"])
        if boundary:
            boundary, warnings = geo.validate_boundary(boundary, lat, lon)
        changes.update({"latitude": lat, "longitude": lon, "boundary": boundary or None})
        location_changed = True
    if not changes:
        return project, warnings
    now = db.now()
    history = list(project.get("history") or [])
    history.append({
        "at": now, "by": user["id"], "event": "updated", "fields": sorted(changes),
        # Keep previous location values so earlier runs remain explainable (FR-35).
        "previous": {k: project.get(k) for k in changes if k in ("latitude", "longitude", "boundary")},
    })
    changes["history"] = history
    changes["updated_at"] = now
    if location_changed:
        # A moved site must be re-confirmed before costly analysis (FR-01 step 5).
        changes["location_confirmed"] = 0
        changes["location_confirmed_at"] = None
    db.update("projects", "id", project["id"], changes)
    return db.get("projects", project["id"]), warnings


def confirm_location(project: dict, user: dict) -> dict:
    now = db.now()
    history = list(project.get("history") or []) + [{"at": now, "by": user["id"], "event": "location_confirmed"}]
    db.update("projects", "id", project["id"], {"location_confirmed": 1, "location_confirmed_at": now, "history": history, "updated_at": now})
    return db.get("projects", project["id"])


def site_area_m2(project: dict) -> float | None:
    if not project.get("boundary"):
        return None
    from shapely.geometry import shape
    return geo.area_m2(shape(project["boundary"]), geo.utm_crs_for(project["latitude"], project["longitude"]))


def missing_information(project: dict) -> list[dict]:
    """Optional information that is absent, and what it limits (FR-01 outputs)."""
    items = []
    pid = project["id"]
    if not project.get("boundary"):
        items.append({"item": "Site boundary", "effect": "Site area, building coverage, green-space proportion and compliance checks cannot be calculated; satellite statistics use a 100 m point buffer."})
    if not project.get("location_confirmed"):
        items.append({"item": "Location confirmation", "effect": "Analysis runs are blocked until you confirm the site location on the map."})
    if not project.get("project_type"):
        items.append({"item": "Project type", "effect": "Only used for labelling and rule applicability; no calculation is blocked."})
    if not db.select("features", "project_id = ? AND active = 1", [pid]):
        items.append({"item": "Urban plan / design geometry", "effect": "Urban-form indicators, shade analysis and scenario comparison are unavailable."})
    if not db.select("rules", "project_id = ? AND status = 'verified'", [pid]):
        items.append({"item": "Verified planning rules", "effect": "Planning compliance is reported as 'not assessed'."})
    if not db.select("rate_items", "project_id = ?", [pid]):
        items.append({"item": "Unit rates for costing", "effect": "Cost estimates report quantities only; no prices are invented."})
    return items


def public_project(project: dict, role: str | None = None) -> dict:
    out = {k: v for k, v in project.items() if k != "history"}
    out["location_confirmed"] = bool(project.get("location_confirmed"))
    out["ai_enabled"] = bool(project.get("ai_enabled"))
    out["synthetic_data"] = bool(project.get("synthetic_data"))
    out["site_area_m2"] = site_area_m2(project)
    out["local_crs"] = geo.utm_crs_for(project["latitude"], project["longitude"])
    if role:
        out["role"] = role
    return out


def dashboard(project: dict) -> dict:
    """Site dashboard (PHASE2_SPEC §8.2): condition, priority hotspots, question answers and data completeness from the
    latest successful baseline run; an empty state with the next step when there is none."""
    from . import comparison, orchestrator, readiness

    base = next(iter(db.select("scenarios", "project_id = ? AND kind = 'baseline' AND status = 'active'", [project["id"]])), None)
    run = comparison.latest_run(base["id"]) if base else None
    rd = readiness.assess(project)
    completeness = {"readiness": {s: sum(1 for a in rd["analyses"] if a["status"] == s) for s in ("ready", "ready_with_limitations", "blocked")}}
    if not run:
        from .dashboard import SITE_NEXT_STEP, SITE_TILES, _metric, site_run

        srun = site_run(project["id"])
        if srun:  # site analysis only (S11): what the land is like today
            sb = orchestrator.run_bundle(srun)
            ms_ = {m["metric_name"]: m for m in sb["metrics"]}
            return {"state": "site", "run_id": srun["id"], "run_status": srun["status"], "completed_at": srun.get("completed_at"),
                    "stale": bool(sb["run"]["stale_modules"]), "site": [_metric(ms_.get(k), k, label) for k, label in SITE_TILES],
                    "existing": [c["statement"] for c in sb.get("site_context") or []], "next_step": SITE_NEXT_STEP, "completeness": completeness}
        step = ("Analyse the site now, or upload a design plan on the Inputs tab and create the baseline." if not base
                else "Run the analysis for the baseline on the Run analysis tab.")
        return {"state": "empty", "next_step": step, "completeness": completeness, "can_analyse_site": not base}
    b = orchestrator.run_bundle(run)
    hs = b["hotspots"]
    from .report_front import _options_by_hotspot, suited

    best = {hid: o for hid, opts in _options_by_hotspot(b).items() for o in [suited(opts)[0]] if o}  # the same choice the report explains
    ins = next(iter(db.select("insights", "run_id = ?", [run["id"]], order="created_at DESC")), None)
    answers = {a["question_id"]: " ".join(c["text"] for c in a["claims"]) for a in (ins or {}).get("blocks", {}).get("answers") or []}
    ms = run.get("module_status") or {}
    hst = ms.get("hotspots") or {}
    assessed = hst.get("status") in ("completed", "partial")
    return {
        "state": "ready", "run_id": run["id"], "run_status": run["status"], "completed_at": run.get("completed_at"), "stale": bool(b["run"]["stale_modules"]),
        "condition": {"design": {"hotspots": len(hs), **{lvl: sum(1 for h in hs if h["severity"] == lvl) for lvl in ("high", "moderate", "low")},
                                 "located_area_m2": round(sum(h["area_m2"] for h in hs if not h.get("site_wide")), 1), "assessed": assessed,
                                 "not_assessed_reason": None if assessed else (hst.get("reason") or "Design hotspot detection was not part of this run.")},
                      "existing": [c["statement"] for c in b.get("site_context") or []]},
        "top_hotspots": [{"id": h["id"], "number": h["number"], "type_labels": h["type_labels"], "severity": h["severity"], "area_m2": h["area_m2"],
                          "main_cause": (h["likely_causes"][0]["text"] if h["likely_causes"] else None),
                          "best_option": ({"title": best[h["id"]]["title"], "size": best[h["id"]]["intensity_label"], "effect": best[h["id"]]["effect"],
                                           "cost": best[h["id"]]["cost"]} if h["id"] in best else None)} for h in hs[:3]],
        "questions": [{"objective": o["objective"], "question": o["question"], "answerable": o["answerable"],
                       "answer": answers.get(f"q_{o['objective']}") or (("Answered with the available evidence from: " + "; ".join(o.get("supported_by") or []))
                                                                      if o["answerable"] else "Cannot be answered with the available data: " + "; ".join(o.get("not_available") or [])),
                       "source": "insights" if f"q_{o['objective']}" in answers else "readiness"} for o in b["objectives"]],
        "completeness": {**completeness, "modules": {k: {"label": v.get("label"), "status": v["status"]} for k, v in ms.items() if v.get("status") != "skipped"}},
        "insights": {"generated_by": ins["generated_by"], "model": ins.get("model")} if ins else None,
    }
