"""Site and surrounding-context analysis from OpenStreetMap (FR-15)."""

from __future__ import annotations

from shapely.geometry import mapping
from shapely.ops import unary_union

from .. import catalog, geo
from .. import values as V
from ..providers.base import ProviderError
from ..providers.osm import fetch_context
from .common import COMPLETED, PARTIAL, UNAVAILABLE, Context, layer, result, save_geojson


def run(ctx: Context) -> dict:
    buffer_m = float(ctx.config.get("context_buffer_m", 500))
    site, study, basis = ctx.site_and_study(buffer_m)
    try:
        data = fetch_context(study)
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"OpenStreetMap context could not be retrieved ({exc.kind}).", failures=[exc.as_dict()])
    local = ctx.local_crs
    study_l = geo.to_local(study, local)
    site_l = geo.to_local(site, local)
    by_type: dict[str, list] = {"building": [], "road": [], "green_space": [], "water": []}
    fc = {"type": "FeatureCollection", "features": []}
    for f in data["features"]:
        g_l = geo.to_local(f["geometry"], local)
        if not g_l.intersects(study_l):
            continue
        clipped = g_l.intersection(study_l)
        by_type[f["feature_type"]].append(clipped)
        fc["features"].append({"type": "Feature", "geometry": mapping(geo.to_wgs84(clipped, local)),
                               "properties": {"feature_type": f["feature_type"], "osm_id": f["osm_id"], **f["tags"], "source": "OpenStreetMap"}})
    ds_id = catalog.register(
        ctx.project["id"], "osm_overpass", "vector_context", acquisition_method="api", request_params=data["request_params"],
        cache_key=data["meta"].get("cache_key"), raw_path=data["meta"].get("raw_path"),
        spatial_coverage=mapping(study), time_coverage={"osm_snapshot": data["osm_timestamp"]},
        variables=["building", "road", "green_space", "water"], units={"area": "m²", "length": "m"}, crs="EPSG:4326",
        quality={"features": len(fc["features"]), "skipped_unusable": data["skipped"]},
        provenance=["Ways retrieved with full geometry", "Classified by OSM tags", f"Clipped to {buffer_m:.0f} m study area"],
        data_nature="mapped",
    )
    src = [ds_id]
    study_area = study_l.area
    bld = unary_union(by_type["building"]) if by_type["building"] else None
    green = unary_union(by_type["green_space"]) if by_type["green_space"] else None
    road_len = sum(g.length for g in by_type["road"])
    q = {"osm_snapshot": data["osm_timestamp"], "study_area": f"{buffer_m:.0f} m around {basis}"}
    note = "Mapped features only; OpenStreetMap completeness is unknown and unmapped features are not proof of absence."
    m = [
        V.metric("context_study_area_m2", "Study area analysed", round(study_area, 1), "m²", method=f"Site ({basis}) buffered by {buffer_m:.0f} m, in {local}", sources=src),
        V.metric("context_mapped_buildings_count", "Mapped buildings in study area", len(by_type["building"]), "count", output_kind=V.OBSERVED, method=note, sources=src, quality=q),
        V.metric("context_mapped_building_footprint_pct", "Mapped building footprint share of study area", round((bld.area if bld else 0) / study_area * 100, 2), "%", method="Union of mapped building footprints ÷ study area. " + note, sources=src, quality=q),
        V.metric("context_mapped_road_length_km", "Mapped road/path length in study area", round(road_len / 1000, 3), "km", output_kind=V.OBSERVED, method=note, sources=src, quality=q),
        V.metric("context_mapped_green_pct", "Mapped green space share of study area", round((green.area if green else 0) / study_area * 100, 2), "%", method="Union of mapped parks/gardens/grass/woodland ÷ study area. " + note, sources=src, quality=q),
    ]
    site_bld = bld.intersection(site_l).area if bld else 0.0
    m.append(V.metric("context_existing_mapped_buildings_on_site_m2", f"Existing mapped building footprint on the site ({basis})", round(site_bld, 1), "m²", output_kind=V.OBSERVED, method=note, sources=src, quality=q))
    warnings = [note]
    status = COMPLETED
    if not by_type["building"]:
        status = PARTIAL
        warnings.append("No buildings are mapped in the study area. This may reflect incomplete mapping, an undeveloped area, or both.")
    path = save_geojson(ctx.out_dir / "context_osm.geojson", fc)
    lay = layer("Surrounding context (OpenStreetMap)", "vector", V.OBSERVED, path, legend={"building": "#7a7a7a", "road": "#d9a400", "green_space": "#2e8b57", "water": "#3a7bd5"},
                meta={"attribution": data["attribution"]})
    details = {"study_buffer_m": buffer_m, "site_basis": basis, "osm_snapshot": data["osm_timestamp"], "attribution": data["attribution"],
               "counts": {k: len(v) for k, v in by_type.items()}, "retrieved_at": data["meta"].get("retrieved_at")}
    ctx.shared["context_buildings_local"] = by_type["building"]
    return result(status, metrics=m, layers=[lay], datasets=[ds_id], warnings=warnings,
                  limitations=["OSM tagging and coverage vary by place; mapped land use is not verified ground truth."], details=details)
