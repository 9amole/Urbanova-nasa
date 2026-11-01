"""Internal dataset catalog and provenance (FR-39, A.7, B.5)."""

from __future__ import annotations

from typing import Any

from . import db
from .providers.base import registry


def register(project_id: str | None, source_id: str, dataset_type: str, *, acquisition_method: str,
             request_params: Any = None, cache_key: str | None = None, raw_path: str | None = None,
             processed_path: str | None = None, spatial_coverage: Any = None, time_coverage: Any = None,
             spatial_resolution: str | None = None, temporal_resolution: str | None = None,
             variables: Any = None, units: Any = None, crs: str | None = None, quality: Any = None,
             provenance: Any = None, parent_ids: list[str] | None = None, scene_ids: list[str] | None = None,
             product_id: str | None = None, original_filename: str | None = None, checksum: str | None = None,
             data_nature: str | None = None, license_: str | None = None, attribution: str | None = None) -> str:
    prov = registry().get(source_id, {})
    ds_id = db.new_id("dst")
    db.insert("datasets", {
        "id": ds_id, "project_id": project_id, "source_id": source_id, "dataset_type": dataset_type,
        "data_nature": data_nature or prov.get("data_nature"), "product_id": product_id,
        "acquisition_method": acquisition_method, "request_params": request_params, "cache_key": cache_key,
        "raw_path": raw_path, "processed_path": processed_path, "original_filename": original_filename,
        "checksum": checksum, "spatial_coverage": spatial_coverage, "time_coverage": time_coverage,
        "spatial_resolution": spatial_resolution or prov.get("spatial_resolution"),
        "temporal_resolution": temporal_resolution or prov.get("temporal_resolution"),
        "variables": variables, "units": units, "crs": crs,
        "license": license_ or prov.get("license"), "attribution": attribution or prov.get("attribution"),
        "quality": quality or {}, "provenance": provenance or [], "parent_ids": parent_ids or [],
        "scene_ids": scene_ids or [], "created_at": db.now(),
    })
    return ds_id


def public_dataset(d: dict) -> dict:
    out = dict(d)
    out.pop("raw_path", None)
    out.pop("processed_path", None)
    return out
