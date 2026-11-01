"""Vegetation indicator from Sentinel-2 L2A NDVI (FR-20).

Reflectance = (DN − offset) / 10000 with offset 1000 for processing baseline ≥ 04.00
(ESA harmonisation, January 2022). Pixels flagged by the Scene Classification Layer
as no-data, saturated, cloud shadow, cloud (medium/high), cirrus or snow are masked.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .. import catalog, geo
from .. import values as V
from ..providers.base import ProviderError, availability
from ..providers.satellite import item_summary, read_to_grid, search
from .common import BLOCKED, COMPLETED, PARTIAL, UNAVAILABLE, Context, grid_for, layer, mask_for, result, save_geotiff, save_overlay_png

PROVIDER = "pc_sentinel2_l2a"
SCL_MASK = {0, 1, 3, 8, 9, 10, 11}
VEG_THRESHOLD = 0.2


def _offset(baseline: str | None) -> float:
    try:
        return 1000.0 if float(baseline or "0") >= 4.0 else 0.0
    except ValueError:
        return 1000.0


def run(ctx: Context) -> dict:
    cfg = ctx.config
    buffer_m = float(cfg.get("ndvi_buffer_m", 500))
    max_scenes = int(cfg.get("ndvi_max_scenes", 4))
    min_valid = float(cfg.get("min_valid_site_fraction", 0.6))
    site, study, basis = ctx.site_and_study(buffer_m)
    local = ctx.local_crs
    site_l, study_l = geo.to_local(site, local), geo.to_local(study, local)
    usable, kind, why = availability(PROVIDER)
    if not usable:
        return result(BLOCKED, reason=why, failures=[{"provider": PROVIDER, "kind": kind, "message": why}])
    end_d = date.today() - timedelta(days=7)
    start_d = end_d - timedelta(days=int(cfg.get("ndvi_lookback_days", 365)))
    try:
        items = search(PROVIDER, mapping(site.centroid), start_d.isoformat(), end_d.isoformat(), max_cloud=float(cfg.get("max_scene_cloud_pct", 40)), limit=60)
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"Sentinel-2 scene search failed ({exc.kind}).", failures=[exc.as_dict()])
    if not items:
        return result(UNAVAILABLE, reason="No Sentinel-2 L2A scenes below the cloud limit cover the site in the look-back period.")
    transform, shape_ = grid_for(study_l, 10)
    site_mask, study_mask = mask_for(site_l, transform, shape_), mask_for(study_l, transform, shape_)
    accepted, excluded, stack, failures = [], [], [], []
    seen_dates = set()
    for it in items:
        if len(accepted) >= max_scenes:
            break
        summ = item_summary(it)
        day = summ["datetime"][:10]
        if day in seen_dates:  # adjacent tiles of the same pass; one per date is enough
            continue
        try:
            scl = read_to_grid(PROVIDER, it["assets"]["SCL"]["href"], summ["epsg"], local, transform, shape_, src_nodata=-1)
            red = read_to_grid(PROVIDER, it["assets"]["B04"]["href"], summ["epsg"], local, transform, shape_, src_nodata=0)
            nir = read_to_grid(PROVIDER, it["assets"]["B08"]["href"], summ["epsg"], local, transform, shape_, src_nodata=0)
        except ProviderError as exc:
            failures.append({**exc.as_dict(), "scene_id": summ["scene_id"]})
            excluded.append({**summ, "reason": f"read failed ({exc.kind})"})
            continue
        off = _offset(summ["processing_baseline"])
        r, n = (red - off) / 10000.0, (nir - off) / 10000.0
        bad = np.isnan(scl) | np.isin(np.nan_to_num(scl, nan=0).astype(int), list(SCL_MASK)) | (r <= 0) | (n <= 0) | np.isnan(r) | np.isnan(n)
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = (n - r) / (n + r)
        ndvi[bad] = np.nan
        valid = float(np.mean(~np.isnan(ndvi[site_mask]))) if site_mask.any() else 0.0
        if valid < min_valid:
            excluded.append({**summ, "reason": f"only {valid:.0%} of site pixels clear"})
            continue
        seen_dates.add(day)
        accepted.append({**summ, "valid_site_fraction": round(valid, 3), "reflectance_offset": off})
        stack.append(ndvi)
    if not accepted:
        return result(UNAVAILABLE, reason="No sufficiently cloud-free Sentinel-2 observations of the site; no vegetation values were produced.",
                      details={"excluded_scenes": excluded}, failures=failures)
    comp = np.nanmedian(np.stack(stack), axis=0)
    comp[~study_mask] = np.nan
    v = ~np.isnan(comp)
    site_vals = comp[site_mask & v]
    ring_vals = comp[study_mask & ~site_mask & v]
    tif = save_geotiff(ctx.out_dir / "ndvi_composite.tif", comp, transform, local, "Median NDVI, Sentinel-2 L2A, SCL-masked")
    ds_id = catalog.register(
        ctx.project["id"], PROVIDER, "satellite_ndvi_composite", acquisition_method="catalog_stac",
        request_params={"collection": "sentinel-2-l2a", "period": [start_d.isoformat(), end_d.isoformat()]}, processed_path=tif,
        spatial_coverage=mapping(study), time_coverage={"scenes": [a["datetime"] for a in accepted]}, spatial_resolution="10 m",
        variables=["ndvi"], units={"ndvi": "unitless"}, crs=local, scene_ids=[a["scene_id"] for a in accepted],
        quality={"accepted": len(accepted), "excluded": len(excluded), "mask": f"SCL classes {sorted(SCL_MASK)}"},
        provenance=["STAC discovery", "Windowed COG read", "Reprojected to local UTM 10 m (nearest)", "BOA offset by processing baseline", "SCL mask", "NDVI", "Per-pixel median"],
        product_id="Sentinel-2 L2A B04/B08/SCL", data_nature="observed",
    )
    src = [ds_id]
    q = {"scenes": len(accepted), "dates": [a["datetime"][:10] for a in accepted], "valid_site_pixels": int(site_vals.size)}
    m = [
        V.metric("ndvi_site_mean", "Mean NDVI on the site", round(float(np.mean(site_vals)), 3), "NDVI", output_kind=V.OBSERVED, method="Mean of median-composite NDVI over valid site pixels", sources=src, quality=q),
        V.metric("ndvi_site_vegetated_share_pct", f"Share of site with vegetation indicator (NDVI ≥ {VEG_THRESHOLD})", round(float(np.mean(site_vals >= VEG_THRESHOLD) * 100), 1), "%", output_kind=V.CLASSIFIED,
                 method=f"Valid site pixels with NDVI ≥ {VEG_THRESHOLD} ÷ valid site pixels", sources=src, quality=q),
    ]
    if ring_vals.size:
        m.append(V.metric("ndvi_surroundings_mean", f"Mean NDVI in surroundings (up to {buffer_m:.0f} m)", round(float(np.mean(ring_vals)), 3), "NDVI", output_kind=V.OBSERVED, method="Mean over valid ring pixels", sources=src, quality=q))
    details = {"site_basis": basis, "period": [start_d.isoformat(), end_d.isoformat()], "accepted_scenes": accepted, "excluded_scenes": excluded,
               "attribution": "Contains modified Copernicus Sentinel data; accessed via Microsoft Planetary Computer"}
    # Planned vs observed green space (FR-20): only where the scenario defines green space.
    greens = [geo.to_local(shape(f["geometry"]), local) for f in (ctx.scenario or {}).get("features", {}).get("features", [])
              if f["properties"].get("feature_type") == "green_space" and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
    if greens:
        gmask = mask_for(unary_union(greens), transform, shape_) & v
        if gmask.any():
            share = float(np.mean(comp[gmask] >= VEG_THRESHOLD) * 100)
            m.append(V.metric("ndvi_planned_green_currently_vegetated_pct", "Share of planned green space currently showing vegetation (NDVI ≥ 0.2)", round(share, 1), "%", output_kind=V.CLASSIFIED,
                              method="Valid pixels inside planned green-space polygons with NDVI ≥ 0.2", sources=src, quality=q))
            details["planned_vs_observed_note"] = "A planned park may not yet exist on the ground; a low share is expected for proposed green space."
    ctx.shared["ndvi"] = {"composite": comp, "transform": transform, "crs": local}
    png = save_overlay_png(ctx.out_dir / "ndvi.png", comp, transform, local, "RdYlGn", -0.1, 0.7)
    layers = [layer("Vegetation indicator (NDVI, median)", "raster", V.OBSERVED, str(ctx.out_dir / "ndvi.png"), png,
                    legend={"type": "continuous", "cmap": "RdYlGn", "min": -0.1, "max": 0.7, "unit": "NDVI"}, meta={"geotiff": tif})]
    status = COMPLETED if len(accepted) >= 2 else PARTIAL
    warnings = [] if len(accepted) >= 2 else ["Only one clear scene was usable; vegetation reflects that date only."]
    limitations = [
        "NDVI indicates vegetation-related spectral characteristics; it does not measure tree health, shade quality, irrigation or cooling.",
        "Observed vegetation describes current conditions at the image dates, not the proposed design.",
    ]
    return result(status, metrics=m, layers=layers, datasets=[ds_id], warnings=warnings, limitations=limitations, details=details, failures=failures)
