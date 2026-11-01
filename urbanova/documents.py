"""Planning-document ingestion and candidate-rule extraction (FR-03, FR-48).

Extraction is deterministic pattern matching over the PDF text layer. Every
candidate keeps its exact wording, page and document; none is applied to
compliance until a user verifies it. Scanned pages without a text layer are
reported — OCR is not available in this build (no OCR engine installed).
"""

from __future__ import annotations

import re
from typing import Any

from . import db

RULE_TYPES = {
    "max_building_coverage_pct": {"label": "Maximum building coverage", "unit": "%", "operator": "<="},
    "max_height_m": {"label": "Maximum building height", "unit": "m", "operator": "<="},
    "max_floors": {"label": "Maximum number of floors", "unit": "floors", "operator": "<="},
    "min_setback_m": {"label": "Minimum setback from site boundary", "unit": "m", "operator": ">="},
    "min_green_space_pct": {"label": "Minimum green-space proportion", "unit": "%", "operator": ">="},
    "min_open_space_pct": {"label": "Minimum open-space proportion", "unit": "%", "operator": ">="},
    "max_far": {"label": "Maximum floor-area ratio", "unit": "ratio", "operator": "<="},
    "plot_area_m2": {"label": "Stated plot / site area", "unit": "m²", "operator": "="},
    "permitted_land_use": {"label": "Permitted land use", "unit": None, "operator": "text"},
    "parking_requirement": {"label": "Parking requirement", "unit": None, "operator": "text"},
}
NUM = r"(\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
# 1 feddan = 4,200.83 m² (Egyptian land unit); the conversion is disclosed on every use.
AREA_UNITS = {"m2": 1.0, "ha": 10_000.0, "feddan": 4200.83}
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("max_building_coverage_pct", re.compile(r"(?:building|site|plot|ground|lot)\s+coverage[^.%\n]{0,80}?" + NUM + r"\s*%", re.I)),
    ("max_building_coverage_pct", re.compile(r"(?:built[- ]up|footprint)\s+(?:area|ratio)[^.%\n]{0,60}?(?:not\s+exceed|maximum|max\.?|up\s+to)[^.%\n]{0,30}?" + NUM + r"\s*%", re.I)),
    ("max_height_m", re.compile(r"(?:maximum|max\.?)\s+(?:building\s+)?height[^.\n]{0,60}?" + NUM + r"\s*(?:m\b|metres|meters)", re.I)),
    ("max_height_m", re.compile(r"height[^.\n]{0,40}?(?:shall\s+not|must\s+not|not\s+to)\s+exceed[^.\n]{0,20}?" + NUM + r"\s*(?:m\b|metres|meters)", re.I)),
    ("max_floors", re.compile(r"(?:maximum|max\.?|not\s+exceed(?:ing)?|up\s+to)[^.\n]{0,30}?" + r"(\d{1,3})\s*(?:floors|storeys|stories|levels)", re.I)),
    ("max_floors", re.compile(r"\bG\s*\+\s*(\d{1,2})\b", re.I)),
    ("min_setback_m", re.compile(r"set\s*-?\s*backs?[^.\n]{0,60}?" + NUM + r"\s*(?:m\b|metres|meters)", re.I)),
    ("min_green_space_pct", re.compile(r"(?:green|landscap\w*|planted|soft)\s+(?:space|area|landscape)s?[^.%\n]{0,80}?" + NUM + r"\s*%", re.I)),
    ("min_open_space_pct", re.compile(r"open\s+(?:space|area)s?[^.%\n]{0,80}?" + NUM + r"\s*%", re.I)),
    ("max_far", re.compile(r"(?:floor\s+area\s+ratio|\bFAR\b)[^.\n]{0,40}?" + NUM, re.I)),
    ("plot_area_m2", re.compile(r"(?:plot|site|land|parcel|total)\s+area[^.\n]{0,40}?" + NUM + r"\s*(m2|m²|sq\.?\s*m|sqm|square\s+met(?:re|er)s|hectares?|ha\b|feddans?)", re.I)),
    ("permitted_land_use", re.compile(r"permitted\s+(?:land\s+)?uses?\s*(?:is|are|:|include)?\s*([^.\n]{3,160})", re.I)),
    ("parking_requirement", re.compile(r"((?:\d+|one|two)\s+parking\s+spaces?\s+(?:per|for\s+each)\s+[^.\n]{2,80})", re.I)),
]
DENOMINATOR_WORDS = re.compile(r"\b(net|gross|plot|site|parcel|lot|land)\s+area\b|\bof\s+the\s+(plot|site|parcel|lot)\b", re.I)


