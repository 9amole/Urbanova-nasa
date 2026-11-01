"""OpenStreetMap context via the Overpass API (FR-15)."""

from __future__ import annotations

from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from .base import ProviderError, http_json, provider

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GREEN_TAGS = {
    "leisure": {"park", "garden", "pitch", "golf_course", "playground", "recreation_ground"},
    "landuse": {"grass", "forest", "meadow", "recreation_ground", "village_green", "orchard", "allotments", "cemetery"},
    "natural": {"wood", "scrub", "grassland", "heath", "tree_row"},
}


def _query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    return (
        "[out:json][timeout:60];("
        f'way["building"]{b};'
        f'way["highway"]{b};'
        f'way["leisure"~"park|garden|pitch|golf_course|playground|recreation_ground"]{b};'
        f'way["landuse"~"grass|forest|meadow|recreation_ground|village_green|orchard|allotments|cemetery"]{b};'
        f'way["natural"~"wood|scrub|grassland|heath|water"]{b};'
        f'way["water"]{b};'
        ");out geom tags;"
    )


def classify(tags: dict) -> str | None:
    if "building" in tags and tags.get("building") != "no":
        return "building"
    if "highway" in tags:
        return "road"
    if tags.get("natural") == "water" or "water" in tags:
        return "water"
    for key, values in GREEN_TAGS.items():
        if tags.get(key) in values:
            return "green_space"
    return None


def fetch_context(study_wgs84: BaseGeometry) -> dict:
    p = provider("osm_overpass")
    minx, miny, maxx, maxy = study_wgs84.bounds
    query = _query((miny, minx, maxy, maxx))
    # OSM is a live database: cached for reuse within a run set, but recorded with its retrieval time.
    body, meta = http_json("osm_overpass", "POST", OVERPASS_URL, data={"data": query}, cache=True, cache_params={"q": query})
    if not isinstance(body, dict) or "elements" not in body:
        raise ProviderError("osm_overpass", "invalid_response", "No 'elements' in response.")
    if body.get("remark") and "runtime error" in body["remark"].lower():
        raise ProviderError("osm_overpass", "http_error", body["remark"][:200])
    features = []
    skipped = 0
    for el in body["elements"]:
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        tags = el.get("tags") or {}
        ftype = classify(tags)
        if not ftype:
            continue
        coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
        try:
            if ftype in ("building", "green_space", "water") and len(coords) >= 4 and coords[0] == coords[-1]:
                geom = Polygon(coords)
                if not geom.is_valid:
                    geom = geom.buffer(0)
            elif len(coords) >= 2:
                geom = LineString(coords)
                if ftype != "road":
                    skipped += 1
                    continue
            else:
                continue
        except Exception:
            skipped += 1
            continue
        features.append({"osm_id": el["id"], "feature_type": ftype, "geometry": geom, "tags": {k: tags[k] for k in list(tags)[:12]}})
    return {
        "provider": "osm_overpass", "features": features, "skipped": skipped, "meta": meta,
        "osm_timestamp": (body.get("osm3s") or {}).get("timestamp_osm_base"), "attribution": p["attribution"],
        "license": p["license"], "request_params": {"bbox": [minx, miny, maxx, maxy]},
    }
