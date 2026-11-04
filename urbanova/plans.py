"""Urban-plan geometry extraction, classification and review (FR-02, FR-37, Module 3).

Supported automatic extraction: GeoJSON and DXF (DXF requires an EPSG code).
PDF/PNG/JPEG plans are preserved as reference drawings only: a plan image is not
georeferenced and pixel sizes do not establish real-world area (FR-02 limitations).
"""

from __future__ import annotations

import json
import re
from typing import Any

from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry

from . import db, geo

FEATURE_TYPES = {
    "site_boundary": "Site boundary",
    "building": "Building footprint",
    "road": "Road / path",
    "green_space": "Green space (planned/mapped)",
    "tree": "Tree / canopy",
    "parking": "Parking area",
    "open_space": "Open space / courtyard / plaza",
    "water": "Water feature",
    "shade_structure": "Shade structure",
    "land_use_zone": "Land-use zone",
    "unclassified": "Unclassified (needs review)",
}
AREA_TYPES = {"building", "green_space", "parking", "open_space", "water", "shade_structure", "land_use_zone", "site_boundary"}

# Keyword → type mapping for property values / DXF layer names. Explicit
# `feature_type` properties win; keyword matches are "medium" confidence.
KEYWORDS: list[tuple[str, str]] = [
    (r"boundary|plot|parcel|site[_ ]?line|red[_ ]?line", "site_boundary"),
    (r"building|bldg|footprint|block|tower|villa|house", "building"),
    (r"road|street|highway|path|walkway|footway|sidewalk|carriageway|circulation", "road"),
    # Shade structures before trees: "shade canopy" / "canopy structure" are built, not planted.
    (r"pergola|shade|canopy[_ ]?struct|shelter", "shade_structure"),
    (r"tree|canopy", "tree"),
    # Parking before green: "park" would otherwise claim PARKING / CAR_PARK layers as green space.
    (r"parking|car[_ \-]?park|garage", "parking"),
    # "park" only as a park, never as part of "parking" or "car park".
    (r"green|(?<!car)(?<!car[_ \-])park(?!ing)|garden|lawn|grass|landscap|planting|vegetat", "green_space"),
    (r"plaza|courtyard|square|open[_ ]?space|hardscape|paved", "open_space"),
    (r"water|pond|pool|lake|fountain", "water"),
    (r"zone|landuse|land[_ ]?use|zoning", "land_use_zone"),
]
TYPE_PROPS = ("feature_type", "type", "class", "category", "layer", "landuse", "leisure", "natural", "amenity", "use", "kind", "name")
HEIGHT_PROPS = ("height_m", "height", "bldg_height", "building_height")
FLOOR_PROPS = ("floors", "levels", "storeys", "stories", "building:levels", "num_floors")
DEFAULT_TREE_CANOPY_RADIUS_M = None  # never assumed — a point tree without canopy size has no area


class PlanError(ValueError):
    pass


def classify(props: dict, layer: str | None = None) -> tuple[str, str, str]:
    """Return (feature_type, source, confidence)."""
    explicit = str(props.get("feature_type") or "").strip().lower()
    if explicit in FEATURE_TYPES:
        return explicit, "explicit attribute 'feature_type'", "high"
    candidates = []
    if layer:
        candidates.append(("DXF layer", layer))
    for k in TYPE_PROPS:
        if props.get(k) not in (None, ""):
            candidates.append((f"attribute '{k}'", str(props[k])))
    if str(props.get("building", "")).lower() not in ("", "no", "none", "false", "0"):
        return "building", "attribute 'building'", "medium"
    for source, text in candidates:
        t = text.lower()
        for pattern, ftype in KEYWORDS:
            if re.search(pattern, t):
                return ftype, f"keyword in {source} ('{text[:40]}')", "medium"
    return "unclassified", "no recognisable attribute", "low"


METRES = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*$", re.I)
COUNT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _number(props: dict, keys: tuple[str, ...], issues: list[str], metres: bool = True) -> tuple[float | None, str | None]:
    """First positive value among `keys`. Lengths must be in metres: a bare number or one ending in "m".
    Any other unit (ft, cm, mm, ...) or text is reported and ignored, never converted or stripped."""
    for k in keys:
        raw = props.get(k)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            v = float(raw)
        else:
            m = (METRES if metres else COUNT).match(str(raw))
            if not m:
                expect = "a number in metres (for example 12 or 12 m)" if metres else "a whole number"
                issues.append(f"Attribute '{k}' = '{raw}' is not {expect}; it was ignored. Correct it on the Inputs tab.")
                continue
            v = float(m.group(1))
        if v > 0:
            return v, k
    return None, None


