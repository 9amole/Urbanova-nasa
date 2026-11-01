"""Coordinate systems, geometry validation and metric-unit geometry operations (FR-01, FR-12).

All stored geometry is WGS 84 (EPSG:4326, lon/lat). Areas and distances are always
computed in the local UTM zone of the project, never from degrees or image pixels.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.validation import explain_validity

WGS84 = "EPSG:4326"
MAX_SITE_AREA_M2 = 100_000_000  # 100 km² — larger "sites" are almost certainly an input error
MIN_SITE_AREA_M2 = 10
MAX_BOUNDARY_DISTANCE_M = 2_000  # boundary further than this from the entered point is suspicious


class GeometryError(ValueError):
    """Raised with a user-facing explanation of why geometry is unusable."""


def validate_coordinates(lat: Any, lon: Any) -> tuple[float, float]:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        raise GeometryError("Latitude and longitude must be numbers in decimal degrees (for example 30.1200, 31.8500).")
    if lat_f != lat_f or lon_f != lon_f:
        raise GeometryError("Latitude and longitude must be numbers, not NaN.")
    if not -90 <= lat_f <= 90:
        raise GeometryError(f"Latitude {lat_f} is outside the valid range −90 to 90. Check that latitude and longitude were not swapped.")
    if not -180 <= lon_f <= 180:
        raise GeometryError(f"Longitude {lon_f} is outside the valid range −180 to 180.")
    if abs(lat_f) > 84:
        raise GeometryError("Sites beyond 84° latitude are outside the supported UTM-based area calculations.")
    if lat_f == 0 and lon_f == 0:
        raise GeometryError("Coordinates 0, 0 (Null Island in the Atlantic Ocean) are almost certainly a missing value.")
    return lat_f, lon_f


@lru_cache(maxsize=64)
def utm_crs_for(lat: float, lon: float) -> str:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if lat >= 0 else 32700) + zone
    return f"EPSG:{epsg}"


@lru_cache(maxsize=128)
def _transformer(src: str, dst: str) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def reproject(geom: BaseGeometry, src: str, dst: str) -> BaseGeometry:
    if src == dst:
        return geom
    return transform(_transformer(src, dst).transform, geom)


def to_local(geom: BaseGeometry, local_crs: str) -> BaseGeometry:
    return reproject(geom, WGS84, local_crs)


def to_wgs84(geom: BaseGeometry, local_crs: str) -> BaseGeometry:
    return reproject(geom, local_crs, WGS84)


def parse_epsg(value: Any) -> str:
    """Accept '32636', 'EPSG:32636' or a CRS name; return 'EPSG:xxxx' or raise."""
    if value is None or str(value).strip() == "":
        raise GeometryError("A coordinate reference system is required.")
    text = str(value).strip()
    if text.isdigit():
        text = f"EPSG:{text}"
    try:
        crs = CRS.from_user_input(text)
    except Exception:
        raise GeometryError(f"'{value}' is not a recognised coordinate reference system. Use an EPSG code such as 4326 or 32636.")
    epsg = crs.to_epsg()
    if epsg is None:
        raise GeometryError(f"CRS '{value}' has no EPSG code; please supply the EPSG code explicitly.")
    return f"EPSG:{epsg}"


def geojson_to_polygon(obj: Any) -> BaseGeometry:
    """Accept a GeoJSON Polygon/MultiPolygon, Feature or single-feature FeatureCollection."""
    if not isinstance(obj, dict):
        raise GeometryError("The site boundary must be a GeoJSON object.")
    if obj.get("type") == "FeatureCollection":
        feats = [f for f in obj.get("features", []) if f.get("geometry")]
        polys = [f for f in feats if f["geometry"].get("type") in ("Polygon", "MultiPolygon")]
        if len(polys) != 1:
            raise GeometryError(
                f"The boundary FeatureCollection must contain exactly one polygon feature (found {len(polys)})."
            )
        obj = polys[0]["geometry"]
    elif obj.get("type") == "Feature":
        obj = obj.get("geometry") or {}
    if obj.get("type") not in ("Polygon", "MultiPolygon"):
        raise GeometryError(f"The site boundary must be a Polygon or MultiPolygon, not {obj.get('type')!r}.")
    try:
        geom = shape(obj)
    except Exception as exc:
        raise GeometryError(f"The boundary geometry could not be read: {exc}")
    return geom


def check_wgs84_bounds(geom: BaseGeometry) -> None:
    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise GeometryError(
            "Boundary coordinates are outside longitude/latitude ranges. They look projected (metres). "
            "GeoJSON must use WGS 84 longitude/latitude; re-export it or upload it as a plan file with its EPSG code."
        )


def validate_boundary(obj: Any, lat: float, lon: float) -> tuple[dict, list[str]]:
    """Validate a site boundary. Returns (geojson geometry, warnings). Raises GeometryError if unusable."""
    geom = geojson_to_polygon(obj)
    check_wgs84_bounds(geom)
    warnings: list[str] = []
    if not geom.is_valid:
        reason = explain_validity(geom)
        raise GeometryError(
            f"The boundary polygon is not valid ({reason}). Fix self-intersections or overlapping rings and upload it again."
        )
    local = utm_crs_for(lat, lon)
    g_local = to_local(geom, local)
    area = g_local.area
    if area < MIN_SITE_AREA_M2:
        raise GeometryError(f"The boundary encloses only {area:.1f} m², which is not a plausible site.")
    if area > MAX_SITE_AREA_M2:
        raise GeometryError(f"The boundary encloses {area / 1e6:.1f} km², above the {MAX_SITE_AREA_M2 / 1e6:.0f} km² limit for a site.")
    pt = to_local(Point(lon, lat), local)
    dist = g_local.distance(pt)
    if dist > MAX_BOUNDARY_DISTANCE_M:
        raise GeometryError(
            f"The boundary is {dist / 1000:.1f} km from the entered coordinates. Check that both describe the same site."
        )
    if dist > 0:
        warnings.append(f"The entered point lies {dist:.0f} m outside the boundary. Confirm the location is correct.")
    if geom.geom_type == "MultiPolygon":
        warnings.append(f"The boundary has {len(geom.geoms)} separate parts; they are treated as one site.")
    return mapping(geom), warnings


def area_m2(geom_wgs84: BaseGeometry, local_crs: str) -> float:
    return to_local(geom_wgs84, local_crs).area


def study_area(lat: float, lon: float, boundary: dict | None, buffer_m: float) -> tuple[BaseGeometry, BaseGeometry, str]:
    """Return (site_geometry_wgs84, study_area_wgs84, site_basis).

    Without a boundary the "site" is a point buffer of 100 m — always labelled as such.
    """
    local = utm_crs_for(lat, lon)
    if boundary:
        site = shape(boundary)
        basis = "site boundary"
    else:
        site = to_wgs84(to_local(Point(lon, lat), local).buffer(100, quad_segs=16), local)
        basis = "100 m radius around the entered point (no site boundary supplied)"
    study = to_wgs84(to_local(site, local).buffer(buffer_m, quad_segs=16), local)
    return site, study, basis


def repair(geom: BaseGeometry) -> tuple[BaseGeometry, str | None]:
    """Make an invalid geometry valid, returning the reason it was invalid."""
    if geom.is_valid:
        return geom, None
    reason = explain_validity(geom)
    fixed = make_valid(geom)
    return fixed, reason
