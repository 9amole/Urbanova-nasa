"""Land cover around the site from ESA WorldCover 10 m (A2b, PHASE2_SPEC §2.1). Existing condition: site context only.

The newest WorldCover map (v200, 2021) is read onto a 10 m grid in the local UTM zone (nearest, classes kept). Metrics:
the share of each class on the site and in the surrounding ring, and the dominant class around the site. The class grid
is shared with the LST downscaling (S7). A land-cover class is context; it is never a cause of a design hotspot.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import mapping

from .. import catalog, geo
from .. import values as V
from ..providers.base import ProviderError, availability
from ..providers.satellite import read_to_grid, search_static
from .common import BLOCKED, COMPLETED, PARTIAL, UNAVAILABLE, Context, grid_for, layer, mask_for, result, save_geotiff, save_overlay_png

PROVIDER = "pc_esa_worldcover"
# WorldCover classes and official colours (product user manual, v200).
CLASSES = {10: ("Tree cover", "#006400"), 20: ("Shrubland", "#ffbb22"), 30: ("Grassland", "#ffff4c"), 40: ("Cropland", "#f096ff"),
           50: ("Built-up", "#fa0000"), 60: ("Bare / sparse vegetation", "#b4b4b4"), 70: ("Snow and ice", "#f0f0f0"),
           80: ("Permanent water bodies", "#0064c8"), 90: ("Herbaceous wetland", "#0096a0"), 95: ("Mangroves", "#00cf75"), 100: ("Moss and lichen", "#fae6a0")}
RES = 10


def class_key(code: int) -> str:
    return CLASSES[code][0].lower().replace(" / ", "_").replace(" ", "_")


def class_shares(classes: np.ndarray, mask: np.ndarray) -> dict[int, float]:
    """% of valid pixels in `mask` per WorldCover class (classes present only; they sum to 100)."""
    vals = classes[mask & ~np.isnan(classes)].astype(int)
    if not vals.size:
        return {}
    codes, counts = np.unique(vals, return_counts=True)
    return {int(c): round(100 * n / vals.size, 2) for c, n in zip(codes, counts) if int(c) in CLASSES}


def run(ctx: Context) -> dict:
    usable, kind, why = availability(PROVIDER)
    if not usable:
        return result(BLOCKED, reason=why, failures=[{"provider": PROVIDER, "kind": kind, "message": why}])
    cfg = ctx.config
    buffer_m = float(cfg.get("lst_buffer_m", 1000) if cfg.get("lst_downscale") else cfg.get("context_buffer_m", 500))
    site, study, site_basis = ctx.site_and_study(buffer_m)
    local = ctx.local_crs
    site_l, study_l = geo.to_local(site, local), geo.to_local(study, local)
    try:
        items = search_static(PROVIDER, mapping(site.centroid))
        if not items:
            return result(UNAVAILABLE, reason="No WorldCover map covers the site.")
        item = items[0]
        if "map" not in (item.get("assets") or {}):
            return result(UNAVAILABLE, reason="The WorldCover catalogue item has no 'map' asset; land cover was not read.")
        transform, shape_ = grid_for(study_l, RES)
        classes = read_to_grid(PROVIDER, item["assets"]["map"]["href"], (item.get("properties") or {}).get("proj:epsg") or 4326, local, transform, shape_, src_nodata=0)
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"WorldCover could not be read ({exc.kind}).", failures=[exc.as_dict()])
    site_mask, study_mask = mask_for(site_l, transform, shape_), mask_for(study_l, transform, shape_)
    classes[~study_mask] = np.nan
    ring_mask = study_mask & ~site_mask
    on_site, around = class_shares(classes, site_mask), class_shares(classes, ring_mask)
    version = (item.get("properties") or {}).get("esa_worldcover:product_version") or "?"
    year = ((item.get("properties") or {}).get("start_datetime") or "????")[:4]
    method = f"ESA WorldCover {year} (v{version}) 10 m classes reprojected to local UTM 10 m (nearest); share of valid pixels"
    tif = save_geotiff(ctx.out_dir / "worldcover_classes.tif", classes, transform, local, f"ESA WorldCover {year} class code")
    ds_id = catalog.register(
        ctx.project["id"], PROVIDER, "land_cover_map", acquisition_method="catalog_stac", request_params={"collection": "esa-worldcover", "item": item.get("id")},
        processed_path=tif, spatial_coverage=mapping(study), time_coverage={"year": year}, spatial_resolution="10 m", variables=["class"],
        units={"class": "WorldCover class code"}, crs=local, product_id=f"ESA WorldCover {year} v{version} map",
        provenance=["STAC discovery", "Windowed COG read", "Reprojected to local UTM 10 m (nearest)", "Class shares on the site and in the ring"], data_nature="classified",
    )
    src = [ds_id]
    m = []
    for code, (label, _) in CLASSES.items():
        if code in on_site or code in around:
            key = class_key(code)
            m.append(V.metric(f"landcover_site_{key}_pct", f"Site: {label} ({year} land cover)", on_site.get(code, 0.0), "%", output_kind=V.CLASSIFIED, method=method, sources=src))
            m.append(V.metric(f"landcover_surroundings_{key}_pct", f"Surroundings: {label} ({year} land cover)", around.get(code, 0.0), "%", output_kind=V.CLASSIFIED, method=method, sources=src))
    dominant = max(around, key=around.get) if around else None
    if dominant is None:
        statement = "No land-cover classes were read for the surroundings."
    else:
        lead = "The surroundings are mainly" if around[dominant] > 50 else "The largest land-cover class around the site is"
        statement = f"{lead} {CLASSES[dominant][0].lower()} ({around[dominant]:.0f} % of the ring, WorldCover {year})."
    ctx.shared["landcover"] = {"classes": classes, "transform": transform, "crs": local}
    site_context = {"type": "land_cover", "condition": V.EXISTING, "metric": f"landcover_surroundings_{class_key(dominant)}_pct" if dominant else None, "statement": statement, "method": method,
                    "note": "Site context only: land cover describes today's land, not the design, and is never a cause."}
    present = [c for c in CLASSES if c in on_site or c in around]
    png = save_overlay_png(ctx.out_dir / "worldcover.png", classes, transform, local, "", 0, 0, categorical={c: col for c, (_, col) in CLASSES.items()})
    lay = layer(f"Land cover (ESA WorldCover {year})", "raster", V.CLASSIFIED, str(ctx.out_dir / "worldcover.png"), png,
                legend={"type": "categorical", "classes": [{"color": CLASSES[c][1], "label": CLASSES[c][0]} for c in present]}, meta={"geotiff": tif})
    valid = float(np.mean(~np.isnan(classes[study_mask]))) if study_mask.any() else 0.0
    status = COMPLETED if valid > 0.9 else PARTIAL
    warnings = [] if status == COMPLETED else [f"Only {valid:.0%} of the study area has a land-cover class."]
    return result(status, metrics=m, layers=[lay], datasets=src, warnings=warnings, details={"site_context": site_context,
        "item": item.get("id"), "year": year, "version": version, "site_shares_pct": {CLASSES[c][0]: v for c, v in on_site.items()},
        "surroundings_shares_pct": {CLASSES[c][0]: v for c, v in around.items()}, "statement": statement, "site_basis": site_basis,
        "attribution": "ESA WorldCover project / Contains modified Copernicus Sentinel data processed by ESA WorldCover consortium; accessed via Microsoft Planetary Computer"},
        limitations=[f"WorldCover is a {year} snapshot; construction or clearing since then is not shown.",
                     "Land-cover classes have about 77 % overall accuracy (product validation); they describe the surroundings for context, not the design."])
