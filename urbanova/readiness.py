"""Input readiness assessment (FR-05). Each analysis is evaluated independently."""

from __future__ import annotations

from . import db, documents, plans
from .providers.base import availability
from .settings import settings

READY, LIMITED, BLOCKED, NOT_IN_BUILD = "ready", "ready_with_limitations", "blocked", "not_in_this_build"

ANALYSES = {
    "climate_baseline": "Regional climate & heat baseline (air temperature)",
    "site_context": "Surrounding land context (OpenStreetMap)",
    "urban_form": "Site & urban-form indicators (coverage, green space)",
    "surface_heat": "Land surface temperature (Landsat) — heat analysis",
    "vegetation": "Vegetation indicator (Sentinel-2 NDVI)",
    "solar_shade": "Solar position & geometric shade",
    "planning_compliance": "Planning-rule checks",
    "scenario_comparison": "Alternative-design comparison",
    "cost_estimate": "Indicative cost estimate",
    "population_context": "Population context",
    "air_quality": "Regional air-quality context",
}
RUNNABLE = ["climate_baseline", "site_context", "urban_form", "vegetation", "surface_heat", "solar_shade", "planning_compliance"]


def _entry(key: str, status: str, missing: list[str] | None = None, prevents: str = "", next_step: str = "", limitations: list[str] | None = None) -> dict:
    return {"analysis": key, "label": ANALYSES[key], "status": status, "missing_inputs": missing or [], "what_missing_prevents": prevents,
            "next_step": next_step, "limitations": limitations or []}


