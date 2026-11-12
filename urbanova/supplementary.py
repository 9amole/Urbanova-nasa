"""Manual dataset upload route (A.6, B.7 'unknown CRS', 'download-only dataset').

A GeoTIFF is validated (readable, CRS, overlap with the site, no-data) and
registered in the dataset catalog with the uploader's declared source, variable,
units and licence. Phase 1 analyses do not consume uploaded rasters automatically;
they are catalogued for traceability and export (see docs/DECISIONS.md D-08).
"""

from __future__ import annotations

from shapely.geometry import Point, box, mapping

from . import catalog


class SupplementaryError(ValueError):
    pass


def register_raster(project: dict, file_rec: dict, meta: dict) -> dict:
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.warp import transform_bounds

    for key in ("source", "variable", "unit"):
        if not (meta.get(key) or "").strip():
            raise SupplementaryError(f"State the dataset's {key} so it can be traced and interpreted.")
    issues, status = [], "validated"
    try:
        with rasterio.open(file_rec["storage_path"]) as src:
            crs = src.crs.to_string() if src.crs else None
            info = {"width": src.width, "height": src.height, "bands": src.count, "dtype": src.dtypes[0], "nodata": src.nodata,
                    "resolution": [abs(src.transform.a), abs(src.transform.e)]}
            if not crs:
                status = "flagged_unknown_crs"
                issues.append("The raster has no coordinate reference system. It cannot be overlaid or used for area calculations until its CRS is defined.")
                coverage = None
            else:
                b = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                coverage = mapping(box(*b))
                pt = Point(project["longitude"], project["latitude"])
                if not box(*b).buffer(0.01).contains(pt):
                    status = "flagged_no_site_coverage"
                    issues.append("The raster does not cover the project location; it is not usable for this site.")
            if src.nodata is None:
                issues.append("No no-data value is declared; missing pixels cannot be distinguished from real values.")
    except RasterioIOError as exc:
        raise SupplementaryError(f"The GeoTIFF could not be read ({exc}). It may be corrupted.")
    ds = catalog.register(
        project["id"], "manual_upload", "user_raster", acquisition_method="manual_upload", raw_path=file_rec["storage_path"],
        original_filename=file_rec["original_filename"], checksum=file_rec["checksum"], spatial_coverage=coverage,
        time_coverage={"declared": meta.get("observation_date")}, variables=[meta["variable"]], units={meta["variable"]: meta["unit"]},
        crs=crs, quality={"status": status, "issues": issues, **info}, provenance=["Uploaded by user", "Validated: readability, CRS, coverage, no-data"],
        data_nature=meta.get("data_nature") or "user_supplied", license_=meta.get("license") or "not stated", attribution=meta["source"],
    )
    return {"status": status, "dataset_id": ds, "issues": issues, "info": info,
            "message": "Registered in the dataset catalog. Uploaded rasters are not automatically used by Phase 1 analyses."}
