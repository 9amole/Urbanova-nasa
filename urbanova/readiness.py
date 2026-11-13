"""Input readiness assessment (FR-05). Each analysis is evaluated independently."""

from __future__ import annotations

from . import basis, db, documents, plans
from .providers.base import availability
from .settings import load_config, settings

READY, LIMITED, BLOCKED, NOT_IN_BUILD = "ready", "ready_with_limitations", "blocked", "not_in_this_build"

ANALYSES = {
    "climate_baseline": "Regional climate & heat baseline (air temperature)",
    "climate_trends": "Long-term climate trends since 1981 (NASA POWER)",
    "site_context": "Surrounding land context (OpenStreetMap)",
    "land_cover": "Land cover around the site (ESA WorldCover)",
    "urban_form": "Site & urban-form indicators (coverage, green space)",
    "surface_heat": "Land surface temperature (Landsat) — heat analysis",
    "vegetation": "Vegetation indicator (Sentinel-2 NDVI)",
    "solar_shade": "Solar position & geometric shade",
    "heat_stress": "Heat stress in sun and shade (UTCI, hot day)",
    "hotspots": "Design hotspots (where, how big, how severe)",
    "interventions": "Intervention options (measured, with costs)",
    "planning_compliance": "Planning-rule checks",
    "scenario_comparison": "Alternative-design comparison",
    "cost_estimate": "Indicative cost estimate",
    "population_context": "Population context",
    "air_quality": "Regional air-quality context",
}
RUNNABLE = ["climate_baseline", "climate_trends", "site_context", "land_cover", "urban_form", "vegetation", "surface_heat", "solar_shade", "heat_stress", "planning_compliance", "hotspots", "interventions"]
# Existing land or proposed design, per runnable module (urbanova/config/analyses.json, decision S-8).
CONDITION = {a["id"]: a["condition"] for a in load_config("analyses.json")["analyses"] if a["id"] in RUNNABLE}


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
        return [r for _, _, r in reasons], "A supervisor must approve a data source for this analysis (urbanova/config/providers.json)."

    climate_block, climate_step = source_block(["open_meteo_archive", "nasa_power_daily"])
    out.append(_entry("climate_baseline", BLOCKED if climate_block else READY, climate_block, "Regional climate statistics.",
                      climate_step or "Run the analysis.", ["Regional gridded data (~25 km); not site microclimate."]))
    trend_block, trend_step = source_block(["nasa_power_monthly"])
    out.append(_entry("climate_trends", BLOCKED if trend_block else READY, trend_block, "Long-term climate trends (rate per decade and significance).",
                      trend_step or "Run the analysis.", ["Regional gridded data (~55 km NASA POWER cell); a trend describes the past, not a forecast."]))
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
    lc_block, lc_step = source_block(["pc_esa_worldcover"])
    out.append(_entry("land_cover", BLOCKED if lc_block else (READY if has_boundary else LIMITED), lc_block + ([] if has_boundary else ["Site boundary"]),
                      "Land-cover shares around the site (context)." if lc_block else ("Site-specific land-cover shares." if not has_boundary else ""),
                      lc_step or "Run the analysis.", pt_limit + ["ESA WorldCover 2021 snapshot; site context only."]))
    veg_block, veg_step = source_block(["pc_sentinel2_l2a"])
    out.append(_entry("vegetation", BLOCKED if veg_block else (READY if has_boundary else LIMITED), veg_block + ([] if has_boundary else ["Site boundary"]),
                      "Observed vegetation indicator." if veg_block else ("Site-specific vegetation statistics." if not has_boundary else ""), veg_step or "Run the analysis.", pt_limit))
    if not sfeats:
        out.append(_entry("solar_shade", READY, [], "", "Run the analysis (site analysis: sun position; shade from a design needs a plan).",
                          ["Flat terrain; off-site obstructions not modelled."]))
    elif not buildings:
        out.append(_entry("solar_shade", LIMITED, ["Building footprints with heights"], "Building-cast shade; only sun position is calculated.",
                          "Upload plan geometry and add building heights."))
    elif len(with_h) < len(buildings):
        out.append(_entry("solar_shade", LIMITED, [f"Heights for {len(buildings) - len(with_h)} building(s)"],
                          "Shade from buildings without heights (shade is underestimated).", "Add the missing building heights on the Inputs screen."))
    else:
        out.append(_entry("solar_shade", READY, [], "", "Run the analysis.", ["Flat terrain; off-site obstructions not modelled."]))
    # Hourly shadows come from buildings with heights, sized trees and shade structures with heights (solar.run).
    casters = with_h or [f for f in sfeats if f["properties"].get("height_m") and (f["properties"].get("feature_type") == "shade_structure"
                         or (f["properties"].get("feature_type") == "tree" and f["properties"].get("canopy_radius_m")))]
    # Without a design (site analysis) heat stress is assessed in open sun and shade, so no shadows are needed.
    hs_missing = ([] if casters or not sfeats else ["Building, tree or shade-structure heights (for hourly shadows)"]) + climate_block
    out.append(_entry("heat_stress", BLOCKED if hs_missing else READY, hs_missing, "UTCI heat stress in sun and shade on the hot day." if hs_missing else "",
                      climate_step or ("Add building heights." if not casters else "Run the analysis."),
                      ["Needs ERA5 hourly data (Open-Meteo); unavailable with the fallback climate provider.", "Surfaces assumed at air temperature."]))
    hot_missing = ([] if sfeats else ["Design geometry"]) + ([] if has_boundary else ["Site boundary"])
    out.append(_entry("hotspots", BLOCKED if hot_missing else (READY if casters and not climate_block else LIMITED), hot_missing,
                      "Located design hotspots." if hot_missing else ("Heat hotspots (H1, H3) need heights for shadows and the hot-day weather." if not casters or climate_block else ""),
                      "Upload the design and boundary." if hot_missing else "Run the analysis.",
                      ["Planning-violation hotspots (H4) need verified rules."]))
    rates = db.select("rate_items", "project_id = ?", [pid])
    out.append(_entry("interventions", BLOCKED if hot_missing else (READY if rates else LIMITED), hot_missing,
                      "Measured intervention options per hotspot." if hot_missing else ("Option costs (options are still measured and ranked per unit of quantity)." if not rates else ""),
                      "Upload the design and boundary." if hot_missing else ("Upload a unit-rate CSV to price the options (optional)." if not rates else "Run the analysis."),
                      [f"Only the {basis.get('interventions.max_hotspots')['value']} highest-priority hotspots are optimised.", "Irrigation water per tree or m² is 'value required' until a sourced value is recorded."]))
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
    out.append(_entry("cost_estimate", READY if rates and alts else BLOCKED,
                      ([] if rates else ["Unit rates (with currency, date and source)"]) + ([] if alts else ["Alternative scenario defining the intervention quantities"]),
                      "A priced estimate; quantities can still be shown.", "Upload a unit-rate CSV or add rates manually."))
    out.append(_entry("population_context", NOT_IN_BUILD, [], "", "Deferred to Phase 2 (no population provider integrated)."))
    out.append(_entry("air_quality", NOT_IN_BUILD, [], "", "Deferred to Phase 2 (no air-quality provider integrated)."))
    blockers = []
    if not project.get("location_confirmed"):
        blockers.append("Confirm the site location on the map before running analyses.")
    return {"analyses": out, "global_blockers": blockers, "assessed_at": db.now(), "scenario_id": (scenario or {}).get("id")}
