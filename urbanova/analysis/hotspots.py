"""Design hotspots: where the problems are on the drawing, how big and how severe (A8, PHASE2_SPEC §4).

* H1 sun-exposed hard surface: exact vector overlay of the hard surface with each hour's shadow polygons
  (07–17 h solar, from the shade analysis), counting per piece the hours in sun at strong and very strong
  UTCI heat stress (heat-stress analysis).
* H3 shade deficit: open and green space farther than D from any tree canopy or shade structure and in
  sun at 15:00, when the 15:00 UTCI in sun is very strong or worse.
* H4 planning violation location: the part of the design that breaks a verified rule (building parts in
  the setback band, buildings above a height or floor limit); site-wide ratios are attached to the site.
Hotspots overlapping by more than half of the smaller one are merged. Size, intensity and severity follow
the §4.2 tables; causes come only from the fixed deterministic rules of §4.3, using design data.
Site context (the H2 hot cluster, regional climate) is never a hotspot.
"""

from __future__ import annotations

import math

from shapely.affinity import translate
from shapely.geometry import mapping
from shapely.ops import unary_union

from .. import basis, geo
from .. import values as V
from ..findings import PROVISIONAL_SEVERITY_CAP as CAP
from ..findings import SEVERITY_ORDER as LEVEL
from .common import BLOCKED, COMPLETED, PARTIAL, Context, layer, result, save_geojson
from .heat_stress import VERY_STRONG
from .solar import sunlit
from .urban_form import design_surfaces

SEVERITY = {"high": {"small": "moderate", "medium": "high", "large": "high"},
            "moderate": {"small": "low", "medium": "moderate", "large": "high"},
            "low": {"small": "low", "medium": "low", "large": "moderate"}}
SEVERITY_COLORS = {"high": "#b2182b", "moderate": "#ef8a62", "low": "#e0a96d"}  # also used by the report map and web/app.js
TYPE_LABELS = {"H1": "Sun-exposed hard surface", "H3": "Shade deficit", "H4": "Planning violation"}
NUMERIC_TOL_M2 = 1e-6  # pieces smaller than this are floating-point residue of polygon overlay
H1_H3_BASIS = ["hotspots.size_classes", "hotspots.intensity_hours", "climate.hot_day_definition", "heat_stress.f_svv", "heat_stress.ground_reflectance"]
H3_BASIS = H1_H3_BASIS + ["hotspots.deficit_distance_m"]
H4_BASIS = ["hotspots.size_classes", "hotspots.h4_bands"]


def cfg(name: str):
    return basis.get(f"hotspots.{name}")["value"]


# ---------------------------------------------------------------- exposure overlay (H1, H3)
def exposure_pieces(geom, shade_by_hour: dict, positions: dict, vs_hours: set[int], s_hours: set[int]) -> list[dict]:
    """Split `geom` exactly by every hour's shadow; each piece carries its hour counts in sun.

    n_vs / n_s: hours in sun with UTCI in sun ≥ 38 / ≥ 32 °C; sun_h: hours in sun; wsun: Σ sin(solar elevation)
    over its sun hours (elevation-weighted sun-hours); sun_hours: the hours it is in sun."""
    pieces = [{"geom": geom, "n_vs": 0, "n_s": 0, "sun_h": 0, "wsun": 0.0, "sun_hours": []}]
    for h, sh in sorted(shade_by_hour.items()):
        if sh is None:
            continue  # sun at or below the horizon
        nxt = []
        for p in pieces:
            lit = sunlit(p["geom"], sh)
            dark = p["geom"].intersection(sh)
            if lit.area > NUMERIC_TOL_M2:
                nxt.append({"geom": lit, "n_vs": p["n_vs"] + (h in vs_hours), "n_s": p["n_s"] + (h in s_hours), "sun_h": p["sun_h"] + 1,
                            "wsun": p["wsun"] + math.sin(math.radians(positions[h][0])), "sun_hours": p["sun_hours"] + [h]})
            if dark.area > NUMERIC_TOL_M2:
                nxt.append({**p, "geom": dark})
        pieces = nxt
    return pieces


