"""Solar position and geometric shade analysis (FR-19).

Solar position: NOAA General Solar Position algorithm (Spencer series), evaluated
at TRUE SOLAR TIME so no time-zone assumption is needed. Shade: exact shadow of
vertical prisms (building footprint × height) on flat ground; flat shade
structures and tree canopies (when height and canopy size are supplied) cast a
translated footprint. No terrain, no off-site obstructions, no transmissivity.
"""

from __future__ import annotations

import math
from datetime import date

from shapely.affinity import translate
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

from .. import geo
from .. import values as V
from .common import BLOCKED, COMPLETED, PARTIAL, Context, layer, result, save_geojson

REPORT_HOURS = (9, 12, 15)
DAY_HOURS = range(7, 18)  # 07:00–17:00 solar time, hourly
OPEN_TYPES = {"open_space", "green_space", "parking"}


def solar_position(lat: float, day: date, solar_hour: float) -> tuple[float, float]:
    """Return (elevation°, azimuth° clockwise from north) at a given true solar time."""
    doy = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (doy - 1 + (solar_hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    ha = math.radians(15 * (solar_hour - 12))
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.acos(cos_zen)
    elev = 90 - math.degrees(zen)
    sin_zen = math.sin(zen)
    if sin_zen < 1e-9:
        return elev, 180.0
    cos_az = (math.sin(decl) - math.sin(phi) * cos_zen) / (math.cos(phi) * sin_zen)
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if ha > 0:
        az = 360 - az
    return elev, az


def _shift(elev: float, az: float, height: float) -> tuple[float, float]:
    length = height / math.tan(math.radians(elev))
    a = math.radians(az)
    return -math.sin(a) * length, -math.cos(a) * length  # shadow points away from the sun


def prism_shadow(footprint, height: float, elev: float, az: float):
    dx, dy = _shift(elev, az, height)
    polys = list(footprint.geoms) if footprint.geom_type == "MultiPolygon" else [footprint]
    parts = []
    for p in polys:
        moved = translate(p, dx, dy)
        parts.extend([p, moved])
        coords = list(p.exterior.coords)
        for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
            quad = Polygon([(x1, y1), (x2, y2), (x2 + dx, y2 + dy), (x1 + dx, y1 + dy)])
            if quad.is_valid and quad.area > 0:
                parts.append(quad)
    return unary_union(parts)


def sunlit(geom, shadow):
    """The part of `geom` in sun for one hour's shadow; a None shadow means the sun is at or below the horizon (no sun)."""
    return geom.difference(shadow) if shadow is not None else geom.difference(geom)


def analysis_date(ctx: Context) -> tuple[date, str]:
    if ctx.config.get("solar_date"):
        return date.fromisoformat(ctx.config["solar_date"]), "user-selected date"
    year = date.today().year
    months = ctx.shared.get("hottest_months")
    if months:
        # Middle month of the hottest three, handling the December–January wrap.
        ms = set(months)
        mid = next((mm for mm in ms if ((mm - 2) % 12 + 1) in ms and (mm % 12 + 1) in ms), sorted(months)[len(months) // 2])
        return date(year, mid, 21), "21st of the middle hottest month (climate baseline)"
    return (date(year, 6, 21), "June solstice (climate baseline unavailable)") if ctx.lat >= 0 else (date(year, 12, 21), "December solstice (climate baseline unavailable)")


def run(ctx: Context) -> dict:
    day, day_basis = analysis_date(ctx)
    positions = {h: solar_position(ctx.lat, day, h) for h in DAY_HOURS}
    m = []
    for h in REPORT_HOURS:
        el, az = positions[h]
        m.append(V.metric(f"solar_elevation_deg_{h:02d}00", f"Sun elevation at {h:02d}:00 solar time ({day.isoformat()})", round(el, 2), "°", method="NOAA solar position (Spencer series) at true solar time", condition=V.EXISTING))
        m.append(V.metric(f"solar_azimuth_deg_{h:02d}00", f"Sun azimuth at {h:02d}:00 solar time ({day.isoformat()})", round(az, 1), "° from north", method="NOAA solar position (Spencer series) at true solar time", condition=V.EXISTING))
    feats = (ctx.scenario or {}).get("features", {}).get("features", [])
    local = ctx.local_crs
    casters, missing_h, open_parts = [], 0, []
    for f in feats:
        p = f["properties"]
        t = p.get("feature_type")
        g = geo.to_local(shape(f["geometry"]), local)
        if t == "building" and g.geom_type in ("Polygon", "MultiPolygon"):
            if p.get("height_m"):
                casters.append(("prism", g, float(p["height_m"])))
            else:
                missing_h += 1
        elif t == "shade_structure" and g.geom_type in ("Polygon", "MultiPolygon") and p.get("height_m"):
            casters.append(("canopy", g, float(p["height_m"])))
        elif t == "tree" and p.get("height_m") and p.get("canopy_radius_m"):
            c = g.centroid if g.geom_type != "Point" else g
            casters.append(("canopy", c.buffer(float(p["canopy_radius_m"]), quad_segs=12), float(p["height_m"])))
        if t in OPEN_TYPES and g.geom_type in ("Polygon", "MultiPolygon"):
            open_parts.append(g)
    limitations = [
        "Flat terrain assumed; surrounding off-site buildings and terrain are not modelled.",
        "Trees and shade structures are treated as opaque; real canopies transmit some light.",
        "Times are true solar time (12:00 = sun due south/north), not clock time.",
    ]
    if not casters:
        reason = ("No building heights are available, so building-cast shade cannot be calculated. Add heights on the Inputs screen."
                  if feats else "No design geometry is available for shade analysis.")
        for h in REPORT_HOURS:
            m.append(V.missing(f"solar_open_space_exposed_pct_{h:02d}00", f"Open space not in building shade at {h:02d}:00", "%", V.BLOCKED, reason))
        m.append(V.missing("solar_open_space_shade_hours", "Mean building-cast shade duration on open space (07–17 h)", "h", V.BLOCKED, reason))
        return result(PARTIAL if feats else BLOCKED, metrics=m, limitations=limitations, reason=reason,
                      details={"date": day.isoformat(), "date_basis": day_basis, "positions": {h: positions[h] for h in REPORT_HOURS}})
    buildings_u = unary_union([g for k, g, _ in casters if k == "prism"]) if any(k == "prism" for k, _, _ in casters) else None
    site_l = geo.to_local(shape(ctx.project["boundary"]), local) if ctx.project.get("boundary") else None
    open_u = unary_union(open_parts) if open_parts else None
    if open_u is not None and buildings_u is not None:
        open_u = open_u.difference(buildings_u)
    ground = (site_l.difference(buildings_u) if buildings_u is not None else site_l) if site_l is not None else None
    shade_by_hour = {}
    for h in DAY_HOURS:
        el, az = positions[h]
        if el <= 1:
            shade_by_hour[h] = None  # sun below/at horizon: everything is unlit, not "shaded"
            continue
        parts = []
        for kind, g, height in casters:
            if kind == "prism":
                # Memoised per request: candidate designs (A10) re-use the unchanged buildings' exact shadows.
                memo = ctx.site_cache.setdefault("prism_shadows", {})
                key = (g.wkb, height, el, az)
                if key not in memo:
                    memo[key] = prism_shadow(g, height, el, az)
                parts.append(memo[key])
            else:
                dx, dy = _shift(el, az, height)
                parts.append(translate(g, dx, dy))
        shade_by_hour[h] = unary_union(parts)
    # Hourly shadows (local CRS) for the heat-stress and hotspot analyses; None = sun at or below the horizon.
    ctx.shared["solar"] = {"date": day.isoformat(), "positions": positions, "shade_by_hour": shade_by_hour,
                           "buildings_without_height": missing_h, "local_crs": local}
    # The method text must be identical across scenarios so results stay comparable; counts go to `extra`.
    method = f"Exact prism shadows of buildings with heights plus flat canopy shadows (shade structures, sized trees) on flat ground, {day.isoformat()}"
    dd = {"extra": {"building_casters": sum(1 for k, _, _ in casters if k == "prism"), "canopy_casters": sum(1 for k, _, _ in casters if k != "prism")}}
    for h in REPORT_HOURS:
        sh = shade_by_hour[h]
        for target, geom, label in (("open_space", open_u, "designated open/green/parking space"), ("site_ground", ground, "unbuilt site area")):
            name = f"solar_{target}_exposed_pct_{h:02d}00"
            if geom is None or geom.area <= 0:
                m.append(V.missing(name, f"Share of {label} in sun at {h:02d}:00", "%", V.NOT_APPLICABLE, f"No {label} in the design.", **dd))
                continue
            if sh is None:
                m.append(V.missing(name, f"Share of {label} in sun at {h:02d}:00", "%", V.NOT_APPLICABLE, "The sun is below the horizon at this time.", **dd))
                continue
            exposed = 100 * (1 - geom.intersection(sh).area / geom.area)
            m.append(V.metric(name, f"Share of {label} in sun at {h:02d}:00 solar time", round(exposed, 1), "%", method=method, **dd))
    if open_u is not None and open_u.area > 0:
        hours = sum((open_u.intersection(sh).area / open_u.area) for sh in shade_by_hour.values() if sh is not None)
        m.append(V.metric("solar_open_space_shade_hours", "Mean geometric shade duration on open/green/parking space from buildings, shade structures and sized trees (07–17 h solar)", round(hours, 2), "h",
                          method="Σ over hourly time steps of shaded share × 1 h (rectangle rule)", **dd))
    status = COMPLETED
    warnings = []
    if missing_h:
        status = PARTIAL
        warnings.append(f"{missing_h} building(s) have no height and cast no shadow in this analysis; shade is underestimated.")
    fc = {"type": "FeatureCollection", "features": []}
    for h in ([] if ctx.partial else REPORT_HOURS):
        if shade_by_hour[h] is not None:
            fc["features"].append({"type": "Feature", "geometry": mapping(geo.to_wgs84(shade_by_hour[h], local)), "properties": {"solar_time": f"{h:02d}:00", "date": day.isoformat()}})
    layers = [] if ctx.partial else [layer(f"Building-cast shade ({day.isoformat()}, solar time)", "vector", V.CALCULATED, save_geojson(ctx.out_dir / "shadows.geojson", fc),
                                           legend={h: f"{h:02d}:00" for h in REPORT_HOURS})]
    details = {"date": day.isoformat(), "date_basis": day_basis, "positions": {str(h): {"elevation": round(positions[h][0], 2), "azimuth": round(positions[h][1], 1)} for h in DAY_HOURS},
               "casters": len(casters), "buildings_without_height": missing_h}
    return result(status, metrics=m, layers=layers, warnings=warnings, limitations=limitations, details=details)