class DocumentError(ValueError):
    pass


def extract_text(path: str) -> tuple[list[str], list[str]]:
    """Return (page_texts, issues)."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise DocumentError("The PDF is password-protected. Upload an unprotected copy.")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise DocumentError(f"The PDF is corrupted or unreadable: {exc}.")
    issues = []
    for i, text in enumerate(pages, start=1):
        if len(text.strip()) < 20:
            issues.append(
                f"Page {i} has no readable text layer (probably a scan). OCR is not available in this build, so this page was not analysed; enter its conditions manually."
            )
    return pages, issues


def _to_float(text: str) -> float:
    return float(text.replace(",", "").replace(" ", ""))


def _sentence(text: str, start: int, end: int) -> str:
    s = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    e_candidates = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    e = min(e_candidates) + 1 if e_candidates else len(text)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def find_candidates(pages: list[str]) -> list[dict]:
    found: list[dict] = []
    seen = set()
    for page_no, text in enumerate(pages, start=1):
        for rule_type, pattern in PATTERNS:
            for m in pattern.finditer(text):
                sentence = _sentence(text, m.start(), m.end())
                key = (rule_type, page_no, m.group(0).strip().lower())
                if key in seen:
                    continue
                seen.add(key)
                cand: dict[str, Any] = {"rule_type": rule_type, "page": page_no, "exact_text": sentence, "match": m.group(0).strip()}
                notes = []
                spec = RULE_TYPES[rule_type]
                if spec["operator"] == "text":
                    cand["value_text"] = m.group(1).strip()
                    cand["value"] = None
                elif rule_type == "plot_area_m2":
                    unit = m.group(2).lower()
                    unit_key = "ha" if unit.startswith("h") else "feddan" if unit.startswith("f") else "m2"
                    raw = _to_float(m.group(1))
                    cand["value"] = raw * AREA_UNITS[unit_key]
                    cand["value_text"] = f"{m.group(1)} {m.group(2)}"
                    if unit_key != "m2":
                        notes.append(f"Converted from {m.group(2)} using 1 {unit_key} = {AREA_UNITS[unit_key]:g} m².")
                else:
                    cand["value"] = _to_float(m.group(1))
                    if rule_type == "max_floors" and m.group(0).strip().upper().startswith("G"):
                        cand["value"] += 1  # "G+5" = ground floor + 5 upper floors = 6 floors
                        notes.append(f"'{m.group(0).strip()}' interpreted as ground + {int(cand['value']) - 1} = {int(cand['value'])} floors.")
                if rule_type.endswith("_pct") and cand["value"] is not None and not 0 < cand["value"] <= 100:
                    notes.append("The percentage is outside 0–100 and is probably a mis-read.")
                if rule_type == "max_building_coverage_pct" and not DENOMINATOR_WORDS.search(sentence):
                    notes.append("Ambiguous denominator: the text does not state whether coverage is of the gross plot, net plot or another sub-area.")
                if re.search(r"\b(may|should|recommended|encouraged)\b", sentence, re.I):
                    notes.append("Wording is advisory ('may/should/recommended'); confirm whether this is a binding requirement.")
                cand["notes"] = notes
                cand["confidence"] = "low" if notes else "medium"
                found.append(cand)
    return found


def detect_conflicts(candidates: list[dict]) -> list[dict]:
    """Contradictions between candidates of the same type (e.g. conflicting plot areas)."""
    issues = []
    by_type: dict[str, list[dict]] = {}
    for c in candidates:
        if c.get("value") is not None:
            by_type.setdefault(c["rule_type"], []).append(c)
    for rtype, items in by_type.items():
        values = sorted({round(c["value"], 2) for c in items})
        if len(values) > 1:
            spread = (values[-1] - values[0]) / values[-1] * 100 if values[-1] else 0
            if rtype != "plot_area_m2" or spread > 1:
                issues.append({
                    "type": "conflicting_values",
                    "rule_type": rtype,
                    "values": values,
                    "pages": sorted({c["page"] for c in items}),
                    "message": f"The document states different values for {RULE_TYPES[rtype]['label'].lower()} ({', '.join(f'{v:g}' for v in values)}). Verify which applies before it is used.",
                })
    return issues


def ingest_document(project: dict, file_rec: dict) -> dict:
    pages, page_issues = extract_text(file_rec["storage_path"])
    candidates = find_candidates(pages)
    conflicts = detect_conflicts(candidates)
    conflict_types = {c["rule_type"] for c in conflicts}
    now = db.now()
    for c in candidates:
        spec = RULE_TYPES[c["rule_type"]]
        notes = list(c["notes"])
        if c["rule_type"] in conflict_types:
            notes.append("Conflicts with another value in the same document.")
        db.insert("rules", {
            "id": db.new_id("rul"), "project_id": project["id"], "file_id": file_rec["id"], "rule_type": c["rule_type"],
            "operator": spec["operator"], "value": c.get("value"), "unit": spec["unit"], "value_text": c.get("value_text"),
            "exact_text": c["exact_text"], "page": c["page"], "document_name": file_rec["original_filename"],
            "issue_date": None, "applicability": None, "confidence": "low" if c["rule_type"] in conflict_types else c["confidence"],
            "origin": "extracted_pattern_match", "status": "candidate", "notes": " ".join(notes) or None, "created_at": now,
        })
    return {
        "status": "extracted",
        "pages": len(pages),
        "pages_with_text": sum(1 for p in pages if len(p.strip()) >= 20),
        "candidates": len(candidates),
        "issues": page_issues,
        "ambiguities": conflicts,
        "message": "Candidate conditions were extracted. None is applied until you verify it on the Planning rules screen.",
    }


def add_manual_rule(project: dict, user: dict, payload: dict) -> dict:
    rtype = payload.get("rule_type")
    if rtype not in RULE_TYPES:
        raise DocumentError(f"Unknown rule type. Choose one of: {', '.join(RULE_TYPES)}.")
    spec = RULE_TYPES[rtype]
    value = payload.get("value")
    if spec["operator"] != "text":
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise DocumentError("A numeric value is required for this rule.")
        if value <= 0 or (rtype.endswith("_pct") and value > 100):
            raise DocumentError("The value is outside the valid range for this rule.")
    source = (payload.get("source") or "").strip()
    if not source:
        raise DocumentError("State the source of the rule (document, authority, clause) so it remains traceable.")
    rule = {
        "id": db.new_id("rul"), "project_id": project["id"], "file_id": None, "rule_type": rtype, "operator": spec["operator"],
        "value": value if spec["operator"] != "text" else None, "unit": spec["unit"],
        "value_text": str(payload.get("value")) if spec["operator"] == "text" else None,
        "exact_text": (payload.get("exact_text") or "").strip() or None, "page": payload.get("page"),
        "document_name": source, "issue_date": payload.get("issue_date"), "applicability": payload.get("applicability"),
        "confidence": "user_entered", "origin": "manual_entry", "status": "candidate", "notes": None, "created_at": db.now(),
    }
    db.insert("rules", rule)
    return rule


def decide_rule(rule: dict, user: dict, decision: str, value: Any = None, note: str | None = None) -> dict:
    if decision not in ("verified", "rejected", "candidate"):
        raise DocumentError("Decision must be 'verified', 'rejected' or 'candidate'.")
    upd: dict[str, Any] = {"status": decision, "decided_by": user["id"], "decided_at": db.now()}
    if value is not None and RULE_TYPES[rule["rule_type"]]["operator"] != "text":
        try:
            upd["value"] = float(value)
        except (TypeError, ValueError):
            raise DocumentError("The corrected value must be a number.")
    if note:
        upd["notes"] = ((rule.get("notes") or "") + f" [User note: {note}]").strip()
    if decision == "verified":
        # A verified value of the same type supersedes other verified ones (one active value per rule type).
        for other in db.select("rules", "project_id = ? AND rule_type = ? AND status = 'verified' AND id != ?", [rule["project_id"], rule["rule_type"], rule["id"]]):
            db.update("rules", "id", other["id"], {"status": "superseded", "decided_at": db.now()})
    db.update("rules", "id", rule["id"], upd)
    return db.get("rules", rule["id"])


def verified_rules(project_id: str) -> dict[str, dict]:
    return {r["rule_type"]: r for r in db.select("rules", "project_id = ? AND status = 'verified'", [project_id])}


def unresolved_ambiguities(project_id: str) -> list[dict]:
    """Candidates that conflict with each other and have not been resolved by verification."""
    cands = db.select("rules", "project_id = ? AND status = 'candidate'", [project_id])
    verified = verified_rules(project_id)
    groups: dict[str, set] = {}
    for c in cands:
        if c["value"] is not None:
            groups.setdefault(c["rule_type"], set()).add(round(c["value"], 2))
    out = []
    for rtype, vals in groups.items():
        if len(vals) > 1 and rtype not in verified:
            out.append({"rule_type": rtype, "values": sorted(vals), "message": f"Conflicting values for {RULE_TYPES[rtype]['label'].lower()} are unresolved."})
    return out