def intensity_of(n_vs: float, n_s: float) -> str:
    """§4.2: high = very strong or extreme heat stress for ≥ 4 h; moderate = at least strong for ≥ 4 h or very strong for 1–3 h."""
    hrs = cfg("intensity_hours")
    if n_vs >= hrs["high_very_strong_h"]:
        return "high"
    if n_s >= hrs["moderate_strong_h"] or n_vs >= 1:
        return "moderate"
    return "low"


def components(pieces: list[dict], levels: set[str]) -> list[tuple[object, str]]:
    """Connected polygons of the pieces whose intensity is in `levels`, one per intensity, slivers dropped."""
    out = []
    for lvl in ("high", "moderate", "low"):
        if lvl not in levels:
            continue
        u = unary_union([p["geom"] for p in pieces if intensity_of(p["n_vs"], p["n_s"]) == lvl])
        if u.is_empty:
            continue
        for g in (u.geoms if hasattr(u, "geoms") else [u]):
            if g.geom_type == "Polygon" and g.area >= cfg("min_piece_m2"):
                out.append((g, lvl))
    return out


def exposure_stats(geom, sol: dict, hs: dict, surf_hard, shade_sources) -> dict:
    """Area-weighted hour counts and UTCI range over the sun hours of the hotspot."""
    pieces = exposure_pieces(geom, sol["shade_by_hour"], sol["positions"], set(hs["very_strong_hours"]), set(hs["strong_hours"]))
    total = sum(p["geom"].area for p in pieces) or 1.0

    def wmean(k):
        return sum(p[k] * p["geom"].area for p in pieces) / total

    sun_hours = sorted({h for p in pieces for h in p["sun_hours"]})
    prof = hs["profile"]
    sun_utci = [prof[h]["utci_sun_c"] for h in sun_hours if prof[h]["utci_sun_c"] is not None]
    shade_utci = [prof[h]["utci_shade_c"] for h in sun_hours if prof[h]["utci_shade_c"] is not None]
    lit15 = sunlit(geom, sol["shade_by_hour"].get(cfg("cause_thresholds")["afternoon_hour"])).area / geom.area
    return {"hard_share": surf_hard.intersection(geom).area / geom.area,
            "canopy_m": None if shade_sources.is_empty else geom.distance(shade_sources), "n_vs": wmean("n_vs"), "n_s": wmean("n_s"), "sun_h": wmean("sun_h"), "wsun": wmean("wsun"), "max_n_vs": max((p["n_vs"] for p in pieces), default=0),
            "utci_sun_peak": max(sun_utci) if sun_utci else None, "utci_shade_peak": max(shade_utci) if shade_utci else None, "lit15": lit15}


def shade_deficit(surf: dict, sol: dict, within=None):
    """H3 area: open and green space (optionally within a polygon) farther than D from any canopy or shade structure,
    in sun at the afternoon hour. One definition for detection and for measuring options."""
    far = surf["open"] if within is None else surf["open"].intersection(within)
    if not surf["shade_sources"].is_empty:
        far = far.difference(surf["shade_sources"].buffer(cfg("deficit_distance_m")))
    return sunlit(far, sol["shade_by_hour"].get(cfg("cause_thresholds")["afternoon_hour"]))


def setback_band(site, limit: float):
    """The strip of the site within `limit` metres of its boundary."""
    return site.difference(site.buffer(-limit))


# ---------------------------------------------------------------- H4
def h4_candidates(checks: list[dict], surf: dict, site_area: float) -> list[dict]:
    """Located or site-wide planning-violation hotspots from failed verified-rule checks."""
    out = []
    for c in checks:
        if c.get("status") != "fails":
            continue
        limit, measured, rtype = c["limit"], c["measured"], c["rule_type"]
        rel = abs(limit - measured) / abs(limit) if limit else 1.0
        geom, area, note = None, None, None
        if rtype == "min_setback_m":
            band = setback_band(surf["site"], limit)
            geom = surf["buildings"].intersection(band)
            note = f"Building parts within {limit:g} m of the site boundary"
        elif rtype in ("max_height_m", "max_floors"):
            key = "height_m" if rtype == "max_height_m" else "floors"
            over = [g for g, p in surf["building_props"] if p.get(key) and float(p[key]) > limit]
            geom = unary_union(over) if over else None
            note = f"Buildings above the verified {c['label'].lower()}"
        elif rtype in ("max_building_coverage_pct", "min_green_space_pct", "min_open_space_pct"):
            area = abs(measured - limit) / 100 * site_area
            note = ("Building footprint above the verified maximum" if rtype.startswith("max") else "Shortfall against the verified minimum") + " (site-wide)"
        elif rtype == "max_far":
            area = abs(measured - limit) * site_area
            note = "Floor area above the verified maximum ratio (site-wide)"
        if geom is not None and (geom.is_empty or geom.area < cfg("min_piece_m2")):
            geom = None
        if geom is None and area is None:
            continue
        out.append({"types": ["H4"], "geom": geom, "site_wide": geom is None, "area": geom.area if geom is not None else area,
                    "intensity": "high" if rel * 100 >= cfg("h4_bands")["high_pct"] else "moderate" if rel * 100 >= cfg("h4_bands")["moderate_pct"] else "low",
                    "rule": {"rule_id": c["rule_id"], "rule_type": rtype, "label": c["label"], "limit": limit, "measured": measured, "unit": c.get("unit"),
                             "relative_shortfall_pct": round(rel * 100, 1), "metric": c["metric"], "note": note}})
    return out


