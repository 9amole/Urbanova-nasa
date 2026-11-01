"""STAC discovery and windowed raster reading for Landsat C2 L2 and Sentinel-2 L2A (A.1 method 2, A.3).

Scenes are discovered on the Planetary Computer STAC API, assets are signed with
an anonymous SAS token (never logged), and only the window covering the study
area is read. Every scene is reprojected onto one common grid in the project's
local UTM zone with nearest-neighbour resampling, which is recorded (FR-12).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .base import ProviderError, http_json, provider, redact
from ..settings import settings

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
COLLECTIONS = {"pc_landsat_c2_l2": "landsat-c2-l2", "pc_sentinel2_l2a": "sentinel-2-l2a"}

_tokens: dict[str, tuple[str, float]] = {}


def search(provider_id: str, geometry: dict, start: str, end: str, max_cloud: float, limit: int = 40,
           extra_query: dict | None = None) -> list[dict]:
    provider(provider_id)
    collection = COLLECTIONS[provider_id]
    query = {"eo:cloud_cover": {"lt": max_cloud}}
    if extra_query:
        query.update(extra_query)
    body = {
        "collections": [collection], "intersects": geometry, "datetime": f"{start}/{end}",
        "query": query, "limit": limit, "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
    }
    resp, _ = http_json(provider_id, "POST", f"{STAC_URL}/search", json_body=body)
    if not isinstance(resp, dict) or "features" not in resp:
        raise ProviderError(provider_id, "invalid_response", "STAC search returned no feature list.")
    return resp["features"]


def _token(provider_id: str) -> str:
    collection = COLLECTIONS[provider_id]
    tok = _tokens.get(collection)
    if tok and tok[1] > time.time() + 120:
        return tok[0]
    body, _ = http_json(provider_id, "GET", SAS_URL.format(collection=collection))
    token = body.get("token") if isinstance(body, dict) else None
    if not token:
        raise ProviderError(provider_id, "auth_failed", "No SAS token returned.")
    _tokens[collection] = (token, time.time() + 45 * 60)
    return token


def signed_href(provider_id: str, href: str) -> str:
    return f"{href}?{_token(provider_id)}"


def read_to_grid(provider_id: str, href: str, src_epsg: int, dst_crs: str, dst_transform: Any, dst_shape: tuple[int, int],
                 resampling: str = "nearest", src_nodata: float | None = None) -> np.ndarray:
    """Read the part of a COG covering the destination grid and reproject onto it. Returns float32 with NaN no-data."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    if settings.offline:
        raise ProviderError(provider_id, "offline")
    url = signed_href(provider_id, href)
    rows, cols = dst_shape
    left, top = dst_transform.c, dst_transform.f
    right, bottom = left + cols * dst_transform.a, top + rows * dst_transform.e
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF", GDAL_HTTP_MAX_RETRY="3", GDAL_HTTP_RETRY_DELAY="2", VSI_CACHE="TRUE")
    try:
        with rasterio.Env(**env), rasterio.open(url) as src:
            sb = transform_bounds(dst_crs, src.crs, left, bottom, right, top, densify_pts=21)
            pad = abs(src.transform.a) * 3
            win = from_bounds(sb[0] - pad, sb[1] - pad, sb[2] + pad, sb[3] + pad, src.transform)
            win = win.round_offsets().round_lengths()
            full = rasterio.windows.Window(0, 0, src.width, src.height)
            try:
                win = win.intersection(full)
            except rasterio.errors.WindowError:
                raise ProviderError(provider_id, "not_covered", "Scene does not overlap the study area.")
            data = src.read(1, window=win).astype("float32")
            nod = src_nodata if src_nodata is not None else src.nodata
            if nod is not None:
                data[data == nod] = np.nan
            src_transform = src.window_transform(win)
            dst = np.full(dst_shape, np.nan, dtype="float32")
            reproject(
                data, dst, src_transform=src_transform, src_crs=src.crs, dst_transform=dst_transform, dst_crs=dst_crs,
                src_nodata=np.nan, dst_nodata=np.nan, resampling=getattr(Resampling, resampling),
            )
            return dst
    except ProviderError:
        raise
    except Exception as exc:  # rasterio/GDAL errors: network, auth, corrupt tile
        msg = redact(str(exc))
        kind = "auth_failed" if "403" in msg else "network_error"
        raise ProviderError(provider_id, kind, msg[:300])


def item_summary(item: dict) -> dict:
    p = item.get("properties", {})
    return {
        "scene_id": item.get("id"), "datetime": p.get("datetime"), "cloud_cover_pct": p.get("eo:cloud_cover"),
        "platform": p.get("platform"), "epsg": p.get("proj:epsg"), "processing_baseline": p.get("s2:processing_baseline"),
        "sun_elevation": p.get("view:sun_elevation"),
    }
