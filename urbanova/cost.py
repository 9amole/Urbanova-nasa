"""Indicative intervention cost estimation (FR-28). Never invents prices."""

from __future__ import annotations

import csv
import io
from datetime import date

from shapely.geometry import shape
from shapely.ops import unary_union

from . import db, geo

QUANTITY_TYPES = {
    "green_space_added_m2": {"label": "Green space added", "unit": "m2"},
    "hard_surface_removed_m2": {"label": "Hard surface (road/parking) removed", "unit": "m2"},
    "trees_added_count": {"label": "Trees added", "unit": "each"},
    "shade_structure_added_m2": {"label": "Shade structure area added", "unit": "m2"},
    "building_footprint_removed_m2": {"label": "Building footprint removed", "unit": "m2"},
}
REQUIRED_COLUMNS = {"quantity_type", "unit", "rate", "currency", "price_date", "source"}
STALE_PRICE_YEARS = 2


class CostError(ValueError):
    pass


def _validate_rate(row: dict, line: str = "") -> dict:
    qt = (row.get("quantity_type") or "").strip()
    if qt not in QUANTITY_TYPES:
        raise CostError(f"{line}Unknown quantity_type '{qt}'. Allowed: {', '.join(QUANTITY_TYPES)}.")
    unit = (row.get("unit") or "").strip().lower().replace("²", "2")
    if unit != QUANTITY_TYPES[qt]["unit"]:
        raise CostError(f"{line}Unit '{row.get('unit')}' does not match the quantity unit '{QUANTITY_TYPES[qt]['unit']}' for {qt}.")
    try:
        rate = float(row.get("rate"))
    except (TypeError, ValueError):
        raise CostError(f"{line}Rate must be a number.")
    if rate < 0:
        raise CostError(f"{line}Rate cannot be negative.")
    cur = (row.get("currency") or "").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise CostError(f"{line}Currency must be a 3-letter ISO code (e.g. EGP, USD).")
    try:
        pdate = date.fromisoformat((row.get("price_date") or "").strip())
    except ValueError:
        raise CostError(f"{line}price_date must be a date in YYYY-MM-DD format.")
    if pdate > date.today():
        raise CostError(f"{line}price_date cannot be in the future.")
    source = (row.get("source") or "").strip()
    if not source:
        raise CostError(f"{line}A source (quotation, schedule of rates, supplier) is required.")
    return {"quantity_type": qt, "unit": unit, "rate": rate, "currency": cur, "price_date": pdate.isoformat(), "source": source,
            "description": (row.get("description") or "").strip() or None, "region": (row.get("region") or "").strip() or None}


def add_rate(project_id: str, row: dict, file_id: str | None = None) -> dict:
    rec = {"id": db.new_id("rat"), "project_id": project_id, **_validate_rate(row), "file_id": file_id, "created_at": db.now()}
    db.insert("rate_items", rec)
    return rec


def import_csv(project_id: str, content: bytes, file_id: str) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CostError("The rate table must be saved as UTF-8 CSV (in Excel: Save As → CSV UTF-8).")
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        raise CostError(f"The rate table is missing column(s): {', '.join(sorted(missing))}.")
    rows = [{(k or "").strip(): v for k, v in r.items()} for r in reader]
    validated = [_validate_rate(r, f"Row {i + 2}: ") for i, r in enumerate(rows)]  # all-or-nothing
    return [add_rate(project_id, v, file_id) for v in validated]


def _quantities(project: dict, fc: dict) -> dict:
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    site = geo.to_local(shape(project["boundary"]), local) if project.get("boundary") else None
    areas, trees = {}, 0
    for t in ("green_space", "road", "parking", "shade_structure", "building"):
        geoms = [geo.to_local(shape(f["geometry"]), local) for f in fc["features"] if f["properties"].get("feature_type") == t and f["geometry"]["type"] in ("Polygon", "MultiPolygon")]
        u = unary_union(geoms) if geoms else None
        areas[t] = 0.0 if u is None else (u.intersection(site).area if site is not None else u.area)
    trees = sum(1 for f in fc["features"] if f["properties"].get("feature_type") == "tree")
    return {"areas": areas, "trees": trees}


def estimate(project: dict, baseline: dict, alternative: dict) -> dict:
    qb, qa = _quantities(project, baseline["features"]), _quantities(project, alternative["features"])
    raw = {
        "green_space_added_m2": qa["areas"]["green_space"] - qb["areas"]["green_space"],
        "hard_surface_removed_m2": (qb["areas"]["road"] + qb["areas"]["parking"]) - (qa["areas"]["road"] + qa["areas"]["parking"]),
        "trees_added_count": qa["trees"] - qb["trees"],
        "shade_structure_added_m2": qa["areas"]["shade_structure"] - qb["areas"]["shade_structure"],
        "building_footprint_removed_m2": qb["areas"]["building"] - qa["areas"]["building"],
    }
    rates = db.select("rate_items", "project_id = ?", [project["id"]], order="price_date DESC")
    lines, totals, exclusions, warnings = [], {}, [], []
    for qt, qty in raw.items():
        if qty <= 0.5:
            continue
        spec = QUANTITY_TYPES[qt]
        matching = [r for r in rates if r["quantity_type"] == qt]
        line = {"quantity_type": qt, "label": spec["label"], "quantity": round(qty, 1), "unit": spec["unit"],
                "quantity_method": "Difference in site-clipped feature totals between the alternative and the baseline"}
        if not matching:
            line.update(status="price_required", note="No unit rate supplied for this quantity; no price is invented.")
            exclusions.append(f"{spec['label']}: rate not supplied")
        else:
            r = matching[0]
            sub = qty * r["rate"]
            line.update(status="priced", rate=r["rate"], currency=r["currency"], rate_source=r["source"], price_date=r["price_date"], rate_id=r["id"], subtotal=round(sub, 2))
            totals[r["currency"]] = totals.get(r["currency"], 0) + sub
            if (date.today() - date.fromisoformat(r["price_date"])).days > 365 * STALE_PRICE_YEARS:
                warnings.append(f"The rate for {spec['label'].lower()} is dated {r['price_date']} (older than {STALE_PRICE_YEARS} years).")
            if len(matching) > 1:
                warnings.append(f"{len(matching)} rates exist for {spec['label'].lower()}; the most recent ({r['price_date']}) was used.")
        lines.append(line)
    if len(totals) > 1:
        warnings.append("Rates use more than one currency; totals are given per currency and are not summed.")
    priced = [ln for ln in lines if ln["status"] == "priced"]
    return {
        "lines": lines,
        "totals_by_currency": {k: round(v, 2) for k, v in totals.items()},
        "status": "no_quantities" if not lines else ("complete" if len(priced) == len(lines) else ("partial" if priced else "quantities_only")),
        "exclusions": exclusions + ["Design fees, demolition, utilities, irrigation systems, maintenance, taxes and contingency are excluded unless supplied as rates."],
        "warnings": warnings,
        "label": "Indicative cost estimate from user-supplied rates — not a quotation or bill of quantities.",
        "estimated_at": db.now(),
    }