# ---------------------------------------------------------------- merging, size, severity
def merge(cands: list[dict]) -> list[dict]:
    """Merge located hotspots whose overlap exceeds `merge_overlap` of the smaller one; repeat until stable."""
    located = [c for c in cands if c["geom"] is not None]
    rest = [c for c in cands if c["geom"] is None]
    thr = cfg("merge_overlap")
    changed = True
    while changed:
        changed = False
        for i in range(len(located)):
            for j in range(i + 1, len(located)):
                a, b = located[i], located[j]
                small = min(a["geom"].area, b["geom"].area)
                if small > 0 and a["geom"].intersection(b["geom"]).area / small > thr:
                    g = unary_union([a["geom"], b["geom"]])
                    located[i] = {**a, "geom": g, "area": g.area, "types": sorted(set(a["types"]) | set(b["types"])),
                                  "intensity": max(a["intensity"], b["intensity"], key=LEVEL.get), "rule": a.get("rule") or b.get("rule"),
                                  "merged_from": a.get("merged_from", 1) + b.get("merged_from", 1)}
                    del located[j]
                    changed = True
                    break
            if changed:
                break
    return located + rest


def size_class(area: float, share_pct: float) -> str:
    s = cfg("size_classes")
    if area < s["small_below_m2"] or share_pct < s["small_below_pct"]:
        return "small"
    if area >= s["large_from_m2"] or share_pct >= s["large_from_pct"]:
        return "large"
    return "medium"


# ---------------------------------------------------------------- causes (§4.3, design data only)
def _axis_deg(g) -> tuple[float, float]:
    """Long-axis bearing (0 = north, 90 = east, folded to 0–180) and short side of the minimum rotated rectangle."""
    pts = list(g.minimum_rotated_rectangle.exterior.coords)
    sides = [((pts[k + 1][0] - pts[k][0]), (pts[k + 1][1] - pts[k][1])) for k in range(2)]
    lengths = [math.hypot(dx, dy) for dx, dy in sides]
    dx, dy = sides[0] if lengths[0] >= lengths[1] else sides[1]
    return math.degrees(math.atan2(dx, dy)) % 180, min(lengths)