def _attributes(props: dict) -> tuple[dict, list[str]]:
    attrs: dict[str, Any] = {}
    issues: list[str] = []
    h, hk = _number(props, HEIGHT_PROPS, issues)
    if h is not None:
        if h > 1000:
            issues.append(f"Height {h} m is implausible and was ignored.")
        else:
            attrs["height_m"] = h
            attrs["height_source"] = f"user data attribute '{hk}'"
    f, fk = _number(props, FLOOR_PROPS, issues, metres=False)
    if f is not None:
        if f > 200 or f != int(f):
            issues.append(f"Floor count {f} is implausible and was ignored.")
        else:
            attrs["floors"] = int(f)
            attrs["floors_source"] = f"user data attribute '{fk}'"
    canopy, ck = _number(props, ("canopy_radius_m", "crown_radius_m"), issues)
    if canopy is not None:
        attrs["canopy_radius_m"] = canopy
    for k in ("name", "species", "surface_material", "albedo", "land_use"):
        if props.get(k) not in (None, ""):
            attrs[k] = props[k]
    return attrs, issues


def _feature_record(project_id: str, file_id: str, geom_wgs: BaseGeometry, props: dict, ref: str, layer: str | None = None) -> dict:
    ftype, source, conf = classify(props, layer)
    attrs, issues = _attributes(props)
    review = "needs_review" if conf == "low" or issues else "auto_classified"
    gj = mapping(geom_wgs)
    return {
        "id": db.new_id("ftr"), "project_id": project_id, "file_id": file_id, "feature_type": ftype,
        "geometry": gj, "original_geometry": gj, "attributes": {**attrs, "source_properties": _jsonable(props)},
        "classification_source": source, "classification_confidence": conf, "review_status": review,
        "original_reference": ref, "correction_history": [{"at": db.now(), "event": "extracted", "issues": issues}],
        "active": 1, "created_at": db.now(),
    }


def _jsonable(props: dict) -> dict:
    return json.loads(json.dumps(props, default=str))


def parse_geojson(content: bytes, declared_epsg: str | None) -> tuple[list[tuple[BaseGeometry, dict, str]], list[str], str]:
    try:
        obj = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"The GeoJSON file could not be read: {exc}.")
    src_crs = geo.WGS84
    notes = []
    legacy = (obj.get("crs") or {}).get("properties", {}).get("name") if isinstance(obj.get("crs"), dict) else None
    if declared_epsg:
        src_crs = geo.parse_epsg(declared_epsg)
        notes.append(f"Coordinates interpreted in the declared CRS {src_crs}.")
    elif legacy:
        try:
            src_crs = geo.parse_epsg(legacy.replace("urn:ogc:def:crs:", "").replace("::", ":").replace("OGC:1.3:CRS84", "EPSG:4326"))
            notes.append(f"Coordinates interpreted in the file's CRS member {src_crs}.")
        except geo.GeometryError:
            raise PlanError(f"The file declares CRS '{legacy}', which is not recognised. Supply its EPSG code.")
    if obj.get("type") == "FeatureCollection":
        feats = obj.get("features") or []
    elif obj.get("type") == "Feature":
        feats = [obj]
    else:
        feats = [{"type": "Feature", "geometry": obj, "properties": {}}]
    out = []
    for i, f in enumerate(feats):
        g = (f or {}).get("geometry")
        if not g:
            notes.append(f"Feature {i} has no geometry and was skipped.")
            continue
        try:
            geom = shape(g)
        except Exception as exc:
            notes.append(f"Feature {i} has unreadable geometry ({exc}) and was skipped.")
            continue
        if geom.is_empty:
            notes.append(f"Feature {i} is empty and was skipped.")
            continue
        out.append((geom, (f.get("properties") or {}), f"feature[{i}]" + (f" id={f['id']}" if f.get("id") is not None else "")))
    if src_crs == geo.WGS84 and out:
        for geom, _, _ in out:
            minx, miny, maxx, maxy = geom.bounds
            if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
                raise PlanError(
                    "Coordinates are outside longitude/latitude ranges, so the file is probably in a projected CRS. "
                    "Upload it again with its EPSG code (for example 32636 for UTM zone 36N)."
                )
    return out, notes, src_crs


