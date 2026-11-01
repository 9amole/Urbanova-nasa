"""Upload validation and original-file preservation (FR-02 step 1, B.4, B.6, Module 3).

Files are identified from their content, not their extension. Originals are
stored untouched under the project's `originals/` folder; processed outputs are
kept separately. Uploaded content is never executed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import db
from .settings import settings

# The explicit Phase 1 allowlist (Module 3 §"supported formats"). Each category
# lists the content types accepted for it and what the system does with them.
ALLOWLIST: dict[str, dict] = {
    "urban_plan": {
        "types": ["geojson", "dxf", "pdf", "png", "jpeg"],
        "label": "Urban plan / design geometry",
    },
    "planning_document": {"types": ["pdf"], "label": "Planning document"},
    "rate_table": {"types": ["csv"], "label": "Unit-rate table (CSV)"},
    "supplementary_raster": {"types": ["geotiff"], "label": "Supplementary raster dataset (GeoTIFF)"},
}
CAPABILITIES = {
    "geojson": "Geometry extracted automatically (WGS 84, or legacy CRS member / declared EPSG).",
    "dxf": "Closed polylines and lines extracted by layer; requires the drawing's EPSG code to be georeferenced.",
    "pdf": "Planning documents: text extraction and candidate-rule detection. Plan drawings: stored for reference only — automatic geometry extraction from PDF drawings is not supported in this build.",
    "png": "Stored for reference only. Raster plan images are not georeferenced and are not used for area calculations.",
    "jpeg": "Stored for reference only. Raster plan images are not georeferenced and are not used for area calculations.",
    "csv": "Parsed as a unit-rate table with required columns.",
    "geotiff": "Validated (CRS, bounds, no-data) and registered in the dataset catalog.",
}
EXTENSIONS = {
    "geojson": {".geojson", ".json"}, "dxf": {".dxf"}, "pdf": {".pdf"}, "png": {".png"},
    "jpeg": {".jpg", ".jpeg"}, "csv": {".csv"}, "geotiff": {".tif", ".tiff"},
}


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def detect_type(content: bytes) -> str | None:
    head = content[:2048]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "geotiff"
    if head.startswith(b"AutoCAD Binary DXF"):
        return "dxf"
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception:
            return None
    stripped = text.lstrip()
    if re.match(r"^0\s*\r?\n\s*SECTION", stripped) or re.match(r"^999\s*\r?\n", stripped):
        return "dxf"
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and obj.get("type") in {
            "FeatureCollection", "Feature", "Polygon", "MultiPolygon", "Point", "LineString", "MultiLineString", "MultiPoint", "GeometryCollection",
        }:
            return "geojson"
        return None
    first = stripped.splitlines()[0] if stripped else ""
    if "," in first and "\x00" not in text[:2048]:
        return "csv"
    return None


def safe_name(name: str) -> str:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "upload"
    return base[:120]


def validate_upload(filename: str, content: bytes, category: str) -> tuple[str, list[str]]:
    """Return (detected_type, warnings) or raise UploadError with corrective guidance."""
    if category not in ALLOWLIST:
        raise UploadError(f"Unknown upload category '{category}'. Choose one of: {', '.join(ALLOWLIST)}.")
    limit = settings.max_upload_mb * 1024 * 1024
    if len(content) == 0:
        raise UploadError("The file is empty. Check that it was exported correctly and upload it again.")
    if len(content) > limit:
        raise UploadError(f"The file is {len(content) / 1e6:.1f} MB; the limit is {settings.max_upload_mb:.0f} MB.", 413)
    detected = detect_type(content)
    allowed = ALLOWLIST[category]["types"]
    ext = Path(filename or "").suffix.lower()
    if detected is None:
        raise UploadError(
            f"The file content is not a supported format for {ALLOWLIST[category]['label'].lower()} "
            f"(supported: {', '.join(allowed)}). It may be corrupted or in an unsupported format."
        )
    if detected not in allowed:
        raise UploadError(
            f"The file content is {detected.upper()}, which is not accepted as {ALLOWLIST[category]['label'].lower()} "
            f"(accepted: {', '.join(t.upper() for t in allowed)})."
        )
    warnings = []
    if ext and ext not in EXTENSIONS[detected]:
        warnings.append(
            f"The file extension '{ext}' does not match its content ({detected.upper()}). The content type was used."
        )
    return detected, warnings


def store_original(project_id: str, file_id: str, filename: str, content: bytes) -> Path:
    folder = settings.projects_dir / project_id / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}_{safe_name(filename)}"
    path.write_bytes(content)
    return path


def processed_dir(project_id: str) -> Path:
    d = settings.projects_dir / project_id / "processed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs_dir(project_id: str, run_id: str) -> Path:
    d = settings.projects_dir / project_id / "outputs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def register_file(project_id: str, user_id: str, filename: str, declared_type: str | None, content: bytes,
                  category: str, detected: str, warnings: list[str]) -> dict:
    file_id = db.new_id("fil")
    path = store_original(project_id, file_id, filename, content)
    record = {
        "id": file_id, "project_id": project_id, "original_filename": filename,
        "declared_type": declared_type, "detected_type": detected, "file_size": len(content),
        "storage_path": str(path), "category": category, "checksum": hashlib.sha256(content).hexdigest(),
        "status": "uploaded", "validation": {"warnings": warnings, "errors": []},
        "source_metadata": {}, "uploaded_by": user_id, "uploaded_at": db.now(),
    }
    db.insert("files", record)
    return record


def public_file(f: dict) -> dict:
    out = {k: v for k, v in f.items() if k != "storage_path"}
    out["capability"] = CAPABILITIES.get(f.get("detected_type") or "", "")
    return out