def assess(project: dict, scenario: dict | None = None) -> dict:
    pid = project["id"]
    has_boundary = bool(project.get("boundary"))
    scenarios = db.select("scenarios", "project_id = ? AND status = 'active'", [pid])
    baseline = next((s for s in scenarios if s["kind"] == "baseline"), None)
    scenario = scenario or baseline
    sfeats = (scenario or {}).get("features", {}).get("features", []) if scenario else []
    extracted = plans.active_features(pid)
    buildings = [f for f in sfeats if f["properties"].get("feature_type") == "building"]
    with_h = [f for f in buildings if f["properties"].get("height_m")]
    verified = documents.verified_rules(pid)
    ambig = documents.unresolved_ambiguities(pid)
    out = []
    pt_limit = [] if has_boundary else ["No site boundary: statistics use a 100 m radius around the point."]

    def source_block(ids: list[str]) -> tuple[list[str], str]:
        """Missing-input text and next step when none of the sources may be used."""
        if settings.offline:
            return ["External data access is disabled (offline mode)"], "Enable network access (URBANOVA_OFFLINE=0)."
        reasons = [availability(i) for i in ids]
        if any(ok for ok, _, _ in reasons):
            return [], ""
        return [r for _, _, r in reasons], "A supervisor must approve a data source for this analysis (config/providers.json)."

    climate_block, climate_step = source_block(["open_meteo_archive", "nasa_power_daily"])
    out.append(_entry("climate_baseline", BLOCKED if climate_block else READY, climate_block, "Regional climate statistics.",
                      climate_step or "Run the analysis.", ["Regional gridded data (~25 km); not site microclimate."]))
    osm_block, osm_step = source_block(["osm_overpass"])
    out.append(_entry("site_context", BLOCKED if osm_block else (READY if has_boundary else LIMITED), osm_block + ([] if has_boundary else ["Site boundary"]),
                      "Precise on-site vs. surrounding separation." if not has_boundary else "", osm_step or ("Add a site boundary for better results." if not has_boundary else "Run the analysis."), pt_limit))
    if not sfeats:
        missing = ["Urban plan geometry (GeoJSON or DXF)"] + ([] if extracted else []) + ([] if has_boundary else ["Site boundary"])
        step = "Create the baseline scenario from the reviewed plan features." if extracted else "Upload an urban plan as GeoJSON or DXF."
        out.append(_entry("urban_form", BLOCKED, missing, "Building coverage, green-space and hard-surface proportions.", step))
    else:
        missing = [] if has_boundary else ["Site boundary"]
        lim = []
        needs_review = [f for f in sfeats if f["properties"].get("review_status") == "needs_review"]
        if needs_review:
            lim.append(f"{len(needs_review)} feature(s) were flagged for review when the scenario was created.")
        out.append(_entry("urban_form", READY if has_boundary and not lim else LIMITED, missing,
                          "Site-relative ratios (coverage, green share)." if not has_boundary else "", "Add a site boundary." if not has_boundary else "Run the analysis.", lim))
    lst_block, lst_step = source_block(["pc_landsat_c2_l2"])
    out.append(_entry("surface_heat", BLOCKED if lst_block else (READY if has_boundary else LIMITED), lst_block + ([] if has_boundary else ["Site boundary"]),
                      "Satellite land surface temperature (the observed-heat part of Heat Analysis)." if lst_block else ("Site-specific surface statistics." if not has_boundary else ""),
                      lst_step or "Run the analysis.", pt_limit + ["Clear-sky land surface temperature only; cloud-covered scenes are excluded."]))
    veg_block, veg_step = source_block(["pc_sentinel2_l2a"])
    out.append(_entry("vegetation", BLOCKED if veg_block else (READY if has_boundary else LIMITED), veg_block + ([] if has_boundary else ["Site boundary"]),
                      "Observed vegetation indicator." if veg_block else ("Site-specific vegetation statistics." if not has_boundary else ""), veg_step or "Run the analysis.", pt_limit))
    if not buildings:
        out.append(_entry("solar_shade", LIMITED, ["Building footprints with heights"], "Building-cast shade; only sun position is calculated.",
                          "Upload plan geometry and add building heights."))
    elif len(with_h) < len(buildings):
        out.append(_entry("solar_shade", LIMITED, [f"Heights for {len(buildings) - len(with_h)} building(s)"],
                          "Shade from buildings without heights (shade is underestimated).", "Add the missing building heights on the Inputs screen."))
    else:
        out.append(_entry("solar_shade", READY, [], "", "Run the analysis.", ["Flat terrain; off-site obstructions not modelled."]))
    if not verified:
        cands = db.select("rules", "project_id = ? AND status = 'candidate'", [pid])
        step = f"Verify the {len(cands)} candidate rule(s) on the Planning rules screen." if cands else "Upload a planning document or enter verified rules manually."
        out.append(_entry("planning_compliance", BLOCKED, ["Verified planning rules"], "Any compliance statement — compliance is reported as 'not assessed'.", step))
    elif not sfeats:
        out.append(_entry("planning_compliance", BLOCKED, ["Design geometry"], "Measurements to compare with the rules.", "Create the baseline scenario."))
    else:
        lim = [a["message"] for a in ambig]
        out.append(_entry("planning_compliance", LIMITED if lim or not has_boundary else READY, [] if has_boundary else ["Site boundary"],
                          "Ratio checks until the ambiguity is resolved." if lim else "", "Resolve the listed ambiguities." if lim else "Run the analysis.", lim))
    alts = [s for s in scenarios if s["kind"] == "alternative"]
    out.append(_entry("scenario_comparison", READY if baseline and alts else BLOCKED,
                      ([] if baseline else ["Baseline scenario"]) + ([] if alts else ["At least one alternative scenario"]),
                      "Comparing the design with an alternative.", "Create an alternative scenario and run the same analysis on both."))
    rates = db.select("rate_items", "project_id = ?", [pid])
    out.append(_entry("cost_estimate", READY if rates and alts else BLOCKED,
                      ([] if rates else ["Unit rates (with currency, date and source)"]) + ([] if alts else ["Alternative scenario defining the intervention quantities"]),
                      "A priced estimate; quantities can still be shown.", "Upload a unit-rate CSV or add rates manually."))
    out.append(_entry("population_context", NOT_IN_BUILD, [], "", "Deferred to Phase 2 (no population provider integrated)."))
    out.append(_entry("air_quality", NOT_IN_BUILD, [], "", "Deferred to Phase 2 (no air-quality provider integrated)."))
    blockers = []
    if not project.get("location_confirmed"):
        blockers.append("Confirm the site location on the map before running analyses.")
    return {"analyses": out, "global_blockers": blockers, "assessed_at": db.now(), "scenario_id": (scenario or {}).get("id")}