def _dxf_geometries(entity, layer_override: str | None, depth: int, notes: list[str], skipped: dict[str, int]):
    """Yield (geometry, layer, reference) for one DXF entity; INSERT blocks are exploded recursively."""
    from ezdxf import path as dxfpath

    kind = entity.dxftype()
    layer = entity.dxf.get("layer", "0")
    if layer == "0" and layer_override:
        layer = layer_override  # block contents on layer 0 inherit the INSERT's layer (AutoCAD convention)
    ref = f"{kind} {entity.dxf.get('handle', '?')}"
    try:
        if kind == "LWPOLYLINE":
            pts = [(q[0], q[1]) for q in entity.get_points("xy")]
            closed = entity.closed or (len(pts) > 2 and pts[0] == pts[-1])
        elif kind == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
            closed = entity.is_closed
        elif kind == "LINE":
            pts = [(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]
            closed = False
        elif kind == "CIRCLE":
            c = entity.dxf.center
            yield Point(c.x, c.y).buffer(entity.dxf.radius, quad_segs=16), layer, ref
            return
        elif kind == "HATCH":
            rings = []
            for hp in dxfpath.from_hatch(entity):
                ring = [(v.x, v.y) for v in hp.flattening(0.05)]
                if len(ring) >= 3:
                    rings.append(Polygon(ring))
            if not rings:
                skipped["HATCH (no closed boundary)"] = skipped.get("HATCH (no closed boundary)", 0) + 1
                return
            rings.sort(key=lambda g: g.area, reverse=True)
            outer = rings[0]
            for inner in rings[1:]:
                if outer.contains(inner):
                    outer = outer.difference(inner)  # island / hole inside the hatch
            yield outer, layer, ref
            return
        elif kind == "INSERT":
            if depth >= 5:
                skipped["INSERT (nested deeper than 5 levels)"] = skipped.get("INSERT (nested deeper than 5 levels)", 0) + 1
                return
            for sub in entity.virtual_entities():
                for g, lyr, sref in _dxf_geometries(sub, layer, depth + 1, notes, skipped):
                    yield g, lyr, f"{ref} > {sref}"
            return
        else:
            skipped[kind] = skipped.get(kind, 0) + 1
            return
    except Exception as exc:  # malformed entity
        notes.append(f"Entity {ref} skipped: {exc}")
        return
    if len(pts) < 2:
        return
    yield (Polygon(pts) if closed and len(pts) >= 3 else LineString(pts)), layer, ref


def parse_dxf(path: str, declared_epsg: str | None) -> tuple[list[tuple[BaseGeometry, dict, str, str]], list[str], str, dict]:
    """Return (items, notes, crs, meta). meta: units_unconfirmed (bool), skipped_entities ({type: count})."""
    import ezdxf
    from ezdxf import recover

    if not declared_epsg:
        raise PlanError(
            "DXF drawings do not carry a reliable coordinate reference system. Supply the EPSG code of the drawing's "
            "coordinates (for example a UTM zone). Drawings in local/arbitrary coordinates must be georeferenced first."
        )
    crs = geo.parse_epsg(declared_epsg)
    if crs == geo.WGS84:
        raise PlanError("DXF coordinates in degrees are not supported; use the drawing's projected CRS (metres).")
    try:
        doc, auditor = recover.readfile(path)
    except (IOError, ezdxf.DXFStructureError) as exc:
        raise PlanError(f"The DXF file is corrupted or unreadable: {exc}.")
    notes = []
    if auditor.has_errors:
        notes.append(f"The DXF had {len(auditor.errors)} structural errors that were repaired on read; review extracted features.")
    units = doc.header.get("$INSUNITS", 0)
    # 6 = metres. Any other unit would need explicit scaling, which we do not guess.
    if units not in (0, 6):
        raise PlanError(f"The drawing units code $INSUNITS={units} is not metres. Re-export the drawing in metres.")
    units_unconfirmed = units == 0
    if units_unconfirmed:
        notes.append(
            "The drawing does not declare its units ($INSUNITS unset), so the scale is unresolved. Features are marked for review "
            "and area-based indicators stay blocked until you confirm that the drawing units are metres."
        )
    out = []
    skipped: dict[str, int] = {}
    for e in doc.modelspace():
        for geom, layer, ref in _dxf_geometries(e, None, 0, notes, skipped):
            out.append((geom, {"layer": layer}, ref, layer))
    if skipped:
        notes.append("Entities not used as plan geometry: " + ", ".join(f"{n} {k}" for k, n in sorted(skipped.items())) + ".")
    return out, notes, crs, {"units_unconfirmed": units_unconfirmed, "skipped_entities": skipped}


REFERENCE_ONLY = {
    "status": "stored_reference_only",
    "message": (
        "Stored as a reference drawing. Automatic geometry extraction from PDF/image plans is not supported in "
        "this build, and an image is not georeferenced: no areas are calculated from it. To analyse this design, "
        "export the plan as GeoJSON or DXF (with its EPSG code)."
    ),
    "features_extracted": 0,
}


def import_plan(project: dict, file_rec: dict, content: bytes, declared_epsg: str | None) -> dict:
    """Extract features from a stored plan file and save them (prototype app). Returns the file validation summary."""
    ftype = file_rec["detected_type"]
    if ftype in ("pdf", "png", "jpeg"):
        return dict(REFERENCE_ONLY)
    out = extract_features(project, file_rec["id"], ftype, content, file_rec["storage_path"], declared_epsg)
    for r in out["records"]:
        db.insert("features", r)
    return {**out["summary"], "quality": geometry_quality(project["id"])}


def extract_features(project: dict, file_id: str | None, ftype: str, content: bytes, path: str | None, declared_epsg: str | None) -> dict:
    """Database-free extraction: {"records": feature records, "summary": validation summary}.
    `project` needs latitude and longitude; `path` is required for DXF (ezdxf reads from disk)."""
    pid = project.get("id")
    if ftype in ("pdf", "png", "jpeg"):
        return {"records": [], "summary": dict(REFERENCE_ONLY)}
    if ftype == "geojson":
        raw, notes, src_crs = parse_geojson(content, declared_epsg)
        items = [(g, p, ref, None) for g, p, ref in raw]
        meta: dict = {"units_unconfirmed": False, "skipped_entities": {}}
    elif ftype == "dxf":
        if not path:
            raise PlanError("A DXF file must be read from disk; no file path was given.")
        items, notes, src_crs, meta = parse_dxf(path, declared_epsg)
    else:
        raise PlanError(f"{ftype.upper()} is not a plan format.")
    if not items:
        raise PlanError("No usable geometry was found in the file.")
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    site_pt = geo.to_local(Point(project["longitude"], project["latitude"]), local)
    records, issues = [], []
    far = 0
    for geom, props, ref, layer in items:
        g_wgs = geo.reproject(geom, src_crs, geo.WGS84)
        fixed, reason = geo.repair(g_wgs)
        rec = _feature_record(pid, file_id, fixed, props, ref, layer)
        if reason:
            rec["correction_history"][0]["issues"].append(f"Invalid geometry repaired automatically ({reason}); review the shape.")
            rec["review_status"] = "needs_review"
        if rec["feature_type"] in AREA_TYPES and fixed.geom_type in ("LineString", "MultiLineString"):
            rec["correction_history"][0]["issues"].append("An area feature type was assigned to an open line; it cannot contribute area until corrected.")
            rec["review_status"] = "needs_review"
        if meta["units_unconfirmed"]:
            rec["attributes"]["units_unconfirmed"] = True
            rec["review_status"] = "needs_review"
            rec["correction_history"][0]["issues"].append("Drawing units are not declared; confirm they are metres before areas are used.")
        if geo.to_local(fixed, local).distance(site_pt) > 5000:
            far += 1
            rec["review_status"] = "needs_review"
            rec["correction_history"][0]["issues"].append("Feature lies more than 5 km from the project location; check the CRS.")
        records.append(rec)
    if far == len(records):
        raise PlanError(
            "All features are more than 5 km from the project location. The CRS is probably wrong, or the plan belongs to another site."
        )
    by_type: dict[str, int] = {}
    for r in records:
        by_type[r["feature_type"]] = by_type.get(r["feature_type"], 0) + 1
    return {"records": records, "summary": {
        "status": "extracted",
        "source_crs": src_crs,
        "features_extracted": len(records),
        "by_type": by_type,
        "needs_review": sum(1 for r in records if r["review_status"] == "needs_review"),
        "notes": notes + issues,
        "units_unconfirmed": meta["units_unconfirmed"],
        "skipped_entities": meta["skipped_entities"],
        "quality": geometry_quality_for(records, project),
        "enabled_analyses": enabled_by_geometry(by_type, records),
    }}


def enabled_by_geometry(by_type: dict, records: list[dict]) -> list[str]:
    enabled = []
    if by_type.get("building"):
        enabled.append("Building coverage and footprint metrics")
        if any("height_m" in r["attributes"] or "floors" in r["attributes"] for r in records if r["feature_type"] == "building"):
            enabled.append("Geometric shade analysis (for buildings with heights)")
    if by_type.get("green_space") or by_type.get("tree"):
        enabled.append("Planned green-space proportion")
    if len(by_type) > 0:
        enabled.append("Scenario creation and comparison")
    return enabled


def active_features(project_id: str) -> list[dict]:
    return db.select("features", "project_id = ? AND active = 1", [project_id], order="created_at")


def geometry_quality(project_id: str) -> dict:
    """Geometry-quality report: invalid, overlapping and unreviewed features (FR-02 step 6)."""
    return geometry_quality_for(active_features(project_id), db.get("projects", project_id))


def geometry_quality_for(feats: list[dict], project: dict) -> dict:
    """Database-free core of geometry_quality. `project` needs latitude, longitude and optional boundary."""
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    buildings = [(f["id"], geo.to_local(shape(f["geometry"]), local)) for f in feats if f["feature_type"] == "building" and shape(f["geometry"]).geom_type in ("Polygon", "MultiPolygon")]
    overlaps = []
    for i in range(len(buildings)):
        for j in range(i + 1, len(buildings)):
            inter = buildings[i][1].intersection(buildings[j][1]).area
            if inter > 1.0:
                overlaps.append({"a": buildings[i][0], "b": buildings[j][0], "overlap_m2": round(inter, 1)})
    outside = []
    if project.get("boundary"):
        site = geo.to_local(shape(project["boundary"]), local)
        for f in feats:
            if f["feature_type"] == "site_boundary":
                continue
            g = geo.to_local(shape(f["geometry"]), local)
            if not g.intersects(site):
                outside.append(f["id"])
    boundary_feats = [f for f in feats if f["feature_type"] == "site_boundary"]
    boundary_conflict = None
    if boundary_feats and project.get("boundary"):
        plan_area = sum(geo.to_local(shape(f["geometry"]), local).area for f in boundary_feats)
        proj_area = geo.to_local(shape(project["boundary"]), local).area
        diff = abs(plan_area - proj_area) / proj_area * 100
        if diff > 5:
            boundary_conflict = {
                "plan_boundary_area_m2": round(plan_area, 1), "project_boundary_area_m2": round(proj_area, 1),
                "difference_pct": round(diff, 1),
                "message": "The plan's site boundary differs from the project boundary by more than 5%. Confirm which one is correct; the project boundary is used for calculations.",
            }
    return {
        "feature_count": len(feats),
        "needs_review": [f["id"] for f in feats if f["review_status"] == "needs_review"],
        "unclassified": [f["id"] for f in feats if f["feature_type"] == "unclassified"],
        "building_overlaps": overlaps,
        "outside_site": outside,
        "buildings_without_height": [f["id"] for f in feats if f["feature_type"] == "building" and "height_m" not in (f["attributes"] or {})],
        "units_unconfirmed": [f["id"] for f in feats if (f["attributes"] or {}).get("units_unconfirmed")],
        "boundary_conflict": boundary_conflict,
    }


def deactivate_features(feats: list[dict], user: dict, reason: str) -> int:
    """Deactivate features (kept in the database and history, never deleted)."""
    for f in feats:
        history = list(f.get("correction_history") or [])
        history.append({"at": db.now(), "by": user["id"], "event": "deactivated", "reason": reason})
        db.update("features", "id", f["id"], {"active": 0, "correction_history": history})
    return len(feats)


def confirm_units(file_rec: dict, user: dict) -> dict:
    """The user confirms that an undeclared-unit DXF is drawn in metres; recorded on every affected feature."""
    feats = db.select("features", "file_id = ?", [file_rec["id"]])
    affected = [f for f in feats if (f["attributes"] or {}).get("units_unconfirmed")]
    if not affected:
        raise PlanError("This file has no features with unconfirmed drawing units.")
    for f in affected:
        attrs = {k: v for k, v in f["attributes"].items() if k != "units_unconfirmed"}
        attrs["units_source"] = "user confirmed drawing units are metres"
        history = list(f.get("correction_history") or [])
        history.append({"at": db.now(), "by": user["id"], "event": "units_confirmed_metres"})
        db.update("features", "id", f["id"], {"attributes": attrs, "correction_history": history})
    return {"confirmed": len(affected)}


def correct_feature(feature: dict, user: dict, changes: dict) -> dict:
    """Apply a user correction as an explicit, reversible change (FR-37)."""
    allowed = {"feature_type", "height_m", "floors", "canopy_radius_m", "review_status", "geometry", "active"}
    unknown = set(changes) - allowed
    if unknown:
        raise PlanError(f"These fields cannot be corrected: {', '.join(sorted(unknown))}.")
    upd: dict[str, Any] = {}
    attrs = dict(feature.get("attributes") or {})
    before: dict[str, Any] = {}
    if "feature_type" in changes:
        if changes["feature_type"] not in FEATURE_TYPES:
            raise PlanError(f"Unknown feature type '{changes['feature_type']}'.")
        before["feature_type"] = feature["feature_type"]
        upd["feature_type"] = changes["feature_type"]
        upd["classification_source"] = "user correction"
        upd["classification_confidence"] = "user_verified"
    for key, limit in (("height_m", 1000), ("floors", 200), ("canopy_radius_m", 100)):
        if key in changes:
            before[key] = attrs.get(key)
            val = changes[key]
            if val in (None, ""):
                attrs.pop(key, None)
                attrs.pop(f"{key.split('_')[0]}_source", None)
            else:
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    raise PlanError(f"{key} must be a number.")
                if not 0 < val <= limit:
                    raise PlanError(f"{key} must be between 0 and {limit}.")
                if key == "floors":
                    if val != int(val):
                        raise PlanError("floors must be a whole number.")
                    val = int(val)
                attrs[key] = val
                attrs[("height" if key == "height_m" else key.split("_")[0]) + "_source"] = "user correction"
            upd["attributes"] = attrs
    if "geometry" in changes:
        try:
            g = shape(changes["geometry"])
        except Exception:
            raise PlanError("The corrected geometry is not valid GeoJSON.")
        geo.check_wgs84_bounds(g)
        if not g.is_valid:
            raise PlanError("The corrected geometry is invalid (for example self-intersecting).")
        before["geometry"] = feature["geometry"]
        upd["geometry"] = mapping(g)
    if "active" in changes:
        before["active"] = feature["active"]
        upd["active"] = 1 if changes["active"] else 0
    if "review_status" in changes:
        if changes["review_status"] not in ("reviewed", "needs_review"):
            raise PlanError("review_status must be 'reviewed' or 'needs_review'.")
        upd["review_status"] = changes["review_status"]
    elif upd:
        upd["review_status"] = "reviewed"
    history = list(feature.get("correction_history") or [])
    history.append({"at": db.now(), "by": user["id"], "event": "corrected", "before": before, "after": {k: v for k, v in changes.items() if k != "geometry"}})
    upd["correction_history"] = history
    db.update("features", "id", feature["id"], upd)
    return db.get("features", feature["id"])


def feature_collection(features: list[dict]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", "id": f["id"], "geometry": f["geometry"],
                "properties": {
                    "feature_id": f["id"], "feature_type": f["feature_type"],
                    **{k: v for k, v in (f.get("attributes") or {}).items() if k != "source_properties"},
                    "review_status": f.get("review_status"), "classification_source": f.get("classification_source"),
                    "classification_confidence": f.get("classification_confidence"),
                },
            }
            for f in features
        ],
    }
