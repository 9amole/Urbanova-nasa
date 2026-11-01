"""Shared analysis context, module result shape and raster output helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import geo

# Module statuses (FR-47): completed, partial (completed with limitations), blocked
# (prerequisite missing), unavailable (data source had nothing usable), failed
# (error), skipped (not selected).
COMPLETED, PARTIAL, BLOCKED, UNAVAILABLE, FAILED, SKIPPED = "completed", "partial", "blocked", "unavailable", "failed", "skipped"


@dataclass
class Context:
    project: dict
    scenario: dict | None
    config: dict
    run_id: str
    out_dir: Path
    shared: dict = field(default_factory=dict)

    @property
    def lat(self) -> float:
        return self.project["latitude"]

    @property
    def lon(self) -> float:
        return self.project["longitude"]

    @property
    def local_crs(self) -> str:
        return geo.utm_crs_for(self.lat, self.lon)

    def site_and_study(self, buffer_m: float):
        return geo.study_area(self.lat, self.lon, self.project.get("boundary"), buffer_m)


def result(status: str, *, metrics: list | None = None, layers: list | None = None, datasets: list | None = None,
           warnings: list | None = None, limitations: list | None = None, details: dict | None = None,
           reason: str | None = None, failures: list | None = None) -> dict:
    return {
        "status": status, "metrics": metrics or [], "layers": layers or [], "datasets": datasets or [],
        "warnings": warnings or [], "limitations": limitations or [], "details": details or {},
        "reason": reason, "provider_failures": failures or [],
    }


def grid_for(geom_local, res: float):
    """A north-up grid in the local CRS covering geom_local at `res` metres."""
    from rasterio.transform import from_origin

    minx, miny, maxx, maxy = geom_local.bounds
    minx, miny = np.floor(minx / res) * res, np.floor(miny / res) * res
    maxx, maxy = np.ceil(maxx / res) * res, np.ceil(maxy / res) * res
    cols, rows = int(round((maxx - minx) / res)), int(round((maxy - miny) / res))
    return from_origin(minx, maxy, res, res), (rows, cols)


def mask_for(geom_local, transform, shape) -> np.ndarray:
    """Boolean mask: True where the pixel centre lies inside the geometry."""
    from rasterio.features import geometry_mask

    return geometry_mask([geom_local.__geo_interface__], out_shape=shape, transform=transform, invert=True, all_touched=False)


def save_geotiff(path: Path, arr: np.ndarray, transform, crs: str, description: str) -> str:
    import rasterio

    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                       crs=crs, transform=transform, nodata=np.nan, compress="deflate") as dst:
        dst.write(arr.astype("float32"), 1)
        dst.set_band_description(1, description)
    return str(path)


def save_overlay_png(path: Path, arr: np.ndarray, transform, crs: str, cmap: str, vmin: float, vmax: float,
                     categorical: dict[int, str] | None = None) -> dict:
    """Reproject to Web Mercator (what Leaflet image overlays assume) and colourise. Returns lat/lon bounds."""
    from PIL import Image
    from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds

    rows, cols = arr.shape
    left, top = transform.c, transform.f
    right, bottom = left + cols * transform.a, top + rows * transform.e
    dst_t, w, h = calculate_default_transform(crs, "EPSG:3857", cols, rows, left, bottom, right, top)
    merc = np.full((h, w), np.nan, dtype="float32")
    reproject(arr.astype("float32"), merc, src_transform=transform, src_crs=crs, dst_transform=dst_t, dst_crs="EPSG:3857",
              src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.nearest)
    rgba = np.zeros((h, w, 4), dtype="uint8")
    valid = ~np.isnan(merc)
    if categorical:
        for val, hex_color in categorical.items():
            sel = valid & (merc == val)
            rgba[sel] = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)] + [200]
    else:
        import matplotlib

        cm = matplotlib.colormaps[cmap]
        norm = np.clip((merc - vmin) / (vmax - vmin if vmax > vmin else 1), 0, 1)
        colors = (cm(np.nan_to_num(norm)) * 255).astype("uint8")
        rgba[valid] = colors[valid]
        rgba[valid, 3] = 190
    Image.fromarray(rgba).save(path)  # (h, w, 4) uint8 → RGBA
    mb = (dst_t.c, dst_t.f + h * dst_t.e, dst_t.c + w * dst_t.a, dst_t.f)
    west, south, east, north = transform_bounds("EPSG:3857", "EPSG:4326", *mb)
    return {"south": south, "west": west, "north": north, "east": east}


def save_geojson(path: Path, fc: dict) -> str:
    path.write_text(json.dumps(fc), encoding="utf-8")
    return str(path)


def layer(name: str, kind: str, output_kind: str, path: str, bounds: dict | None = None, legend: Any = None, meta: dict | None = None) -> dict:
    return {"name": name, "layer_kind": kind, "output_kind": output_kind, "path": path, "bounds": bounds, "legend": legend, "meta": meta or {}}


def pct(values: np.ndarray, q: float) -> float:
    return float(np.nanpercentile(values, q))
