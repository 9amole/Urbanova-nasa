"""Run the analysis modules for one design, with no database, web or authentication dependency.

Both the prototype app (orchestrator) and the engine API call `run_design`, so there is one
code path for every analysis. Provenance goes to the active recorder (see urbanova.recording).
"""

from __future__ import annotations

import copy
import logging
import traceback
from typing import Callable

from .. import findings, interventions, recording
from ..analysis import climate, climate_trends, compliance, site_trends, context, heat_stress, hotspots, landcover, solar, surface_heat, urban_form, vegetation
from ..analysis.common import FAILED, SKIPPED, Context
from ..readiness import ANALYSES, CONDITION, RUNNABLE

log = logging.getLogger("urbanova.engine")

MODULES = {
    "climate_baseline": climate.run, "climate_trends": climate_trends.run, "site_context": context.run, "land_cover": landcover.run, "urban_form": urban_form.run, "vegetation": vegetation.run,
    "surface_heat": surface_heat.run, "site_trends": site_trends.run, "solar_shade": solar.run, "heat_stress": heat_stress.run, "planning_compliance": compliance.run,
    "hotspots": hotspots.run, "interventions": interventions.run,
}
# Modules whose result depends only on the site and settings, never on the design. In a request
# with several designs they run once and every design receives the same result (remediation D4),
# together with the values they leave in ctx.shared for later modules.
SITE_LEVEL = {"climate_baseline", "climate_trends", "site_trends", "site_context", "land_cover", "surface_heat"}
# Existing-land context records (H2 hot cluster first, then land cover): each lives in its module's details.
CONTEXT_MODULES = ("surface_heat", "land_cover")
# Site analysis (no design, stage S11): these need a design plan, so they are not part of the run rather than "blocked".
DESIGN_ONLY = {"urban_form", "planning_compliance", "hotspots", "interventions"}
SITE_MODE_NOTE = "Not part of a site analysis: upload a design plan (GeoJSON or DXF) to get design indicators, hotspots, options, costs and rule checks."


def run_design(ctx: Context, selected: list[str], on_module: Callable[[str, int], None] | None = None) -> dict:
    """Run the selected modules in order for ctx.scenario (None = site only).

    Returns {"results", "module_status", "metrics", "layers", "dataset_ids", "findings", "recommendations", "status", "warnings",
    "hotspots", "site_context", "analysis_mode"}. With no scenario the run is a site analysis ("site"): the design-only
    modules are left out with a note instead of being blocked.
    Metric, finding and recommendation records carry ids; nothing is persisted here.
    """
    results: dict[str, dict] = {}
    module_status: dict[str, dict] = {}
    metrics: list[dict] = []
    layers: list[dict] = []
    dataset_ids: list[str] = []
    mode = "site" if not ctx.scenario else "design"
    for i, mod in enumerate(RUNNABLE):
        if mode == "site" and mod in DESIGN_ONLY:
            module_status[mod] = {"status": SKIPPED, "label": ANALYSES[mod], "reason": SITE_MODE_NOTE, "site_mode": True}
            continue
        if mod not in selected:
            module_status[mod] = {"status": SKIPPED, "label": ANALYSES[mod], "reason": "Not selected for this run."}
            continue
        if on_module:
            on_module(mod, i)
        res = _run_module(ctx, mod)
        results[mod] = res
        for m in res["metrics"]:
            cond = m.get("condition") or CONDITION[mod]
            metrics.append({**m, "id": recording.new_id("met"), "run_id": ctx.run_id, "module": mod, "calculated_at": recording.now(),
                            "condition": cond, "design_dependent": int(cond == "design")})  # design_dependent: derived, for contract 1.0 readers
        for lay in res["layers"]:
            layers.append({"id": recording.new_id("lyr"), "run_id": ctx.run_id, "module": mod, **lay})
        dataset_ids.extend(d for d in res["datasets"] if d not in dataset_ids)
        module_status[mod] = {"status": res["status"], "label": ANALYSES[mod], "reason": res.get("reason"), "warnings": res["warnings"],
                              "limitations": res["limitations"], "details": jsonable(res["details"]), "provider_failures": res.get("provider_failures", []),
                              "shared_across_designs": bool(res.get("_shared"))}
    hotspots = link_hotspots(results.get("hotspots", {}).get("hotspots") or [], metrics, ctx.run_id)
    site_context = [results[m]["details"]["site_context"] for m in CONTEXT_MODULES if (results.get(m, {}).get("details") or {}).get("site_context")]
    fnds, recs = ([], []) if ctx.partial else findings.generate({"id": ctx.run_id}, ctx.project, results, metrics, ctx.shared, hotspots)
    ran = [s["status"] for s in module_status.values() if s["status"] != SKIPPED]
    if ran and all(s == "completed" for s in ran):
        status = "succeeded"
    elif any(s in ("completed", "partial") for s in ran):
        status = "partially_succeeded"
    else:
        status = "failed"
    warnings = [f"{ANALYSES[m]}: {w}" for m, s in module_status.items() for w in s.get("warnings", [])]
    return {"results": results, "module_status": module_status, "metrics": metrics, "layers": layers, "dataset_ids": dataset_ids,
            "findings": fnds, "recommendations": recs, "status": status, "warnings": warnings, "hotspots": hotspots, "site_context": jsonable(site_context),
            "analysis_mode": mode}


def link_hotspots(hotspots: list[dict], metrics: list[dict], run_id: str) -> list[dict]:
    """Give each hotspot an id and replace the metric names it cites with the ids of this run's available metrics
    (the id chain metric.id → hotspot.supporting_metrics[].source_metric_ids / likely_causes[].evidence_ids)."""
    ids = {m["metric_name"]: m["id"] for m in metrics if m["state"] == "available"}
    out = []
    for h in hotspots:
        sm = [{**{k: v for k, v in s.items() if k != "source_metrics"}, "source_metric_ids": [ids[n] for n in s["source_metrics"] if n in ids]}
              for s in h["supporting_metrics"]]
        cs = [{**{k: v for k, v in c.items() if k != "evidence_metrics"}, "evidence_ids": [ids[n] for n in c["evidence_metrics"] if n in ids]}
              for c in h["likely_causes"]]
        out.append(jsonable({**h, "id": recording.new_id("hs"), "run_id": run_id, "supporting_metrics": sm, "likely_causes": cs}))
    return out


def _run_module(ctx: Context, mod: str) -> dict:
    key = ("module", mod)
    if mod in SITE_LEVEL and key in ctx.site_cache:
        res, shared = ctx.site_cache[key]
        ctx.shared.update(shared)
        return {**copy.deepcopy({k: v for k, v in res.items() if k != "_shared"}), "_shared": True}
    before = set(ctx.shared)
    try:
        res = MODULES[mod](ctx)
    except Exception as exc:
        log.error("module %s failed in run %s: %s", mod, ctx.run_id, traceback.format_exc())
        res = {"status": FAILED, "metrics": [], "layers": [], "datasets": [], "warnings": [], "limitations": [], "details": {},
               "reason": f"The module failed unexpectedly ({type(exc).__name__}). Other modules were not affected.", "provider_failures": []}
    if mod in SITE_LEVEL:
        # Cache every outcome, failures included: all designs of one request must see the same site data.
        ctx.site_cache[key] = (copy.deepcopy(res), {k: ctx.shared[k] for k in set(ctx.shared) - before})
    return res


def jsonable(obj):
    import json

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
