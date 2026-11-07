"""Land surface temperature analysis from Landsat Collection 2 Level-2 (FR-18). Heat Analysis component.

Method (documented in docs/METHODOLOGY.md):
1. Discover Landsat 8/9 scenes over the site in the hottest months of recent years.
2. Read ST_B10 (lwir11) and QA_PIXEL on a 30 m grid in the local UTM zone.
3. Mask fill, dilated cloud, cirrus, cloud and cloud-shadow pixels (QA bits 0–4).
4. Scale: LST[K] = DN × 0.00341802 + 149.0; convert to °C; drop values outside −30…80 °C.
5. Accept a scene only if ≥ 60% of site pixels are valid; composite = per-pixel median.
6. Compare the site with a surrounding ring and classify pixels relative to the
   study-area distribution (P25/P75/P90). No absolute "hot" threshold is used.
7. Statistical position of the site: the ring is aggregated to 90 m blocks (3 × 3 pixels, close to
   the 100 m native thermal resolution) and the site's mean is placed among the block means
   (percentile and standardised difference).
"""

from __future__ import annotations

from datetime import date

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .. import basis, catalog, geo
from .. import values as V
from ..providers.base import ProviderError, availability
from ..providers.satellite import item_summary, read_to_grid, search
from .common import BLOCKED, COMPLETED, PARTIAL, UNAVAILABLE, Context, grid_for, layer, mask_for, result, save_geojson, save_geotiff, save_overlay_png

PROVIDER = "pc_landsat_c2_l2"
SCALE, OFFSET = 0.00341802, 149.0
QA_MASK_BITS = 0b11111  # fill, dilated cloud, cirrus, cloud, cloud shadow
CLASS_COLORS = {1: "#2c7bb6", 2: "#ffffbf", 3: "#fdae61", 4: "#d7191c"}
BLOCK = 3  # 3 × 3 pixels of 30 m = 90 m blocks (basis: surface_heat.block_size_m)
MIN_RING_BLOCKS = 10  # basis: surface_heat.min_ring_blocks
CLASS_LABELS = {1: "Cooler than study-area P25", 2: "Typical (P25–P75)", 3: "Warmer (P75–P90)", 4: "Hottest decile (≥ P90)"}


def hot_months(ctx: Context) -> tuple[list[int], str]:
    months = ctx.config.get("hot_months") or ctx.shared.get("hottest_months")
    if months:
        basis = "the three warmest months in the climate baseline" if not ctx.config.get("hot_months") else "user-selected months"
        return sorted(months), basis
    return ([6, 7, 8] if ctx.lat >= 0 else [12, 1, 2]), "hemisphere default summer (climate baseline unavailable)"


def mid_rank(ref_sorted: np.ndarray, x) -> np.ndarray:
    """Mid-rank percentile (0–1) of x within sorted ref: values tied with x count half."""
    return (np.searchsorted(ref_sorted, x, "left") + np.searchsorted(ref_sorted, x, "right")) / 2 / ref_sorted.size


def site_position(comp: np.ndarray, site_mask: np.ndarray, ring_mask: np.ndarray, draws: int = 999) -> dict:
    """Where the site's mean surface temperature sits among the surroundings, at the ~100 m native scale.

    The ring is cut into 90 m blocks (a block counts only if all its pixels are valid and inside the
    ring). The site covers about n blocks, so it is compared with the means of n blocks drawn from
    the ring (seeded resampling): a large site is judged against averages of the same size, not
    against single blocks. Returns the site mean, the number of ring blocks, the mid-rank percentile
    (ties count half) and the standardised difference (None when the blocks do not vary)."""
    site_vals = comp[site_mask & ~np.isnan(comp)]
    grid = block_grid(comp, ring_mask)
    blocks = grid[~np.isnan(grid)]
    out = {"site_mean_c": float(site_vals.mean()) if site_vals.size else None, "ring_blocks": int(blocks.size), "percentile": None, "z": None}
    if out["site_mean_c"] is None or blocks.size < MIN_RING_BLOCKS:
        return out
    s = out["site_mean_c"]
    n = int(min(max(1, round(site_vals.size / BLOCK ** 2)), blocks.size))
    if n == 1:
        null = blocks  # a one-block site is compared with every block
    else:
        rng = np.random.default_rng(0)  # fixed seed: the same data always gives the same answer
        idx = rng.random((draws, blocks.size)).argpartition(n, axis=1)[:, :n]  # n distinct blocks per draw
        null = blocks[idx].mean(axis=1)
    sd = float(blocks.std(ddof=1)) / np.sqrt(n)
    out.update(site_blocks=n, ring_block_mean_c=float(blocks.mean()), percentile=float(100 * mid_rank(np.sort(null), s)),
               z=(s - float(blocks.mean())) / sd if sd > 1e-9 else None)
    return out