def causes(hs: dict, surf: dict, stats: dict | None, sol: dict | None) -> list[dict]:
    t = cfg("cause_thresholds")
    out = []
    g = hs["geom"]
    if "H4" in hs["types"] and hs.get("rule"):
        r = hs["rule"]
        out.append({"code": "EXCEEDS_RULE", "evidence": {"limit": r["limit"], "measured": r["measured"], "relative_shortfall_pct": r["relative_shortfall_pct"]},
                    "evidence_metrics": [r["metric"]], "text": f"{r['note']}: measured {r['measured']:g} vs verified limit {r['limit']:g} {r['unit'] or ''}".strip()})
    if g is None or stats is None:
        return out
    hard_share = stats["hard_share"]
    if hard_share >= t["large_paved_share"]:
        out.append({"code": "LARGE_PAVED_AREA", "evidence": {"hard_share": round(hard_share, 3)}, "evidence_metrics": ["hard_surface_pct", "heat_stress_hard_surface_very_strong_m2h"],
                    "text": f"{hard_share:.0%} of the area is hard surface"})
    if stats["lit15"] >= t["afternoon_sunlit_share"]:
        out.append({"code": "NO_AFTERNOON_SHADE", "evidence": {"sunlit_share_1500": round(stats["lit15"], 3)}, "evidence_metrics": ["solar_site_ground_exposed_pct_1500"],
                    "text": f"{stats['lit15']:.0%} of the area is in sun at {t['afternoon_hour']}:00 solar time"})
    d = stats["canopy_m"]
    if d is None or d > cfg("deficit_distance_m"):
        out.append({"code": "NO_TREES_NEARBY", "evidence": {"nearest_canopy_m": None if d is None else round(d, 1), "distance_limit_m": cfg("deficit_distance_m")},
                    "evidence_metrics": ["green_space_m2"],
                    "text": "No tree canopy or shade structure in the design" if d is None else f"Nearest canopy or shade structure is {d:.0f} m away"})
    sh15 = sol["shade_by_hour"].get(t["afternoon_hour"]) if sol else None
    for bg, bp in surf["building_props"]:
        west = translate(bg, -t["west_edge_m"], 0).difference(bg)  # ground within west_edge_m west of the façade
        strip = west.intersection(g)
        if strip.area <= 0:
            continue
        lit = sunlit(strip, sh15).area
        if lit / strip.area >= t["afternoon_sunlit_share"]:
            out.append({"code": "EXPOSED_WEST_EDGE", "evidence": {"building": bp.get("name"), "sunlit_share_1500": round(lit / strip.area, 3)},
                        "evidence_metrics": ["solar_site_ground_exposed_pct_1500"], "text": f"Sunlit edge along the west façade of {bp.get('name') or 'a building'} in the afternoon"})
            break
    heights = [(bg, float(bp["height_m"])) for bg, bp in surf["building_props"] if bp.get("height_m")]
    for rg, rp in surf["roads"]:
        if rg.intersection(g).area <= 0:
            continue
        axis, width = _axis_deg(rg)
        near = [h for bg, h in heights if bg.distance(rg) <= t["street_building_search_m"]]
        hw = (sum(near) / len(near)) / width if near and width > 0 else 0.0
        if abs(axis - 90) <= t["ew_axis_tolerance_deg"] and hw < t["max_h_w_ratio"]:
            out.append({"code": "ORIENTATION_EXPOSES_STREETS", "evidence": {"road": rp.get("name"), "axis_deg": round(axis, 1), "height_to_width": round(hw, 2)},
                        "evidence_metrics": ["road_area_m2", "max_building_height_m"],
                        "text": f"{rp.get('name') or 'A street'} runs within {t['ew_axis_tolerance_deg']}° of east–west with height-to-width {hw:.2f}"})
            break
    return out


# ---------------------------------------------------------------- module
def _supporting(hs: dict, stats: dict | None, surf: dict, site_area: float) -> list[dict]:
    g = hs["geom"]
    out = [{"name": "area_m2", "label": "Area", "value": round(hs["area"], 1), "unit": "m²", "source_metrics": ["site_area_m2"]}]
    if stats:
        out += [
            {"name": "sun_hours_hot_day", "label": "Hours in sun (07–17 solar, area-weighted)", "value": round(stats["sun_h"], 1), "unit": "h", "source_metrics": ["solar_open_space_shade_hours"]},
            {"name": "elevation_weighted_sun_hours", "label": "Sun-hours weighted by solar elevation (Σ sin elevation)", "value": round(stats["wsun"], 2), "unit": "h", "source_metrics": ["solar_open_space_shade_hours"]},
            {"name": "very_strong_sun_hours", "label": "Hours in sun at very strong or extreme heat stress (area-weighted)", "value": round(stats["n_vs"], 1), "unit": "h", "source_metrics": ["utci_sun_very_strong_hours"]},
            {"name": "strong_sun_hours", "label": "Hours in sun at strong heat stress or worse (area-weighted)", "value": round(stats["n_s"], 1), "unit": "h", "source_metrics": ["utci_sun_strong_hours"]},
        ]
        if stats["utci_sun_peak"] is not None:
            out.append({"name": "utci_sun_peak_c", "label": "Highest UTCI in sun during its sun hours", "value": round(stats["utci_sun_peak"], 1), "unit": "°C", "source_metrics": ["utci_sun_peak_c"]})
        if stats["utci_shade_peak"] is not None:
            out.append({"name": "utci_shade_peak_c", "label": "UTCI in shade at the same hours (what shade would give)", "value": round(stats["utci_shade_peak"], 1), "unit": "°C", "source_metrics": ["utci_shade_peak_c"]})
        hard = surf["hard"].intersection(g).area
        out.append({"name": "hard_surface_share_pct", "label": "Hard-surface share", "value": round(100 * hard / g.area, 1), "unit": "%", "source_metrics": ["hard_surface_pct"]})
        comp: dict[str, float] = {}
        for pg, t, _ in surf["hard_parts"]:
            comp[t] = comp.get(t, 0.0) + pg.intersection(g).area
        if hard > 0:
            out.append({"name": "composition_pct", "label": "Hard surface by feature type", "value": {k: round(100 * v / hard, 1) for k, v in comp.items() if v > 0}, "unit": "%",
                        "source_metrics": ["road_area_m2", "parking_area_m2"]})
        d = stats["canopy_m"]
        out.append({"name": "nearest_canopy_m", "label": "Distance to the nearest tree canopy or shade structure", "value": None if d is None else round(d, 1), "unit": "m",
                    "source_metrics": ["green_space_m2"], "note": "No canopy or shade structure in the design" if d is None else None})
    if hs.get("rule"):
        out.append({"name": "relative_shortfall_pct", "label": "Shortfall relative to the verified limit", "value": hs["rule"]["relative_shortfall_pct"], "unit": "%",
                    "source_metrics": [hs["rule"]["metric"]]})
    return out


