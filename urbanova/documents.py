"""Planning-document ingestion and candidate-rule extraction (FR-03, FR-48).

Extraction is deterministic pattern matching over the PDF text layer, in English
and Arabic (decision C4: all output is English; Arabic wording is kept verbatim as
evidence, with Arabic-Indic digits shown as 0-9). Every candidate keeps its exact
wording, page and document; none is applied to compliance until a user verifies it.
Scanned pages without a text layer are reported — OCR is not in Phase 1.
"""

from __future__ import annotations

import re
import unicodedata
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
# A decimal comma ("40,5") is tried first so it is never read as its last digit; "1,250" (three
# digits after the comma) stays a thousands separator. Both cases are flagged for review below.
NUM = r"(\d+,\d{1,2}(?!\d)|\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
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
] + [
    # Arabic (Egyptian planning terms). Matched on normalised text: presentation forms folded,
    # diacritics and tatweel removed, Arabic-Indic digits and separators converted to 0-9 . , %
    ("max_building_coverage_pct", re.compile(r"نسب[ةه]\s+(?:ال)?(?:بناء|تغطي[ةه]|إشغال|اشغال|مباني)[^.%\n]{0,80}?" + NUM + r"\s*%")),
    ("max_height_m", re.compile(r"ارتفاع[^.\n]{0,60}?" + NUM + r"\s*(?:م\b|متر|مترا|أمتار|امتار)")),
    ("max_floors", re.compile(r"(?:لا\s+(?:يزيد|يتجاوز|تزيد|تتجاوز)|بحد\s+أقصى|بحد\s+اقصى|أقصى|اقصى|حتى)[^.\n]{0,30}?(\d{1,3})\s*(?:أدوار|ادوار|طوابق|طابق|دور)")),
    ("max_floors", re.compile(r"(?:أرضي|ارضي|الأرضي|الارضي)\s*\+\s*(\d{1,2})(?!\d)")),
    ("min_setback_m", re.compile(r"(?:الردود|ردود|الارتداد|ارتداد|الردود\s+الجانبي[ةه])[^.\n]{0,60}?" + NUM + r"\s*(?:م\b|متر|مترا|أمتار|امتار)")),
    ("min_green_space_pct", re.compile(r"(?:المساحات|المسطحات|مساح[ةه]|مسطحات)\s+(?:ال)?خضراء[^.%\n]{0,80}?" + NUM + r"\s*%")),
    ("min_open_space_pct", re.compile(r"(?:المساحات|المسطحات|مساح[ةه]|مسطحات)\s+(?:ال)?مفتوح[ةه][^.%\n]{0,80}?" + NUM + r"\s*%")),
    ("max_far", re.compile(r"(?:معامل\s+(?:ال)?استغلال|معامل\s+(?:ال)?بناء|نسب[ةه]\s+(?:ال)?مسطحات\s+(?:ال)?بنائي[ةه])[^.\n]{0,40}?" + NUM)),
    ("plot_area_m2", re.compile(r"مساح[ةه]\s+(?:قطع[ةه]\s+)?(?:ال)?(?:أرض|ارض|قطع[ةه]|موقع)[^.\n]{0,40}?" + NUM
                                + r"\s*(م\s*2|م²|متر\s+مربع|مترا\s+مربعا|أمتار\s+مربع[ةه]|فدان|فدانا|أفدن[ةه]|افدن[ةه]|هكتار)")),
    ("permitted_land_use", re.compile(r"(?:الاستخدام|الاستعمال|النشاط|الاستخدامات)\s+(?:المسموح|المصرح)\s*(?:به|بها)?\s*[:：]?\s*([^.\n]{3,160})")),
    ("parking_requirement", re.compile(r"((?:\d+\s*)?(?:موقف|مواقف|مكان|أماكن)\s+(?:ل)?(?:سيار[ةه]|سيارات|انتظار)[^.\n]{0,20}?(?:لكل|عن\s+كل)\s+[^.\n]{2,80})")),
]
GROUND_PLUS = re.compile(r"^(?:G|أرضي|ارضي|الأرضي|الارضي)\s*\+", re.I)
ARABIC_LETTER = re.compile(r"[\u0621-\u064A]")
# Words whose presence tells which reading order of an Arabic line is the logical one.
ARABIC_TERMS = re.compile(r"نسب[ةه]|البناء|ارتفاع|مساح[ةه]|الأرض|الارض|أدوار|ادوار|طوابق|الردود|ارتداد|الخضراء|المفتوح[ةه]|معامل|الاستخدام|مواقف|لكل|متر|يزيد|يتجاوز")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬٪", "01234567890123456789.,%")
_STRIP = re.compile(r"[\u0640\u064B-\u0652\u0670]")  # tatweel, harakat, superscript alef


