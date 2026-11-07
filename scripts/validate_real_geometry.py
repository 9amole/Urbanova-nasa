"""D2 (partial): run real-world geometry through the full engine path and check the areas independently.

Real OpenStreetMap footprints of an existing Cairo neighbourhood (Garden City) are written to a
GeoJSON file *without* feature types, as a CAD/GIS export would arrive. The file goes through
extract_plan() (keyword classification, repair, quality checks) and analyse() (urban form), and the
engine's coverage and green shares are recomputed independently in an equal-area projection
(EPSG:6933) from the same polygons. This exercises messy real geometry; it does not replace a
validation on a real project (plan, planning document, quoted rates), which stays open.

Run (needs internet):  .venv/Scripts/python scripts/validate_real_geometry.py
Writes validation/real_geometry_check.json.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyproj import Transformer  # noqa: E402
from shapely.geometry import box, mapping, shape  # noqa: E402
from shapely.ops import transform, unary_union  # noqa: E402

from urbanova import geo, recording  # noqa: E402
from urbanova.engine import analyse, extract_plan  # noqa: E402
from urbanova.providers.osm import fetch_context  # noqa: E402

LAT, LON, HALF_M = 30.0370, 31.2320, 200  # Garden City, Cairo (well mapped in OSM); a 400 m x 400 m site


def main() -> int:
    local = geo.utm_crs_for(LAT, LON)
    from shapely.geometry import Point

    c = geo.to_local(Point(LON, LAT), local)
    site = geo.to_wgs84(box(c.x - HALF_M, c.y - HALF_M, c.x + HALF_M, c.y + HALF_M), local)
    with recording.use(recording.MemoryRecorder()):
        ctx = fetch_context(site)
    polys = [f for f in ctx["features"] if f["feature_type"] in ("building", "green_space") and f["geometry"].geom_type in ("Polygon", "MultiPolygon")]
    # As an export would arrive: OSM tags only, no feature_type.
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": f"osm-{f['osm_id']}", "geometry": mapping(f["geometry"]),
         "properties": {k: v for k, v in f["tags"].items() if k in ("building", "landuse", "leisure", "natural", "amenity", "name", "building:levels")}} for f in polys]}
    raw = json.dumps(fc).encode()
    t0 = time.monotonic()
    plan = extract_plan("garden_city_osm.geojson", raw, {"latitude": LAT, "longitude": LON, "boundary": mapping(site)})
    t_extract = time.monotonic() - t0
    feats = plan["features"]["features"]
    by_type: dict[str, int] = {}
    for f in feats:
        by_type[f["properties"]["feature_type"]] = by_type.get(f["properties"]["feature_type"], 0) + 1
    design = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": f["id"], "geometry": f["geometry"],
         "properties": {"feature_type": f["properties"]["feature_type"], **({"floors": f["properties"]["floors"]} if f["properties"].get("floors") else {})}}
        for f in feats if f["properties"]["feature_type"] != "unclassified"]}
    req = {"contract_version": "1.1", "request_id": "real-geometry-garden-city", "site": {"latitude": LAT, "longitude": LON, "boundary": mapping(site)},
           "designs": [{"id": "existing", "name": "Existing buildings (OSM)", "kind": "baseline", "features": design}],
           "settings": {"modules": ["urban_form"]}}
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        res = analyse(req, tmp)
        t_analyse = time.monotonic() - t0
    d = res["designs"][0]
    met = {m["metric_name"]: m["value"] for m in d["metrics"] if m["state"] == "available"}
    # Independent recomputation in an equal-area CRS.
    to_ea = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
    site_ea = transform(to_ea, site)

    def share(ftype: str) -> float:
        gs = [transform(to_ea, shape(f["geometry"])) for f in design["features"] if f["properties"]["feature_type"] == ftype]
        return unary_union(gs).intersection(site_ea).area / site_ea.area * 100 if gs else 0.0

    independent = {"building_coverage_ratio_pct": round(share("building"), 2), "green_space_pct": round(share("green_space"), 2)}
    diffs = {k: round(met.get(k, float("nan")) - v, 3) for k, v in independent.items()}
    quality = plan["summary"]["quality"]
    out = {
        "generated_at": recording.now(), "method": __doc__.strip().splitlines()[0],
        "site": {"latitude": LAT, "longitude": LON, "size_m": 2 * HALF_M, "osm_snapshot": ctx.get("osm_timestamp")},
        "input": {"features": len(fc["features"]), "vertices": sum(len(json.dumps(f["geometry"]["coordinates"])) // 20 for f in fc["features"])},
        "extract_plan": {"by_type": by_type, "needs_review": plan["summary"]["needs_review"], "building_overlaps": len(quality["building_overlaps"]),
                         "outside_site": len(quality["outside_site"]), "seconds": round(t_extract, 2)},
        "analyse": {"status": d["status"], "urban_form": d["modules"]["urban_form"]["status"], "warnings": d["warnings"], "seconds": round(t_analyse, 2),
                    "engine": {k: met.get(k) for k in independent}, "building_count": met.get("building_count")},
        "independent_equal_area": independent, "engine_minus_independent_pct_points": diffs,
        "pass": all(abs(v) < 0.5 for v in diffs.values()),
    }
    path = ROOT / "validation" / "real_geometry_check.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("input", "extract_plan", "analyse", "independent_equal_area", "engine_minus_independent_pct_points", "pass")}, indent=1, ensure_ascii=False))
    print("wrote", path)
    return 0 if out["pass"] else 3


if __name__ == "__main__":
    sys.exit(main())