def run(ctx: Context) -> dict:
    surf = design_surfaces(ctx)
    if not surf:
        return result(BLOCKED, reason="Hotspots need a site boundary and design geometry.")
    sol, hs_data = ctx.shared.get("solar"), ctx.shared.get("heat_stress")
    local = ctx.local_crs
    site_area = ctx.project.get("verified_site_area_m2") or surf["site"].area
    warnings, notes = [], []
    cands: list[dict] = []
    if sol and hs_data:
        vs, s = set(hs_data["very_strong_hours"]), set(hs_data["strong_hours"])
        if not surf["hard"].is_empty:
            for g, lvl in components(exposure_pieces(surf["hard"], sol["shade_by_hour"], sol["positions"], vs, s), {"high", "moderate"}):
                cands.append({"types": ["H1"], "geom": g, "area": g.area, "intensity": lvl, "site_wide": False})
        hour = cfg("cause_thresholds")["afternoon_hour"]
        u15 = (hs_data["profile"].get(hour) or {}).get("utci_sun_c")
        sh15 = sol["shade_by_hour"].get(hour)
        if surf["open"].is_empty:
            notes.append("H3 not assessed: no open or green space in the design.")
        elif u15 is None or u15 < VERY_STRONG or sh15 is None:
            notes.append(f"H3 not produced: UTCI in sun at {hour}:00 is {'unknown' if u15 is None else f'{u15:.1f} °C'}, below very strong heat stress ({VERY_STRONG:g} °C).")
        else:
            lit = shade_deficit(surf, sol)
            if lit.area > 0:
                for g, lvl in components(exposure_pieces(lit, sol["shade_by_hour"], sol["positions"], vs, s), {"high", "moderate"}):
                    cands.append({"types": ["H3"], "geom": g, "area": g.area, "intensity": lvl, "site_wide": False})
    else:
        notes.append("H1 and H3 not produced: " + ("the heat-stress analysis (UTCI) is unavailable." if sol else "there are no hourly shadows (buildings need heights)."))
    cands += h4_candidates(ctx.shared.get("compliance_checks") or [], surf, site_area)
    merged = merge(cands)
    out = []
    for h in merged:
        stats = exposure_stats(h["geom"], sol, hs_data, surf["hard"], surf["shade_sources"]) if h["geom"] is not None and {"H1", "H3"} & set(h["types"]) and sol and hs_data else None
        share = 100 * h["area"] / site_area
        size = size_class(h["area"], share)
        sev = SEVERITY[h["intensity"]][size]
        ids = H4_BASIS if h["types"] == ["H4"] else (H3_BASIS if "H3" in h["types"] else H1_H3_BASIS)
        provisional = [i for i in ids if basis.is_provisional(i)]
        capped = bool(provisional) and LEVEL[sev] > LEVEL[CAP]
        shown = CAP if capped else sev
        if h["types"] == ["H4"]:
            why = f"shortfall {h['rule']['relative_shortfall_pct']:g} % of the verified limit"
        else:
            why = (f"very strong or extreme heat stress in sun for up to {stats['max_n_vs']} h, {stats['n_vs']:.1f} h area-weighted (UTCI, R-26)"
                   + (f"; merged from {h['merged_from']} hotspots, keeping the higher intensity" if h.get("merged_from", 1) > 1 else "")) if stats else "heat stress in sun"
        cs = causes(h, surf, stats, sol)
        out.append({
            "types": h["types"], "type_labels": [TYPE_LABELS[t] for t in h["types"]], "condition": V.DESIGN, "site_wide": h["site_wide"],
            "geometry": mapping(geo.to_wgs84(h["geom"], local)) if h["geom"] is not None else None,
            "area_m2": round(h["area"], 1), "site_share_pct": round(share, 2), "size_class": size, "intensity": h["intensity"],
            "severity": shown, "severity_uncapped": sev, "severity_capped": capped, "provisional_basis": provisional,
            "severity_basis": f"§4.2 table: intensity {h['intensity']} ({why}) × size {size} → {sev}" + (f"; capped at {CAP} while {', '.join(provisional)} are not approved" if capped else ""),
            "supporting_metrics": _supporting(h, stats, surf, site_area), "likely_causes": cs, "rule": h.get("rule"), "merged_from": h.get("merged_from", 1),
            "_geom": h["geom"],  # local CRS, for the interventions module; removed below
            "confidence": "limited" if (sol or {}).get("buildings_without_height") else "adequate",
            "confidence_reason": ("Some buildings have no height, so their shade is missing." if (sol or {}).get("buildings_without_height")
                                  else "Design geometry and computed shadows; no satellite dependency."),
        })
    out.sort(key=lambda r: (-LEVEL[r["severity"]], -r["area_m2"]))
    for n, r in enumerate(out, start=1):
        r["number"] = n
    # Hotspots with their local geometry, in priority order, for the interventions module (A10).
    ctx.shared["hotspots"] = [{"record": r, "geom": r.pop("_geom")} for r in out]
    count_label, area_label = "Design hotspots (H1, H3, H4 after merging)", "Total area of located design hotspots"
    if sol and hs_data:
        m = [V.metric("hotspot_count", count_label, len(out), "count", method="Count of design hotspots after merging (§4)"),
             V.metric("hotspot_total_area_m2", area_label, round(sum(r["area_m2"] for r in out if not r["site_wide"]), 1), "m²",
                      method="Sum of hotspot polygon areas (merged hotspots do not overlap by more than half; site-wide shortfalls excluded)")]
    else:  # heat hotspots were not assessed: a count here would read as "none found"
        why = notes[-1]
        m = [V.missing("hotspot_count", count_label, "count", V.UNKNOWN, why), V.missing("hotspot_total_area_m2", area_label, "m²", V.UNKNOWN, why)]
    layers = []
    feats = [{"type": "Feature", "geometry": r["geometry"], "properties": {"number": r["number"], "severity": r["severity"], "types": r["types"], "area_m2": r["area_m2"]}}
             for r in out if r["geometry"]]
    if feats:
        path = save_geojson(ctx.out_dir / "hotspots.geojson", {"type": "FeatureCollection", "features": feats})
        layers.append(layer("Design hotspots (numbered)", "vector", V.CALCULATED, path,
                            legend={"type": "categorical", "classes": [{"color": c, "label": f"{k} severity"} for k, c in SEVERITY_COLORS.items()]}))
    if (sol or {}).get("buildings_without_height"):
        warnings.append("Some buildings have no height; their shade is missing, so sun exposure and hotspots are overstated.")
    limitations = ["Hotspots locate design problems on the drawing with exact polygons; heat stress uses the composite hot day and assumes surfaces at air temperature.",
                   "Open space is assumed paved unless its `surface` attribute says otherwise (grass, soil, planted, ...).",
                   "Site context (today's surface hot cluster, regional climate) is reported separately and is never a hotspot or a cause."]
    res = result(PARTIAL if warnings or not (sol and hs_data) else COMPLETED, metrics=m, layers=layers, warnings=warnings, limitations=limitations,
                 details={"notes": notes})
    res["hotspots"] = out
    return res
