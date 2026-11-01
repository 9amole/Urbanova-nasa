"""SQLite persistence.

Phase 1 uses SQLite (stdlib, zero-install) with geometry stored as GeoJSON in
WGS 84. All spatial arithmetic happens in Python (Shapely/pyproj) in a local
projected CRS, so moving to PostgreSQL + PostGIS later only changes this module.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from .settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    password_hash TEXT NOT NULL, failed_logins INTEGER DEFAULT 0, locked_until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
    project_type TEXT, latitude REAL NOT NULL, longitude REAL NOT NULL, boundary TEXT,
    country TEXT, city_or_address TEXT, objectives TEXT, analysis_period TEXT,
    location_confirmed INTEGER DEFAULT 0, location_confirmed_at TEXT,
    verified_site_area_m2 REAL, history TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, original_filename TEXT NOT NULL,
    declared_type TEXT, detected_type TEXT, file_size INTEGER, storage_path TEXT NOT NULL,
    category TEXT NOT NULL, checksum TEXT NOT NULL, status TEXT NOT NULL, validation TEXT,
    source_metadata TEXT, uploaded_by TEXT, uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, file_id TEXT, feature_type TEXT NOT NULL,
    geometry TEXT NOT NULL, original_geometry TEXT NOT NULL, attributes TEXT,
    classification_source TEXT, classification_confidence TEXT, review_status TEXT NOT NULL,
    original_reference TEXT, correction_history TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, file_id TEXT, rule_type TEXT NOT NULL,
    operator TEXT, value REAL, unit TEXT, value_text TEXT, exact_text TEXT, page INTEGER,
    document_name TEXT, issue_date TEXT, applicability TEXT, confidence TEXT, origin TEXT NOT NULL,
    status TEXT NOT NULL, notes TEXT, decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, project_id TEXT, source_id TEXT NOT NULL, dataset_type TEXT NOT NULL,
    data_nature TEXT, product_id TEXT, acquisition_method TEXT NOT NULL, request_params TEXT,
    cache_key TEXT, raw_path TEXT, processed_path TEXT, original_filename TEXT, checksum TEXT,
    spatial_coverage TEXT, time_coverage TEXT, spatial_resolution TEXT, temporal_resolution TEXT,
    variables TEXT, units TEXT, crs TEXT, license TEXT, attribution TEXT, quality TEXT,
    provenance TEXT, parent_ids TEXT, scene_ids TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, url TEXT, status_code INTEGER,
    duration_ms INTEGER, outcome TEXT, attempt INTEGER, cache_hit INTEGER, message TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project_id TEXT, job_type TEXT NOT NULL, status TEXT NOT NULL,
    progress REAL DEFAULT 0, stage TEXT, input TEXT, output TEXT, started_at TEXT, completed_at TEXT,
    error_code TEXT, error_details TEXT, retry_count INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    parent_id TEXT, description TEXT, features TEXT NOT NULL, modifications TEXT,
    status TEXT NOT NULL, design_hash TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario_id TEXT, job_id TEXT,
    configuration TEXT, fingerprint TEXT, input_dataset_ids TEXT, analysis_version TEXT,
    software_version TEXT, status TEXT NOT NULL, module_status TEXT, warnings TEXT,
    started_at TEXT, completed_at TEXT, created_by TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, module TEXT NOT NULL, metric_name TEXT NOT NULL,
    label TEXT, state TEXT NOT NULL, value REAL, unit TEXT, reason TEXT, method TEXT,
    source_dataset_ids TEXT, quality TEXT, uncertainty TEXT, output_kind TEXT,
    design_dependent INTEGER DEFAULT 0, extra TEXT, calculated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS layers (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, module TEXT NOT NULL, name TEXT NOT NULL,
    layer_kind TEXT NOT NULL, output_kind TEXT, path TEXT, bounds TEXT, legend TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, category TEXT NOT NULL, classification TEXT NOT NULL,
    title TEXT NOT NULL, summary TEXT, geometry TEXT, metric_ids TEXT, severity TEXT NOT NULL,
    severity_rule TEXT, rule_id TEXT, threshold TEXT, evidence TEXT, sources TEXT,
    confidence TEXT, limitations TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, finding_id TEXT NOT NULL, title TEXT NOT NULL,
    problem TEXT, evidence TEXT, intervention TEXT, target TEXT, expected_direction TEXT,
    required_inputs TEXT, tradeoffs TEXT, evaluation_method TEXT, review_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparisons (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, baseline_scenario_id TEXT NOT NULL,
    alternative_scenario_ids TEXT NOT NULL, run_ids TEXT NOT NULL, criteria TEXT, result TEXT,
    created_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_items (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, quantity_type TEXT NOT NULL, description TEXT,
    unit TEXT NOT NULL, rate REAL NOT NULL, currency TEXT NOT NULL, price_date TEXT NOT NULL,
    source TEXT NOT NULL, region TEXT, file_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, comparison_id TEXT,
    html_path TEXT, pdf_path TEXT, status TEXT NOT NULL, summary TEXT, created_by TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
    user_id TEXT, kind TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_features_project ON features(project_id);
CREATE INDEX IF NOT EXISTS ix_metrics_run ON metrics(run_id);
CREATE INDEX IF NOT EXISTS ix_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS ix_datasets_cache ON datasets(cache_key);
"""