def normalize_page(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalise a page's text layer for matching. Returns (text, reversed_spans).

    Arabic lines extracted in visual order come out reversed character by character (digits
    included). For each line with Arabic letters the order with more recognised planning terms is
    kept; spans of lines that were reversed are returned so their candidates can be flagged."""
    out, spans, pos = [], [], 0
    for line in unicodedata.normalize("NFKC", text).split("\n"):
        line = _STRIP.sub("", line)
        if ARABIC_LETTER.search(line):
            rev = line[::-1]
            if len(ARABIC_TERMS.findall(rev)) > len(ARABIC_TERMS.findall(line)):
                line = rev
                spans.append((pos, pos + len(line)))
        line = line.translate(_DIGITS)
        out.append(line)
        pos += len(line) + 1
    return "\n".join(out), spans


def _area_unit(unit: str) -> str:
    u = unit.lower()
    if u.startswith("h") or u.startswith("هكتار"):
        return "ha"
    if u.startswith("f") or u.startswith("ف") or u.startswith("أفدن") or u.startswith("افدن"):
        return "feddan"
    return "m2"
DECIMAL_COMMA = re.compile(r"^\d+,\d{1,2}$")
FLOOR_CONTEXT = re.compile(r"\b(basements?|parking|car|garages?|underground|below\s+ground|بدروم|البدروم|قبو|جراج|الجراج|مواقف|انتظار|سيارات)\b", re.I)
ADVISORY = re.compile(r"\b(may|should|recommended|encouraged|يجوز|يفضل|يستحسن|يوصى|ينصح)\b", re.I)
DENOMINATOR_WORDS = re.compile(r"\b(net|gross|plot|site|parcel|lot|land)\s+area\b|\bof\s+the\s+(plot|site|parcel|lot)\b"
                               r"|صافي|إجمالي|اجمالي|من\s+مساح[ةه]\s+(?:ال)?(?:أرض|ارض|قطع[ةه]|موقع)", re.I)


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
    if DECIMAL_COMMA.match(text):
        return float(text.replace(",", "."))
    return float(text.replace(",", "").replace(" ", ""))


def _number_notes(text: str, m: re.Match) -> list[str]:
    """Reasons the number in group 1 may be mis-read (decimal comma, digits split by spaces)."""
    raw = m.group(1)
    notes = []
    if DECIMAL_COMMA.match(raw):
        notes.append(f"'{raw}' was read as a decimal comma ({_to_float(raw):g}); confirm the value.")
    elif re.search(r"\d\s+\d", raw):
        notes.append(f"'{raw}' contains digits separated by a space and was read as {_to_float(raw):g}; confirm it is one number.")
    if re.search(r"\d[,\s]*$", text[max(0, m.start(1) - 4):m.start(1)]):
        notes.append(f"Another number directly precedes '{raw}' in the text, so the value may be mis-read; check the source wording.")
    return notes


def _sentence(text: str, start: int, end: int) -> str:
    s = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    e_candidates = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    e = min(e_candidates) + 1 if e_candidates else len(text)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def find_candidates(pages: list[str]) -> list[dict]:
    found: list[dict] = []
    seen = set()
    for page_no, raw in enumerate(pages, start=1):
        text, reversed_spans = normalize_page(raw)
        for rule_type, pattern in PATTERNS:
            for m in pattern.finditer(text):
                sentence = _sentence(text, m.start(), m.end())
                key = (rule_type, page_no, m.group(0).strip().lower())
                if key in seen:
                    continue
                seen.add(key)
                cand: dict[str, Any] = {"rule_type": rule_type, "page": page_no, "exact_text": sentence, "match": m.group(0).strip(),
                                        "language": "ar" if ARABIC_LETTER.search(m.group(0)) else "en"}
                notes = []
                if any(a <= m.start() < b for a, b in reversed_spans):
                    notes.append("The PDF stored this Arabic line in visual (reversed) order; it was re-ordered for reading. Check the value against the document.")
                spec = RULE_TYPES[rule_type]
                if spec["operator"] == "text":
                    cand["value_text"] = m.group(1).strip()
                    cand["value"] = None
                elif rule_type == "plot_area_m2":
                    unit_key = _area_unit(m.group(2))
                    raw = _to_float(m.group(1))
                    notes.extend(_number_notes(text, m))
                    cand["value"] = raw * AREA_UNITS[unit_key]
                    cand["value_text"] = f"{m.group(1)} {m.group(2)}"
                    if unit_key != "m2":
                        notes.append(f"Converted from {'hectares' if unit_key == 'ha' else 'feddan'} using 1 {unit_key} = {AREA_UNITS[unit_key]:g} m².")
                else:
                    cand["value"] = _to_float(m.group(1))
                    if rule_type == "max_floors" and FLOOR_CONTEXT.search(sentence):
                        notes.append("The floor count appears next to basement/parking wording; it may describe basement or parking levels rather than building floors.")
                    if rule_type == "max_floors" and GROUND_PLUS.match(m.group(0).strip()):
                        cand["value"] += 1  # "G+5" = ground floor + 5 upper floors = 6 floors
                        notes.append(f"'{m.group(0).strip()}' interpreted as ground + {int(cand['value']) - 1} = {int(cand['value'])} floors.")
                if spec["operator"] != "text" and rule_type != "plot_area_m2" and not (rule_type == "max_floors" and GROUND_PLUS.match(m.group(0).strip())):
                    notes.extend(_number_notes(text, m))
                if rule_type.endswith("_pct") and cand["value"] is not None and not 0 < cand["value"] <= 100:
                    notes.append("The percentage is outside 0–100 and is probably a mis-read.")
                if rule_type == "max_building_coverage_pct" and not DENOMINATOR_WORDS.search(sentence):
                    notes.append("Ambiguous denominator: the text does not state whether coverage is of the gross plot, net plot or another sub-area.")
                if ADVISORY.search(sentence):
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


def check_value(rtype: str, value: float) -> None:
    """Range checks shared by manual entry and verification of extracted values."""
    if value <= 0 or (rtype.endswith("_pct") and value > 100):
        raise DocumentError("The value is outside the valid range for this rule (it must be above 0, and at most 100 for a percentage).")
    if rtype == "max_floors" and value != int(value):
        raise DocumentError("A floor count must be a whole number.")


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
        check_value(rtype, value)
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
    numeric = RULE_TYPES[rule["rule_type"]]["operator"] != "text"
    if value is not None and numeric:
        try:
            upd["value"] = float(value)
        except (TypeError, ValueError):
            raise DocumentError("The corrected value must be a number.")
    if decision == "verified" and numeric:
        effective = upd.get("value", rule.get("value"))
        if effective is None:
            raise DocumentError("Enter the value before verifying this rule.")
        check_value(rule["rule_type"], effective)
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


def rules_for_analysis(project_id: str) -> dict:
    """The rules block an analysis context expects (see analysis.common.Context.rules)."""
    return {
        "verified": verified_rules(project_id),
        "unverified_count": len(db.select("rules", "project_id = ? AND status = 'candidate'", [project_id])),
        "ambiguities": unresolved_ambiguities(project_id),
    }


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