def block_grid(comp: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """90 m block means (BLOCK × BLOCK pixels); a block is NaN unless all its pixels are valid and inside `mask`."""
    rows, cols = (comp.shape[0] // BLOCK) * BLOCK, (comp.shape[1] // BLOCK) * BLOCK
    shape4 = (rows // BLOCK, BLOCK, cols // BLOCK, BLOCK)
    full = (mask[:rows, :cols] & ~np.isnan(comp[:rows, :cols])).reshape(shape4).all(axis=(1, 3))
    out = comp[:rows, :cols].reshape(shape4).mean(axis=(1, 3))
    out[~full] = np.nan
    return out


def hot_cluster(comp: np.ndarray, site_mask: np.ndarray, study_mask: np.ndarray) -> dict:
    """H2 site context: Gi* hot clusters of today's surface temperature at the 90 m block scale.

    Returns the significant-block grid, the site blocks (blocks touching the site) and the share of
    valid site blocks inside a significant hot cluster (None when no site block is valid)."""
    from . import spatial_stats

    cfg = {k: basis.get(f"surface_heat.h2_{k}")["value"] for k in ("band_blocks", "permutations", "fdr_q")}
    grid = block_grid(comp, study_mask)
    rows, cols = grid.shape[0] * BLOCK, grid.shape[1] * BLOCK
    site_blocks = site_mask[:rows, :cols].reshape(grid.shape[0], BLOCK, grid.shape[1], BLOCK).any(axis=(1, 3))
    out = {"blocks": int((~np.isnan(grid)).sum()), "site_blocks": site_blocks, "share_pct": None, "significant": None, **cfg}
    if out["blocks"] < MIN_RING_BLOCKS:
        return out
    res = spatial_stats.hot_clusters(grid, cfg["band_blocks"], cfg["fdr_q"], perms=int(cfg["permutations"]))
    sv = site_blocks & res["valid"]
    out.update(significant=res["significant"], z=res["z"], cluster_blocks=int(res["significant"].sum()),
               share_pct=float(100 * res["significant"][sv].mean()) if sv.any() else None)
    return out


def _colocate_with_ndvi(ctx: Context, comp, classes, transform, shape_, study_mask, local, src) -> list[dict]:
    """FR-18 step 7: compare heat classes with observed vegetation where both exist (co-location, not causation)."""
    nd = ctx.shared.get("ndvi")
    if not nd:
        return []
    from rasterio.warp import Resampling, reproject

    ndvi30 = np.full(shape_, np.nan, dtype="float32")
    reproject(nd["composite"].astype("float32"), ndvi30, src_transform=nd["transform"], src_crs=nd["crs"], dst_transform=transform, dst_crs=local,
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.average)
    valid = study_mask & ~np.isnan(ndvi30) & ~np.isnan(classes)
    hot, rest = valid & (classes == 4), valid & (classes < 4)
    if hot.sum() < 3 or rest.sum() < 3:
        return []
    note = "Co-location only: different dates and sensors; this does not show that vegetation causes the temperature difference."
    q = {"note": note, "pixels_hottest": int(hot.sum()), "pixels_other": int(rest.sum())}
    return [
        V.metric("lst_ndvi_mean_in_hottest_decile", "Mean NDVI of study-area pixels in the hottest LST decile", round(float(ndvi30[hot].mean()), 3), "NDVI",
                 method="NDVI composite averaged onto the 30 m LST grid; mean over hottest-decile pixels", sources=src, quality=q),
        V.metric("lst_ndvi_mean_elsewhere", "Mean NDVI of the other study-area pixels", round(float(ndvi30[rest].mean()), 3), "NDVI",
                 method="NDVI composite averaged onto the 30 m LST grid; mean over pixels below P90", sources=src, quality=q),
    ]


def run(ctx: Context) -> dict:
    cfg = ctx.config
    buffer_m = float(cfg.get("lst_buffer_m", 1000))
    years = int(cfg.get("satellite_years", 3))
    max_scenes = int(cfg.get("lst_max_scenes", 6))
    min_valid = float(cfg.get("min_valid_site_fraction", 0.6))
    months, month_basis = hot_months(ctx)
    site, study, site_basis = ctx.site_and_study(buffer_m)
    local = ctx.local_crs
    site_l, study_l = geo.to_local(site, local), geo.to_local(study, local)
    ring_l = study_l.difference(site_l.buffer(60))
    usable, kind, why = availability(PROVIDER)
    if not usable:
        return result(BLOCKED, reason=why, failures=[{"provider": PROVIDER, "kind": kind, "message": why}])
    end_year = date.today().year - 1
    start = f"{end_year - years + 1}-01-01"
    end = f"{end_year}-12-31"
    try:
        items = search(PROVIDER, mapping(site.centroid), start, end, max_cloud=float(cfg.get("max_scene_cloud_pct", 40)), limit=100,
                       extra_query={"platform": {"in": ["landsat-8", "landsat-9"]}})
    except ProviderError as exc:
        return result(UNAVAILABLE, reason=f"Landsat scene search failed ({exc.kind}).", failures=[exc.as_dict()])
    candidates = [it for it in items if int(it["properties"]["datetime"][5:7]) in months]
    if not candidates:
        return result(UNAVAILABLE, reason=f"No Landsat 8/9 scenes with scene cloud cover below the limit were found for months {months} in {start[:4]}–{end_year}.")
    transform, shape_ = grid_for(study_l, 30)
    site_mask, ring_mask, study_mask = mask_for(site_l, transform, shape_), mask_for(ring_l, transform, shape_), mask_for(study_l, transform, shape_)
    accepted, excluded, stack = [], [], []
    failures = []
    for it in candidates:
        if len(accepted) >= max_scenes:
            break
        summ = item_summary(it)
        try:
            qa = read_to_grid(PROVIDER, it["assets"]["qa_pixel"]["href"], summ["epsg"], local, transform, shape_, src_nodata=-1)
            st = read_to_grid(PROVIDER, it["assets"]["lwir11"]["href"], summ["epsg"], local, transform, shape_, src_nodata=0)
        except ProviderError as exc:
            failures.append({**exc.as_dict(), "scene_id": summ["scene_id"]})
            excluded.append({**summ, "reason": f"read failed ({exc.kind})"})
            continue
        qa_int = np.where(np.isnan(qa), 1, qa).astype("uint32")
        clear = (qa_int & QA_MASK_BITS) == 0
        lst = st * SCALE + OFFSET - 273.15
        lst[~clear | np.isnan(st)] = np.nan
        lst[(lst < -30) | (lst > 80)] = np.nan
        valid_site = float(np.mean(~np.isnan(lst[site_mask]))) if site_mask.any() else 0.0
        if valid_site < min_valid:
            excluded.append({**summ, "reason": f"only {valid_site:.0%} of site pixels clear (cloud/shadow/fill)"})
            continue
        accepted.append({**summ, "valid_site_fraction": round(valid_site, 3), "site_median_c": round(float(np.nanmedian(lst[site_mask])), 2),
                         "ring_median_c": round(float(np.nanmedian(lst[ring_mask])), 2) if np.any(~np.isnan(lst[ring_mask])) else None})
        stack.append(lst)
    if not accepted:
        return result(UNAVAILABLE, reason="No usable cloud-free Landsat observations of the site were found; no surface-temperature values were produced.",
                      details={"excluded_scenes": excluded, "months": months}, failures=failures,
                      warnings=["Unsuitable observations were excluded rather than estimated."])
    comp = np.nanmedian(np.stack(stack), axis=0)
    comp[~study_mask] = np.nan
    n_obs = np.sum(~np.isnan(np.stack(stack)), axis=0)
    p25, p75, p90 = (float(np.nanpercentile(comp[study_mask], q)) for q in (25, 75, 90))
    classes = np.full(comp.shape, np.nan, dtype="float32")
    v = ~np.isnan(comp)
    # Mid-rank percentile of each pixel within the study area. Using ranks (not "≥ P90")
    # keeps tied values together: a uniform field is "typical", never "hottest decile".
    ref = np.sort(comp[v])
    rank = mid_rank(ref, comp[v])
    cls = np.select([rank < 0.25, rank < 0.75, rank < 0.90], [1, 2, 3], default=4).astype("float32")
    classes[v] = cls
    site_vals = comp[site_mask & v]
    ring_vals = comp[ring_mask & v]
    tif = save_geotiff(ctx.out_dir / "lst_composite_c.tif", comp, transform, local, "Median land surface temperature (°C), Landsat C2 L2, clear-sky")
    ds_id = catalog.register(
        ctx.project["id"], PROVIDER, "satellite_lst_composite", acquisition_method="catalog_stac",
        request_params={"collection": "landsat-c2-l2", "months": months, "period": [start, end], "max_scene_cloud_pct": cfg.get("max_scene_cloud_pct", 40)},
        processed_path=tif, spatial_coverage=mapping(study), time_coverage={"scenes": [a["datetime"] for a in accepted]},
        spatial_resolution="30 m grid (thermal native 100 m)", temporal_resolution="per overpass; median composite",
        variables=["lst_c"], units={"lst_c": "°C"}, crs=local, scene_ids=[a["scene_id"] for a in accepted],
        quality={"accepted": len(accepted), "excluded": len(excluded), "mask": "QA_PIXEL bits 0–4", "min_valid_site_fraction": min_valid},
        provenance=["STAC discovery", "Windowed COG read", "Reprojected to local UTM 30 m (nearest)", "QA mask", f"Scale {SCALE}, offset {OFFSET} K", "K→°C", "Range check −30…80 °C", "Per-pixel median"],
        product_id="Landsat Collection 2 Level-2 ST_B10", data_nature="observed",
    )
    src = [ds_id]
    q = {"scenes": len(accepted), "dates": [a["datetime"][:10] for a in accepted], "valid_site_pixels": int(site_vals.size), "months": months, "month_basis": month_basis}
    unc = {"scene_spread_site_minus_ring_k": None}
    diffs = [a["site_median_c"] - a["ring_median_c"] for a in accepted if a["ring_median_c"] is not None]
    if diffs:
        unc = {"per_scene_site_minus_ring_k_min": round(min(diffs), 2), "per_scene_site_minus_ring_k_max": round(max(diffs), 2)}
    m = [
        V.metric("lst_site_median_c", "Median land surface temperature on the site (clear-sky, hot season)", round(float(np.median(site_vals)), 2), "°C", output_kind=V.OBSERVED, method="Median of per-pixel median composite inside the site", sources=src, quality=q),
        V.metric("lst_site_p90_c", "90th percentile land surface temperature on the site", round(float(np.percentile(site_vals, 90)), 2), "°C", output_kind=V.OBSERVED, method="P90 of composite pixels inside the site", sources=src, quality=q),
    ]
    if ring_vals.size:
        diff = float(np.median(site_vals) - np.median(ring_vals))
        m.append(V.metric("lst_surroundings_median_c", f"Median land surface temperature in the surrounding ring (up to {buffer_m:.0f} m)", round(float(np.median(ring_vals)), 2), "°C", output_kind=V.OBSERVED, method="Median of composite pixels in the ring around the site (60 m gap excluded)", sources=src, quality=q))
        m.append(V.metric("lst_site_minus_surroundings_k", "Site minus surroundings median surface temperature", round(diff, 2), "K", method="Difference of medians (site − ring)", sources=src, quality=q, uncertainty=unc))
    else:
        m.append(V.missing("lst_site_minus_surroundings_k", "Site minus surroundings median surface temperature", "K", V.UNKNOWN, "No valid pixels in the surrounding ring.", sources=src))
    pos = site_position(comp, site_mask, ring_mask)
    pq = {**q, "ring_blocks_90m": pos["ring_blocks"], "basis": "finding.HEAT-LST-01"}
    p_label = "Site position among surrounding 90 m blocks (percentile of mean surface temperature)"
    z_label = "Site minus surroundings, in standard deviations of the surrounding 90 m blocks"
    if pos["percentile"] is None:
        why = f"Only {pos['ring_blocks']} complete 90 m blocks in the surrounding ring (at least {MIN_RING_BLOCKS} needed)."
        m.append(V.missing("lst_site_percentile_in_surroundings", p_label, "percentile", V.UNKNOWN, why, sources=src))
        m.append(V.missing("lst_site_minus_surroundings_z", z_label, "SD", V.UNKNOWN, why, sources=src))
    else:
        m.append(V.metric("lst_site_percentile_in_surroundings", p_label, round(pos["percentile"], 1), "percentile",
                          method=(f"Share of 999 seeded draws of {pos['site_blocks']} blocks (the site's size) from the {pos['ring_blocks']} complete 90 m ring blocks (3 × 3 composite pixels) whose mean is cooler than the site's mean surface temperature; ties count half"
                                  if pos["site_blocks"] > 1 else f"Share of the {pos['ring_blocks']} complete 90 m ring blocks (3 × 3 composite pixels) cooler than the site's mean surface temperature; ties count half"),
                          sources=src, quality=pq))
        if pos["z"] is None:
            m.append(V.missing("lst_site_minus_surroundings_z", z_label, "SD", V.UNKNOWN, "The surrounding blocks have no temperature variation.", sources=src))
        else:
            m.append(V.metric("lst_site_minus_surroundings_z", z_label, round(pos["z"], 2), "SD",
                              method="(site mean − mean of the 90 m ring blocks) ÷ (standard deviation of the ring blocks ÷ √ site blocks)", sources=src, quality=pq))
    share = float(np.mean(classes[site_mask & v] == 4) * 100)
    m.append(V.metric("lst_site_hottest_decile_share_pct", "Share of site in the study area's hottest decile of surface temperature", round(share, 1), "%", output_kind=V.CLASSIFIED,
                      method="Site pixels whose mid-rank percentile within the study-area composite is ≥ 90 (ties share one rank) ÷ site pixels; expected ≈10% if the site were typical", sources=src, quality=q))
    h2 = hot_cluster(comp, site_mask, study_mask)
    h2_label = "Share of the site's 90 m blocks inside a significant surface hot cluster (Gi*, today's land)"
    h2_method = (f"Getis–Ord Gi* on 90 m block means of the composite, distance band {h2['band_blocks']:g} blocks (cell included), "
                 f"{int(h2['permutations'])} conditional permutations, Benjamini–Hochberg FDR q = {h2['fdr_q']:g}; hot clusters only")
    h2_cluster_geom = None
    h2_why = None
    if h2["share_pct"] is None:
        h2_why = (f"Only {h2['blocks']} complete 90 m blocks in the study area (at least {MIN_RING_BLOCKS} needed)." if h2["significant"] is None
               else "No complete 90 m block covers the site.")
        m.append(V.missing("lst_site_hot_cluster_share_pct", h2_label, "%", V.UNKNOWN, h2_why, sources=src))
    else:
        m.append(V.metric("lst_site_hot_cluster_share_pct", h2_label, round(h2["share_pct"], 1), "%", output_kind=V.CLASSIFIED, method=h2_method, sources=src,
                          quality={**q, "basis": "surface_heat.h2_fdr_q", "study_blocks": h2["blocks"], "cluster_blocks": h2["cluster_blocks"]}))
        if h2["cluster_blocks"]:
            from rasterio import Affine
            from rasterio.features import shapes

            btf = transform @ Affine.scale(BLOCK)
            polys = [shape(g) for g, val in shapes(h2["significant"].astype("uint8"), mask=h2["significant"], transform=btf) if val == 1]
            h2_cluster_geom = mapping(geo.to_wgs84(unary_union(polys), local)) if polys else None
    site_context = {"type": "H2", "condition": V.EXISTING, "metric": "lst_site_hot_cluster_share_pct",
                    "in_cluster": None if h2["share_pct"] is None else h2["share_pct"] > 0, "site_share_pct": h2["share_pct"],
                    "cluster_geometry": h2_cluster_geom, "method": h2_method,
                    "statement": (f"Not assessed: {h2_why}" if h2["share_pct"] is None else
                                  f"The site lies in a statistically significant surface hot cluster of today's land ({h2['share_pct']:.0f} % of its 90 m blocks)."
                                  if h2["share_pct"] > 0 else "The site lies outside any statistically significant surface hot cluster of today's land."),
                    "note": "Site context only: it describes today's land, not the proposed design, and produces no design recommendations."}
    m.append(V.metric("lst_scenes_used", "Clear-sky Landsat scenes used", len(accepted), "count", output_kind=V.OBSERVED, method="Scenes passing the site validity test", sources=src, quality=q))
    from rasterio.features import shapes

    hot_polys = [shape(g) for g, val in shapes((classes == 4).astype("uint8"), mask=(classes == 4) & site_mask, transform=transform) if val == 1]
    hotspot_geom = mapping(geo.to_wgs84(unary_union(hot_polys), local)) if hot_polys else None
    ctx.shared["site_context"] = [site_context]
    ctx.shared["lst"] = {"composite": comp, "transform": transform, "shape": shape_, "site_mask": site_mask, "hot_class": classes, "hotspot_geometry": hotspot_geom}
    lst_png = save_overlay_png(ctx.out_dir / "lst_composite.png", comp, transform, local, "inferno", float(np.nanpercentile(comp, 2)), float(np.nanpercentile(comp, 98)))
    cls_png = save_overlay_png(ctx.out_dir / "lst_classes.png", classes, transform, local, "", 0, 0, categorical=CLASS_COLORS)
    layers = [
        layer("Land surface temperature (°C, clear-sky median)", "raster", V.OBSERVED, str(ctx.out_dir / "lst_composite.png"), lst_png,
              legend={"type": "continuous", "cmap": "inferno", "min": round(float(np.nanpercentile(comp, 2)), 1), "max": round(float(np.nanpercentile(comp, 98)), 1), "unit": "°C"}, meta={"geotiff": tif}),
        layer("Relative surface-heat classes", "raster", V.CLASSIFIED, str(ctx.out_dir / "lst_classes.png"), cls_png,
              legend={"type": "categorical", "classes": [{"color": CLASS_COLORS[k], "label": CLASS_LABELS[k]} for k in CLASS_LABELS],
                      "breaks_c": {"p25": round(p25, 2), "p75": round(p75, 2), "p90": round(p90, 2)}}),
    ]
    if h2_cluster_geom:
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": h2_cluster_geom, "properties": {"label": "Significant hot cluster (Gi*, FDR)"}}]}
        layers.append(layer("Surface hot cluster, today's land (site context)", "vector", V.CLASSIFIED, save_geojson(ctx.out_dir / "lst_hot_cluster.geojson", fc),
                            legend={"type": "categorical", "classes": [{"color": "#b2182b", "label": f"Significant hot cluster (Gi*, FDR q = {h2['fdr_q']:g})"}]}))
    status = COMPLETED if len(accepted) >= 3 else PARTIAL
    warnings = [] if len(accepted) >= 3 else [f"Only {len(accepted)} clear-sky scene(s) were usable; patterns may reflect conditions on those dates only."]
    if excluded:
        warnings.append(f"{len(excluded)} scene(s) were excluded (cloud, shadow or read failure); see details.")
    if "point" in site_basis:
        warnings.append("No site boundary: site statistics use a 100 m radius around the entered point.")
    native_px = site_l.area / (100 * 100)
    if native_px < 9:
        # FR-12: alignment/resolution uncertainty that could materially affect the result.
        status = PARTIAL
        warnings.append(f"The site covers only about {native_px:.1f} native 100 m thermal pixels. Site values are dominated by mixed pixels "
                        "that include surrounding surfaces; treat the site-versus-surroundings comparison as indicative only.")
    m.extend(_colocate_with_ndvi(ctx, comp, classes, transform, shape_, study_mask, local, src))
    limitations = [
        "Land SURFACE temperature at ~10:30 local solar time on clear days; not air temperature or pedestrian thermal comfort.",
        "Thermal band native resolution is 100 m, so features smaller than ~100 m are not resolved even on the 30 m grid.",
        "Relative classes compare pixels within this study area only; they do not establish a causal link between any building and heating.",
        "Observed conditions describe the existing site, not the proposed design.",
    ]
    details = {"site_basis": site_basis, "months": months, "month_basis": month_basis, "period": [start, end], "accepted_scenes": accepted,
               "excluded_scenes": excluded, "breaks_c": {"p25": p25, "p75": p75, "p90": p90}, "hotspot_geometry": hotspot_geom,
               "attribution": "Landsat Collection 2 courtesy of the U.S. Geological Survey; accessed via Microsoft Planetary Computer",
               "max_obs_per_pixel": int(n_obs.max()), "site_context": site_context}
    return result(status, metrics=m, layers=layers, datasets=[ds_id], warnings=warnings, limitations=limitations, details=details, failures=failures)