# Columns holding JSON documents, decoded automatically on read.
JSON_COLUMNS: dict[str, set[str]] = {
    "projects": {"boundary", "objectives", "analysis_period", "history"},
    "files": {"validation", "source_metadata"},
    "features": {"geometry", "original_geometry", "attributes", "correction_history"},
    "datasets": {
        "request_params", "spatial_coverage", "time_coverage", "variables", "units", "quality",
        "provenance", "parent_ids", "scene_ids",
    },
    "jobs": {"input", "output", "error_details"},
    "scenarios": {"features", "modifications"},
    "runs": {"configuration", "fingerprint", "input_dataset_ids", "module_status", "warnings"},
    "metrics": {"source_dataset_ids", "quality", "uncertainty", "extra"},
    "layers": {"bounds", "legend", "meta"},
    "findings": {"geometry", "metric_ids", "threshold", "evidence", "sources", "limitations"},
    "recommendations": {"evidence", "target", "required_inputs", "tradeoffs"},
    "comparisons": {"alternative_scenario_ids", "run_ids", "criteria", "result"},
    "reports": {"summary"},
}

_local = threading.local()
_init_lock = threading.Lock()
_initialised_for: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def conn() -> sqlite3.Connection:
    path = str(settings.db_path)
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        settings.ensure_dirs()
        c = sqlite3.connect(path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn, _local.path = c, path
        with _init_lock:
            if path not in _initialised_for:
                c.executescript(SCHEMA)
                c.commit()
                _initialised_for.add(path)
    return c


def reset_connection() -> None:
    """Drop the thread-local connection (used by tests that swap data dirs)."""
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
    _local.conn = None


def _encode(table: str, data: dict[str, Any]) -> dict[str, Any]:
    cols = JSON_COLUMNS.get(table, set())
    out = {}
    for k, v in data.items():
        if k in cols and v is not None:
            out[k] = json.dumps(v, default=str)
        elif isinstance(v, bool):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _decode(table: str, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    cols = JSON_COLUMNS.get(table, set())
    d = dict(row)
    for k in cols:
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


def insert(table: str, data: dict[str, Any]) -> dict[str, Any]:
    enc = _encode(table, data)
    keys = list(enc)
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
    c = conn()
    c.execute(sql, [enc[k] for k in keys])
    c.commit()
    return data


def update(table: str, key: str, key_value: Any, data: dict[str, Any]) -> None:
    if not data:
        return
    enc = _encode(table, data)
    sets = ", ".join(f"{k} = ?" for k in enc)
    c = conn()
    c.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", [*enc.values(), key_value])
    c.commit()


def get(table: str, id_: str, key: str = "id") -> dict[str, Any] | None:
    row = conn().execute(f"SELECT * FROM {table} WHERE {key} = ?", [id_]).fetchone()
    return _decode(table, row)


def select(table: str, where: str = "1=1", params: Iterable[Any] = (), order: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {table} WHERE {where}"
    if order:
        sql += f" ORDER BY {order}"
    return [_decode(table, r) for r in conn().execute(sql, list(params)).fetchall()]


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    c = conn()
    c.execute(sql, list(params))
    c.commit()
