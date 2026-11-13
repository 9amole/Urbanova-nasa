"""Site trends inside the project boundary since 1984 (A1c, NASA Space Apps 2026: "Be An Earth System Trend Detective").

Is this plot heating up, is it heating faster than its surroundings, and is it losing green cover? Landsat 5, 7, 8 and 9
Collection 2 Level-2 (30 m) from the Planetary Computer: for every year, the clearest hot-season scenes give the surface
temperature inside the site and in the ring around it, and the site's greenness (NDVI). The yearly values go through the
same validated tests as the Trend Detective (urbanova.trends.analyse_yearly): Sen's slope with its 95 % range and a verdict
that needs both persistence-corrected tests. Per pixel, the same tests with a Benjamini-Hochberg false discovery rate map
where on and around the site the change is significant.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .. import catalog, geo, trends
from .. import values as V
from ..providers.base import ProviderError, availability
from ..providers.satellite import item_summary, read_to_grid, search
from ..settings import settings
from .common import (BLOCKED, COMPLETED, PARTIAL, UNAVAILABLE, Context, grid_for, layer, mask_for, result, save_geojson, save_geotiff,
                     save_overlay_png)
from .surface_heat import OFFSET, QA_MASK_BITS, SCALE, hot_months

PROVIDER = "pc_landsat_c2_l2"
PLATFORMS = ["landsat-5", "landsat-7", "landsat-8", "landsat-9"]
FIRST_YEAR = 1984
SENSOR_SWITCH = 2013  # Landsat 8 (new thermal sensor) from 2013; earlier years are Landsat 5 and 7
SR_SCALE, SR_OFFSET = 0.0000275, -0.2
WORKERS = 6
FDR_Q = 0.10
MAX_PIXELS = 40000  # per-pixel tests above this are run on 60 m blocks
ROWS = {
    "lst": ("Site surface temperature, hot season", "°C", "warming", "cooling", "site_trend_lst_per_decade"),
    "diff": ("Site minus surroundings, surface temperature", "K", "heating faster than its surroundings", "heating slower than its surroundings",
             "site_trend_lst_minus_surroundings_per_decade"),
    "ndvi": ("Site greenness (NDVI)", "NDVI", "getting greener", "losing green cover", "site_trend_ndvi_per_decade"),
}


def parallel(fn, items: list) -> list:
    """Map fn over items on WORKERS threads. Each call runs in a copy of the caller's context, so provenance goes to the
    active recorder (urbanova.recording uses a ContextVar, which new threads do not inherit): the engine stays database-free."""
    base = contextvars.copy_context()
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda x: base.copy().run(fn, x), items))


def platform_of(item: dict) -> str:
    return (item.get("properties") or {}).get("platform") or ""


def thermal_asset(item: dict) -> str:
    return "lwir11" if "lwir11" in item["assets"] else "lwir"


def _grid_key(transform, shape_, crs) -> str:
    return hashlib.sha1(json.dumps([list(transform)[:6], list(shape_), crs]).encode()).hexdigest()[:12]


def read_scene(item: dict, crs: str, transform, shape_) -> dict:
    """LST (°C) and NDVI arrays of one scene on the study grid, cloud/shadow/fill masked (QA_PIXEL bits 0-4; the Landsat 8/9-only
    bits are 0 on Landsat 5/7). Cached on disk per scene and grid, so a re-run reads nothing again."""
    summ = item_summary(item)
    cache = settings.cache_dir / "site_trends" / f"{summ['scene_id']}_{_grid_key(transform, shape_, crs)}.npz"
    if cache.exists():
        z = np.load(cache)
        return {"lst": z["lst"], "ndvi": z["ndvi"], "cached": True}
    a = item["assets"]
    qa = read_to_grid(PROVIDER, a["qa_pixel"]["href"], summ["epsg"], crs, transform, shape_, src_nodata=-1)
    st = read_to_grid(PROVIDER, a[thermal_asset(item)]["href"], summ["epsg"], crs, transform, shape_, src_nodata=0)
    red = read_to_grid(PROVIDER, a["red"]["href"], summ["epsg"], crs, transform, shape_, src_nodata=0)
    nir = read_to_grid(PROVIDER, a["nir08"]["href"], summ["epsg"], crs, transform, shape_, src_nodata=0)
    clear = (np.where(np.isnan(qa), 1, qa).astype("uint32") & QA_MASK_BITS) == 0
    lst = st * SCALE + OFFSET - 273.15
    lst[~clear | np.isnan(st) | (lst < -30) | (lst > 80)] = np.nan
    r, n = red * SR_SCALE + SR_OFFSET, nir * SR_SCALE + SR_OFFSET
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (n - r) / (n + r)
    ndvi[~clear | np.isnan(r) | np.isnan(n) | (np.abs(ndvi) > 1)] = np.nan
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, lst=lst.astype("float32"), ndvi=ndvi.astype("float32"))
    return {"lst": lst, "ndvi": ndvi, "cached": False}


def _search_year(year: int, geom: dict, months: list[int], max_cloud: float) -> list[dict]:
    items = search(PROVIDER, geom, f"{year}-01-01", f"{year}-12-31", max_cloud=max_cloud, limit=100, extra_query={"platform": {"in": PLATFORMS}})
    return [it for it in items if int(it["properties"]["datetime"][5:7]) in months]


def _year(year: int, items: list[dict], grid: tuple, masks: dict, per_year: int, min_valid: float) -> dict:
    """The clearest usable scenes of one year (at most per_year), each needing min_valid of the site clear."""
    crs, transform, shape_ = grid
    accepted, excluded, failures, lst_stack, ndvi_stack = [], [], [], [], []
    for it in items:
        if len(accepted) >= per_year:
            break
        summ = item_summary(it)
        try:
            sc = read_scene(it, crs, transform, shape_)
        except ProviderError as exc:
            failures.append({**exc.as_dict(), "scene_id": summ["scene_id"]})
            excluded.append({**summ, "reason": f"read failed ({exc.kind})"})
            continue
        site = masks["site"]
        valid = float(np.mean(~np.isnan(sc["lst"][site]))) if site.any() else 0.0
        if valid < min_valid:
            excluded.append({**summ, "reason": f"only {valid:.0%} of site pixels clear"})
            continue
        lst, ndvi = sc["lst"], sc["ndvi"]
        ring = lst[masks["ring"]]
        rec = {**summ, "valid_site_fraction": round(valid, 3), "site_lst_c": float(np.nanmedian(lst[site])),
               "ring_lst_c": float(np.nanmedian(ring)) if np.any(~np.isnan(ring)) else None,
               "site_ndvi": float(np.nanmedian(ndvi[site])) if np.any(~np.isnan(ndvi[site])) else None, "cached": sc["cached"]}
        rec["diff_k"] = None if rec["ring_lst_c"] is None else rec["site_lst_c"] - rec["ring_lst_c"]
        accepted.append(rec)
        lst_stack.append(lst)
        ndvi_stack.append(ndvi)
    out = {"year": year, "accepted": accepted, "excluded": excluded, "failures": failures}
    if accepted:
        with warnings.catch_warnings():  # all-NaN pixels (cloud in every scene) stay NaN
            warnings.simplefilter("ignore", RuntimeWarning)
            out["lst"] = np.nanmedian(np.stack(lst_stack), axis=0)
            out["ndvi"] = np.nanmedian(np.stack(ndvi_stack), axis=0)
        med = lambda k: float(np.median([a[k] for a in accepted if a[k] is not None])) if any(a[k] is not None for a in accepted) else None  # noqa: E731
        out.update(site_lst_c=med("site_lst_c"), diff_k=med("diff_k"), site_ndvi=med("site_ndvi"),
                   sensors=sorted({a["platform"] for a in accepted}))
    return out


def sensor_check(yearly: list[tuple[int, float]], unit: str, full: dict) -> dict:
    """The record joins two thermal sensors (Landsat 5/7, then 8/9 from 2013). A sensor change can fake a trend, so the
    trend is also tested on each side: if both sides go the same way as the whole record, the switch does not explain it."""
    parts = []
    for lo, hi in ((None, SENSOR_SWITCH - 1), (SENSOR_SWITCH, None)):
        seg = [(y, v) for y, v in yearly if (lo is None or y >= lo) and (hi is None or y <= hi)]
        r = trends.analyse_yearly(seg, unit) if len(seg) >= trends.MIN_YEARS else None
        parts.append({"from": seg[0][0] if seg else None, "to": seg[-1][0] if seg else None, "years": len(seg),
                      "per_decade": r["rate"]["per_decade"]["slope"] if r else None, "level": r["verdict"]["level"] if r else None})
    s = full["rate"]["per_decade"]["slope"]
    tested = [p for p in parts if p["per_decade"] is not None]
    consistent = len(tested) == 2 and all(p["per_decade"] * s > 0 for p in tested)
    if len(tested) < 2:
        note = (f"The record joins the Landsat 5/7 and Landsat 8/9 thermal sensors ({SENSOR_SWITCH}); one side has fewer than "
                f"{trends.MIN_YEARS} usable years, so the sensor change could not be tested separately.")
    elif consistent:
        note = f"Sensor check passed: the trend goes the same way before and after the switch to Landsat 8 ({SENSOR_SWITCH}), so the sensor change does not explain it."
    else:
        note = (f"Caution: the trend is not the same before and after the switch to Landsat 8 ({SENSOR_SWITCH}); part of it may come from the "
                "change of sensor. The site-minus-surroundings row is the most robust to this.")
    return {"switch_year": SENSOR_SWITCH, "segments": parts, "consistent": consistent, "note": note}


def step_change(yearly: list[tuple[int, float]], res: dict, unit: str) -> dict | None:
    """Building, clearing or irrigating a plot changes it in a step, not a steady trend. When the Pettitt test finds a
    significant shift, report the averages before and after it, so a real step is not hidden behind 'no steady trend'."""
    cp = res["change_point"]
    if not cp["significant"]:
        return None
    before = [v for y, v in yearly if y < cp["year"]]
    after = [v for y, v in yearly if y >= cp["year"]]
    if len(before) < 3 or len(after) < 3:
        return None
    b, a = float(np.mean(before)), float(np.mean(after))
    p = cp["p_value"]
    return {"year": cp["year"], "before": b, "after": a, "p_value": p,
            "note": (f"Step change around {cp['year']} (Pettitt test, p {'< 0.001' if p < 0.001 else f'= {p:.3f}'}): on average {b:.3g} {unit} "
                     f"before and {a:.3g} {unit} after ({a - b:+.3g} {unit}). A step, such as construction, clearing or new irrigation, is a "
                     "real change even when the tests find no steady trend.")}


def pixel_trends(years: list[int], stack: np.ndarray, study: np.ndarray) -> dict:
    """Per-pixel Sen's slope (per decade) and corrected Mann-Kendall p, with a Benjamini-Hochberg FDR at q = FDR_Q
    (as for climate maps, Wilks 2016). Pixels need MIN_YEARS valid years."""
    t = np.array([y + 0.5 for y in years])
    slope = np.full(stack.shape[1:], np.nan, dtype="float32")
    pv = np.full(stack.shape[1:], np.nan)
    idx = [(i, j) for i, j in zip(*np.nonzero(study)) if np.sum(~np.isnan(stack[:, i, j])) >= trends.MIN_YEARS]
    for i, j in idx:
        v = stack[:, i, j]
        ok = ~np.isnan(v)
        mk = trends.mann_kendall_corrected(t[ok], v[ok].astype(float))
        slope[i, j] = trends.sens_slope(t[ok], v[ok].astype(float), mk["var_S"])["slope"] * 10
        pv[i, j] = mk["p_value"]
    sig = np.zeros(stack.shape[1:], dtype=bool)
    if idx:
        flags = trends.benjamini_hochberg([pv[i, j] for i, j in idx], q=FDR_Q)
        for (i, j), f in zip(idx, flags):
            sig[i, j] = f
    return {"slope": slope, "significant": sig, "tested": len(idx), "n_significant": int(sig.sum())}


def _outline(mask: np.ndarray, transform, crs: str):
    from rasterio.features import shapes

    polys = [shape(g) for g, v in shapes(mask.astype("uint8"), mask=mask, transform=transform) if v == 1]
    return geo.to_wgs84(unary_union(polys), crs) if polys else None


def run(ctx: Context) -> dict:
    cfg = ctx.config
    buffer_m = float(cfg.get("lst_buffer_m", 1000))
    per_year = int(cfg.get("site_trend_scenes_per_year", 2))
    min_valid = float(cfg.get("min_valid_site_fraction", 0.6))
    max_cloud = float(cfg.get("max_scene_cloud_pct", 40))
    usable, kind, why = availability(PROVIDER)
    if not usable:
        return result(BLOCKED, reason=why, failures=[{"provider": PROVIDER, "kind": kind, "message": why}])
    months, month_basis = hot_months(ctx)
    site, study, site_basis = ctx.site_and_study(buffer_m)
    local = ctx.local_crs
    site_l, study_l = geo.to_local(site, local), geo.to_local(study, local)
    ring_l = study_l.difference(site_l.buffer(60))
    transform, shape_ = grid_for(study_l, 30)
    masks = {"site": mask_for(site_l, transform, shape_), "ring": mask_for(ring_l, transform, shape_), "study": mask_for(study_l, transform, shape_)}
    end_year = date.today().year - 1
    years = list(range(FIRST_YEAR, end_year + 1))
    geom = mapping(site.centroid)
    if ctx.progress:
        ctx.progress(f"Site trends: searching the Landsat archive {FIRST_YEAR}–{end_year}")
    failures = []
    found = parallel(lambda y: _safe_search(y, geom, months, max_cloud), years)
    search_fail = [f for _, f in found if f]
    if len(search_fail) == len(years):
        return result(UNAVAILABLE, reason=f"The Landsat archive search failed ({search_fail[0]['kind']}).", failures=search_fail[:3])
    failures += search_fail[:3]
    if ctx.progress:
        ctx.progress(f"Site trends: reading clear hot-season scenes for {sum(1 for items, _ in found if items)} years")
    per = parallel(lambda yi: _year(yi[0], yi[1][0], (local, transform, shape_), masks, per_year, min_valid), list(zip(years, found)))
    used = [p for p in per if p["accepted"]]
    for p in per:
        failures += p["failures"]
    coverage = [{"year": p["year"], "scenes": len(p["accepted"]), "sensors": p.get("sensors", []),
                 "excluded": len(p["excluded"]), "dates": [a["datetime"][:10] for a in p["accepted"]]} for p in per]
    if len(used) < trends.MIN_YEARS:
        read_fail = sum(1 for p in per for x in p["excluded"] if str(x.get("reason", "")).startswith("read failed"))
        busy = (f" {read_fail} scene read(s) failed, so the satellite archive was probably busy or unreachable: run the trend detective again "
                "in a few minutes (scenes already read are kept).") if read_fail else ""
        return result(UNAVAILABLE, reason=(f"Only {len(used)} year(s) since {FIRST_YEAR} have a usable hot-season Landsat view of the site; "
                                           f"a trend needs at least {trends.MIN_YEARS}.{busy}"), details={"coverage": coverage, "months": months}, failures=failures[:10])
    scenes = [a for p in used for a in p["accepted"]]
    ds_id = catalog.register(
        ctx.project["id"], PROVIDER, "satellite_lst_ndvi_time_series", acquisition_method="catalog_stac",
        request_params={"collection": "landsat-c2-l2", "platforms": PLATFORMS, "months": months, "years": [FIRST_YEAR, end_year],
                        "max_scene_cloud_pct": max_cloud, "scenes_per_year": per_year},
        spatial_coverage=mapping(study), time_coverage={"scenes": [a["datetime"] for a in scenes]},
        spatial_resolution="30 m grid (thermal native 100 m Landsat 8/9, 120 m Landsat 5, 60 m Landsat 7)", temporal_resolution="per overpass; yearly median",
        variables=["lst_c", "ndvi"], units={"lst_c": "°C", "ndvi": "1"}, crs=local, scene_ids=[a["scene_id"] for a in scenes],
        quality={"years_used": len(used), "scenes": len(scenes), "mask": "QA_PIXEL bits 0–4", "min_valid_site_fraction": min_valid},
        provenance=["STAC discovery per year (hot months)", "Windowed COG reads", "Reprojected to local UTM 30 m (nearest)", "QA mask",
                    f"ST scale {SCALE}, offset {OFFSET} K → °C", f"SR scale {SR_SCALE}, offset {SR_OFFSET}", "NDVI = (NIR − red)/(NIR + red)",
                    "Per-year median of the accepted scenes", "Trend tests: urbanova.trends.analyse_yearly"],
        product_id="Landsat Collection 2 Level-2 (ST and SR)", data_nature="observed",
    )
    src = [ds_id]
    rows, metrics, warnings = [], [], []
    for key, (name, unit, up, down, metric_name) in ROWS.items():
        field = {"lst": "site_lst_c", "diff": "diff_k", "ndvi": "site_ndvi"}[key]
        ann = [(p["year"], p[field]) for p in used if p.get(field) is not None]
        pts = [(a["datetime"][:10], a[field]) for p in used for a in p["accepted"] if a.get(field) is not None]
        res = trends.yearly_result(ann, pts, unit, up_word=up, down_word=down)
        label = f"Trend in {name[0].lower() + name[1:]} since {ann[0][0] if ann else FIRST_YEAR}"
        if not res.get("ok"):
            metrics.append(V.missing(metric_name, label, f"{unit}/decade", V.UNKNOWN, res["reason"], sources=src))
            warnings.append(f"{name}: {res['reason']}")
            continue
        res["notes"].append((sc := sensor_check(ann, unit, res))["note"])
        step = step_change(ann, res, unit)
        if step:
            res["notes"].insert(0, step["note"])
            if res["verdict"]["level"] != "significant":  # a longer record would not help: the change is a step, not a slope
                res["verdict"]["why"] = (f"The change came as a step around {step['year']} ({step['after'] - step['before']:+.3g} {unit}), not as a steady "
                                         "trend, so the trend tests do not capture it; see the step-change note below.")
        rate, verdict = res["rate"]["per_decade"], res["verdict"]
        metrics.append(V.metric(
            metric_name, label, round(rate["slope"], 4), f"{unit}/decade", output_kind=V.CALCULATED, sources=src,
            method=(f"Sen's slope of yearly medians of clear hot-season Landsat scenes, {res['period']['first_year']}–{res['period']['last_year']} "
                    f"({res['period']['years']} years); verdict {verdict['level'].replace('_', ' ')} (needs both corrected tests at p < 0.05): {verdict['tests']}"),
            uncertainty={"ci95_low": round(rate["ci_low"], 4), "ci95_high": round(rate["ci_high"], 4), "method": "Sen's slope 95 % range from the corrected Mann-Kendall variance"},
            quality={"verdict": verdict["level"], "years": res["period"]["years"], "missing_years": res["period"]["missing_years"], "scenes": len(pts),
                     "sensor_check_consistent": sc["consistent"], "p_mann_kendall_corrected": res["mann_kendall"]["p_value"], "p_t_test_corrected": res["ols"]["p_value"]},
            extra={"verdict": verdict["level"], "direction": verdict["direction"], "total_change": round(res["rate"]["total_change"], 4)},
        ))
        rows.append({"id": key, "name": name, "unit": unit, "verdict": verdict, "per_decade": rate, "total_change": res["rate"]["total_change"],
                     "period": res["period"], "p_mann_kendall": res["mann_kendall"]["p_value"], "p_mann_kendall_plain": res["mann_kendall"]["p_value_plain"],
                     "p_t_test": res["ols"]["p_value"], "p_t_test_plain": res["ols"]["p_value_plain"], "effective_years": res["ols"]["n_eff"],
                     "change_point": res["change_point"], "series": res["series"], "deseasonalised": False, "annual": res["annual"],
                     "annual_raw": res["annual_raw"], "trend_line": res["trend_line"], "notes": res["notes"], "sensor_check": sc, "step": step,
                     "metric_name": metric_name, "integrity": {"complete_pct": round(100 * res["period"]["years"] / len(years), 1)}})
    if not rows:
        return result(UNAVAILABLE, metrics=metrics, datasets=src, warnings=warnings, reason="No site trend could be tested.", details={"coverage": coverage})

    # Where on and around the site: per-pixel trend maps.
    if ctx.progress:
        ctx.progress("Site trends: testing every pixel (false discovery rate)")
    yrs = [p["year"] for p in used]
    layers, maps = [], {}
    for key, title, unit, cmap in (("lst", "Surface temperature change", "°C per decade", "RdBu_r"), ("ndvi", "Greenness (NDVI) change", "NDVI per decade", "BrBG")):
        stack = np.stack([p[key] for p in used]).astype("float32")
        study = masks["study"]
        if study.sum() > MAX_PIXELS:
            stack, study = stack[:, ::2, ::2], study[::2, ::2]  # 60 m sampling keeps the per-pixel tests quick on large sites
            tr = transform * transform.scale(2, 2)
        else:
            tr = transform
        pt = pixel_trends(yrs, stack, study)
        sl = pt["slope"]
        if not np.any(~np.isnan(sl)):
            continue
        lim = float(np.nanpercentile(np.abs(sl), 98)) or 1e-6
        tif = save_geotiff(ctx.out_dir / f"site_trend_{key}.tif", sl, tr, local, f"{title} ({unit}), Sen's slope of yearly Landsat composites")
        png = save_overlay_png(ctx.out_dir / f"site_trend_{key}.png", sl, tr, local, cmap, -lim, lim)
        layers.append(layer(f"{title} since {yrs[0]} ({unit})", "raster", V.CALCULATED, str(ctx.out_dir / f"site_trend_{key}.png"), png,
                            legend={"type": "continuous", "cmap": cmap, "min": round(-lim, 4), "max": round(lim, 4), "unit": unit}, meta={"geotiff": tif}))
        outline = _outline(pt["significant"], tr, local)
        if outline is not None:
            fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(outline),
                                                             "properties": {"label": f"{title}: significant (FDR q = {FDR_Q:g})"}}]}
            layers.append(layer(f"{title}: significant pixels (FDR q = {FDR_Q:g})", "vector", V.CALCULATED,
                                save_geojson(ctx.out_dir / f"site_trend_{key}_significant.geojson", fc),
                                legend={"type": "categorical", "classes": [{"color": "#222222", "label": f"Significant change (corrected Mann-Kendall, FDR q = {FDR_Q:g})"}]}))
        site_px = masks["site"][::2, ::2] if study.shape != masks["site"].shape else masks["site"]
        maps[key] = {"tested_pixels": pt["tested"], "significant_pixels": pt["n_significant"],
                     "site_significant_share_pct": round(100 * float(np.mean(pt["significant"][site_px])) if site_px.any() else 0.0, 1),
                     "pixel_m": 30 if tr is transform else 60}
    ctx.shared["site_trends"] = {r["id"]: {"level": r["verdict"]["level"], "per_decade": r["per_decade"]["slope"]} for r in rows}
    details = {"rows": rows, "coverage": coverage, "months": months, "month_basis": month_basis, "site_basis": site_basis, "first_year": yrs[0],
               "last_year": yrs[-1], "years_used": len(used), "years_possible": len(years), "scenes": len(scenes), "maps": maps,
               "cached_scenes": sum(1 for a in scenes if a["cached"]), "accepted_scenes": [{k: a[k] for k in ("scene_id", "datetime", "platform", "cloud_cover_pct", "valid_site_fraction")} for a in scenes],
               "excluded_scenes": [{k: x.get(k) for k in ("scene_id", "datetime", "platform", "cloud_cover_pct", "reason")} for p in per for x in p["excluded"]],
               "attribution": "Landsat Collection 2 courtesy of the U.S. Geological Survey and NASA, via Microsoft Planetary Computer"}
    limitations = [
        "Land surface temperature (the ground and roofs), not air temperature.",
        "One or two clear-sky overpasses per year, so the weather of single days adds noise; the site-minus-surroundings row cancels most of it.",
        f"The thermal sensors changed (Landsat 5/7 to Landsat 8/9 in {SENSOR_SWITCH}); each trend is checked on both sides of the switch.",
        "Years without a clear hot-season view of the site are left out, never filled.",
        "A trend describes the past; it is not a forecast.",
    ]
    if site_basis != "site boundary":
        limitations.insert(0, "No site boundary: the 'site' is a buffer around the project point. Add the boundary for a trend of the plot itself.")
    status = PARTIAL if warnings or failures else COMPLETED
    if failures:
        warnings.append(f"{len(failures)} scene read(s) or search(es) failed and were skipped.")
    return result(status, metrics=metrics, layers=layers, datasets=src, warnings=warnings, limitations=limitations, details=details, failures=failures[:10])


def _safe_search(year: int, geom: dict, months: list[int], max_cloud: float) -> tuple[list[dict], dict | None]:
    try:
        return _search_year(year, geom, months, max_cloud), None
    except ProviderError as exc:
        return [], exc.as_dict()
