"""Site and urban-form indicators from scenario geometry (FR-16).

All areas are computed in the project's local UTM zone. Overlapping features of
one class are unioned so nothing is double counted. Heat-relevant design
indicators (green share, hard-surface share) are reported as indicators, not as
predicted temperatures.
"""

from __future__ import annotations

import statistics

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from .. import geo
from .. import values as V
from .common import BLOCKED, COMPLETED, PARTIAL, Context, result

POLY = ("Polygon", "MultiPolygon")


def _features_local(ctx: Context) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in (ctx.scenario or {}).get("features", {}).get("features", []):
        ftype = f["properties"].get("feature_type", "unclassified")
        g = geo.to_local(shape(f["geometry"]), ctx.local_crs)
        if ftype == "tree" and g.geom_type == "Point":
            r = f["properties"].get("canopy_radius_m")
            if not r:
                out.setdefault("tree_point_no_canopy", []).append(g)
                continue
            g = g.buffer(float(r), quad_segs=12)
        out.setdefault(ftype, []).append((g, f["properties"]))
    return out


def compute(ctx: Context) -> dict:
    feats = _features_local(ctx)
    boundary = ctx.project.get("boundary")
    site_l = geo.to_local(shape(boundary), ctx.local_crs) if boundary else None
    m, warnings, limitations = [], [], []
    method_area = f"Shapely area in {ctx.local_crs} (local UTM), features unioned per class and clipped to the site boundary"
    dd = {"design_dependent": True}

    def area_of(ftype: str) -> float | None:
        geoms = [g for g, _ in feats.get(ftype, []) if g.geom_type in POLY]
        if not geoms:
            return 0.0
        u = unary_union(geoms)
        return (u.intersection(site_l) if site_l is not None else u).area

    buildings = [(g, p) for g, p in feats.get("building", []) if g.geom_type in POLY]
    if site_l is not None:
        site_area = site_l.area
        verified_area = ctx.project.get("verified_site_area_m2")
        denom = verified_area or site_area
        denom_label = "verified site area (planning document)" if verified_area else "site boundary area"
        m.append(V.metric("site_area_m2", "Total site area", round(site_area, 1), "m²", method=f"Area of the project site boundary in {ctx.local_crs}", **dd))
    else:
        denom = None
        for name, label in (("site_area_m2", "Total site area"), ("building_coverage_ratio_pct", "Building coverage ratio"), ("green_space_pct", "Green-space proportion"), ("open_space_pct", "Open-space proportion"), ("hard_surface_pct", "Mapped hard-surface proportion")):
            m.append(V.missing(name, label, "%" if name.endswith("pct") else "m²", V.BLOCKED, "No site boundary has been supplied, so site-relative areas cannot be calculated.", **dd))
    m.append(V.metric("building_count", "Number of buildings", len(buildings), "count", method="Count of building features in the scenario", **dd))
    if buildings:
        u = unary_union([g for g, _ in buildings])
        footprint = (u.intersection(site_l) if site_l is not None else u).area
        m.append(V.metric("building_footprint_m2", "Total building footprint area", round(footprint, 1), "m²", method=method_area, **dd))
        sizes = [g.area for g, _ in buildings]
        m.append(V.metric("building_footprint_median_m2", "Median building footprint", round(statistics.median(sizes), 1), "m²", method="Median of individual footprint areas", **dd))
        if len(buildings) > 1:
            nn = []
            for i, (g, _) in enumerate(buildings):
                d = min(g.distance(h) for j, (h, _) in enumerate(buildings) if j != i)
                nn.append(d)
            m.append(V.metric("building_spacing_median_m", "Median distance to nearest neighbouring building", round(statistics.median(nn), 1), "m", method="Median over buildings of the minimum edge-to-edge distance to another building", **dd))
    else:
        footprint = 0.0
        m.append(V.metric("building_footprint_m2", "Total building footprint area", 0.0, "m²", method="No building features in the scenario", **dd))
    if denom:
        m.append(V.metric("building_coverage_ratio_pct", "Building coverage ratio", round(footprint / denom * 100, 2), "%",
                          method=f"Total building footprint ÷ {denom_label} × 100", **dd))
        green_parts = [g for t in ("green_space", "tree") for g, _ in feats.get(t, []) if g.geom_type in POLY]
        green = unary_union(green_parts).intersection(site_l).area if green_parts else 0.0
        m.append(V.metric("green_space_m2", "Planned/mapped green-space area (incl. tree canopies with known size)", round(green, 1), "m²", method=method_area, **dd))
        m.append(V.metric("green_space_pct", "Green-space proportion", round(green / denom * 100, 2), "%", method=f"Green-space area ÷ {denom_label} × 100", **dd))
        road = area_of("road") or 0.0
        parking = area_of("parking") or 0.0
        road_lines = [g for g, _ in feats.get("road", []) if g.geom_type in ("LineString", "MultiLineString")]
        if road_lines:
            warnings.append(f"{len(road_lines)} road feature(s) are centre-lines without width; they are excluded from road area.")
        m.append(V.metric("road_area_m2", "Road and circulation area (polygons)", round(road, 1), "m²", method=method_area, **dd))
        m.append(V.metric("parking_area_m2", "Parking area", round(parking, 1), "m²", method=method_area, **dd))
        open_sp = area_of("open_space") or 0.0
        m.append(V.metric("open_space_pct", "Open-space proportion (designated open space + green space)", round((open_sp + green) / denom * 100, 2), "%",
                          method=f"(Designated open space + green space) ÷ {denom_label} × 100", **dd))
        hard = unary_union([g for t in ("road", "parking") for g, _ in feats.get(t, []) if g.geom_type in POLY] or [Point(0, 0).buffer(0)]).intersection(site_l).area
        m.append(V.metric("hard_surface_pct", "Mapped hard-surface proportion (roads + parking)", round(hard / denom * 100, 2), "%",
                          method=f"Union of road and parking polygons ÷ {denom_label} × 100. Plazas/courtyards with unknown surface material are not counted.", **dd))
        classified = unary_union([g for t, items in feats.items() if t not in ("tree_point_no_canopy", "site_boundary", "land_use_zone") for g, _ in items if hasattr(g, "geom_type") and g.geom_type in POLY] or [Point(0, 0).buffer(0)])
        unclassified = max(site_l.area - classified.intersection(site_l).area, 0.0)
        m.append(V.metric("unassigned_site_area_pct", "Site area with no design feature assigned", round(unclassified / site_l.area * 100, 2), "%",
                          method="Site area not covered by any classified polygon feature", **dd))
        outside = sum(g.difference(site_l).area for g, _ in buildings)
        if outside > 1:
            warnings.append(f"{outside:.0f} m² of building footprint lies outside the site boundary and is excluded from site ratios.")
    heights = [p.get("height_m") for _, p in buildings]
    floors = [p.get("floors") for _, p in buildings]
    known_h = [h for h in heights if h]
    known_f = [f for f in floors if f]
    if buildings and known_h:
        state_note = "" if len(known_h) == len(buildings) else f" ({len(buildings) - len(known_h)} of {len(buildings)} buildings have no height)"
        if len(known_h) == len(buildings):
            m.append(V.metric("max_building_height_m", "Maximum building height", max(known_h), "m", output_kind=V.USER_PROVIDED, method="Maximum of supplied building heights", **dd))
        else:
            m.append(V.missing("max_building_height_m", "Maximum building height", "m", V.UNKNOWN, "Heights are missing for some buildings" + state_note, **dd))
    elif buildings:
        m.append(V.missing("max_building_height_m", "Maximum building height", "m", V.UNKNOWN, "No building heights were supplied. Heights are never inferred from footprints.", **dd))
    if buildings and len(known_f) == len(buildings):
        m.append(V.metric("max_building_floors", "Maximum number of floors", max(known_f), "floors", output_kind=V.USER_PROVIDED, method="Maximum of supplied floor counts", **dd))
        if denom:
            gfa = sum(g.intersection(site_l).area * p["floors"] for g, p in buildings)
            m.append(V.metric("floor_area_ratio", "Floor-area ratio (approximate)", round(gfa / denom, 3), "ratio",
                              method=f"Σ(footprint × floors) ÷ {denom_label}. Assumes every floor equals the footprint.", **dd))
    elif buildings:
        reason = "Floor counts are missing for some or all buildings, so floor-area ratio and floor limits cannot be calculated."
        m.append(V.missing("max_building_floors", "Maximum number of floors", "floors", V.UNKNOWN, reason, **dd))
        m.append(V.missing("floor_area_ratio", "Floor-area ratio", "ratio", V.UNKNOWN, reason, **dd))
    if site_l is not None and buildings:
        edge = site_l.boundary
        setbacks = [g.distance(edge) if site_l.contains(g) else 0.0 for g, _ in buildings]
        m.append(V.metric("min_building_setback_m", "Minimum distance from a building to the site boundary", round(min(setbacks), 2), "m",
                          method="Minimum edge distance from each building to the site boundary line (0 if a building crosses it)", **dd))
    zones = [(g, p) for g, p in feats.get("land_use_zone", []) if g.geom_type in POLY]
    if zones and site_l is not None:
        by_use: dict[str, float] = {}
        for g, p in zones:
            use = str(p.get("land_use") or p.get("name") or "unspecified")
            by_use[use] = by_use.get(use, 0.0) + g.intersection(site_l).area
        total = sum(by_use.values())
        m.append(V.metric("land_use_zone_count", "Land-use zones in the design", len(zones), "count", method="Count of land-use zone features",
                          extra={"distribution_m2": {k: round(v, 1) for k, v in by_use.items()},
                                 "distribution_pct_of_site": {k: round(v / site_l.area * 100, 2) for k, v in by_use.items()},
                                 "overlap_note": "Zones are summed per category; overlapping zones would be double counted." if total > site_l.area + 1 else None}, **dd))
    if feats.get("tree_point_no_canopy"):
        warnings.append(f"{len(feats['tree_point_no_canopy'])} tree point(s) have no canopy size and contribute no green area.")
    unclassified_count = len(feats.get("unclassified", []))
    if unclassified_count:
        warnings.append(f"{unclassified_count} feature(s) are unclassified and are not counted in any class.")
    limitations.append("Indicators describe the proposed geometry as supplied. They do not predict temperatures or comfort.")
    return {"metrics": m, "warnings": warnings, "limitations": limitations, "has_boundary": site_l is not None, "buildings": len(buildings)}


def run(ctx: Context) -> dict:
    if not ctx.scenario or not ctx.scenario.get("features", {}).get("features"):
        return result(BLOCKED, reason="No design geometry. Upload an urban plan (GeoJSON or DXF) and create the baseline scenario.")
    out = compute(ctx)
    ctx.shared["urban_form"] = {mm["metric_name"]: mm for mm in out["metrics"]}
    status = COMPLETED if out["has_boundary"] and not out["warnings"] else PARTIAL
    return result(status, metrics=out["metrics"], warnings=out["warnings"], limitations=out["limitations"],
                  details={"scenario_id": ctx.scenario["id"], "scenario_name": ctx.scenario["name"]})
