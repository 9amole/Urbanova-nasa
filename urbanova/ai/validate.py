"""Grounding validator for AI-written text (PHASE2_SPEC §5 step 4). Runs on every output.

An output is a JSON object of blocks (site_status, top_issues, attention_first, answers, limitations), each a list
of claims {text, claim_type, evidence_ids, code?}. It is rejected, with messages, on any of:
invalid JSON or shape, an unknown claim type, an unknown evidence id, a number not in the cited evidence (numbers
in words, k-suffixes, %, m² / ha / km² and °C are normalised first, with rounding tolerance), a cause code the
hotspot does not have, a hotspot mentioned that the claim does not cite, a temperature prediction or literature
number for the site, a simulation / CFD claim, a changed priority order, a forbidden claim (statutory approval,
guaranteed savings, health outcomes) or a limitation of the pack that no limitation claim discloses.
"""

from __future__ import annotations

import json
import re

from .evidence import index

CLAIM_TYPES = {"metric_value", "hotspot_location", "cause", "comparison", "recommendation", "limitation", "explanation_of_term"}
BLOCKS = ("site_status", "top_issues", "attention_first", "answers", "limitations")
WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
         "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
         "half": 0.5, "dozen": 12}
# Tokens that carry digits but are not quantities: record ids, codes, references, clock times.
NON_QUANTITY = re.compile(r"\b[a-z]{2,4}_[0-9a-f]{6,}\b|\bR-\d+\b|\b[HIA]\d{1,2}\b|\bTX\d+p?\b|\b\d{1,2}:\d{2}\b|\b[A-Z][A-Z_]{3,}\b|\bGi\*")
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(k\b|thousand\b|million\b)?\s*(%|per\s*cent|percent|m²|m2|sq\.?\s*m|square\s+met(?:re|er)s?|ha\b|hectares?|km²|km2|°\s*C|degrees?|h\b|hours?)?", re.I)
WORD_NUMBER = re.compile(r"\b(" + "|".join(WORDS) + r")\b\s*(%|per\s*cent|m²|ha\b|hours?|°\s*C)?", re.I)
HOTSPOT_REF = re.compile(r"\bhotspots?\s+(?:no\.?\s*)?(\d+)", re.I)
PREDICTION = re.compile(r"(\b(cool(?:s|ed|ing)?|lower(?:s|ed|ing)?|reduc\w*|drop\w*|warm\w*)\b[^.]{0,60}\bby\s+(about\s+)?\d+(\.\d+)?\s*(°\s*C|degrees|K\b))|"
                        r"(\bwill be\s+(about\s+)?\d+(\.\d+)?\s*(°\s*C|degrees)\s*(cooler|warmer))|(\b\d+(\.\d+)?\s*(°\s*C|degrees)\s*(cooler|warmer)\b)", re.I)
SIMULATION = re.compile(r"\b(CFD|ENVI-?met|computational fluid dynamics|microclimate (?:model(?:l)?ing|simulation|model)|simulat\w*)\b", re.I)
NEGATION = re.compile(r"\b(no|not|without|never|nor)\b[^.]{0,25}$", re.I)
FORBIDDEN = [
    (re.compile(r"\b(statutory|legal|official|municipal|authority)\s+(approval|permit|consent|sign-?off)|\b(is|are|has been|have been|will be)\s+(approved|certified|compliant with (the )?law)\b", re.I),
     "statutory approval or certification"),
    (re.compile(r"\bguarantee\w*|\bwill save\b|\bsavings of\b|\bcost savings\b", re.I), "guaranteed savings"),
    (re.compile(r"\b(heat ?stroke|mortality|deaths?|lives|hospitali[sz]ation|health (outcomes?|benefits?|risks?))\b", re.I), "health outcomes"),
]
AREA = {"m²": 1.0, "ha": 1e4, "km²": 1e6}


def _unit(u: str | None) -> str | None:
    if not u:
        return None
    u = u.lower().replace(" ", "")
    if u == "%" or u.startswith("per"):
        return "%"
    if u.startswith("m²") or u in ("m2",) or u.startswith("sq") or u.startswith("square"):
        return "m²"
    if u.startswith("ha") or u.startswith("hectare"):
        return "ha"
    if u.startswith("km"):
        return "km²"
    if u.startswith("°") or u.startswith("degree"):
        return "°C"
    if u.startswith("h"):
        return "h"
    return None


def _scale(s: str | None) -> float:
    s = (s or "").lower()
    return 1e3 if s in ("k", "thousand") else 1e6 if s == "million" else 1.0


def quantities(text: str) -> list[tuple[float, str | None, float]]:
    """(value in base units, unit, tolerance) for each quantity in the text. Base units: m² for areas, % stays %."""
    t = NON_QUANTITY.sub(" ", text or "")
    out = []
    for m in NUMBER.finditer(t):
        raw, scale, unit = m.group(1), _scale(m.group(2)), _unit(m.group(3))
        v = float(raw.replace(",", "")) * scale
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** -decimals * scale
        if unit in AREA:
            v, tol, unit = v * AREA[unit], tol * AREA[unit], "m²"
        out.append((v, unit, tol))
    for m in WORD_NUMBER.finditer(t):
        unit = _unit(m.group(2))
        v = WORDS[m.group(1).lower()]
        if unit in AREA:
            v, unit = v * AREA[unit], "m²"
        out.append((float(v), unit, 0.0))
    return out


def _allowed(entry, acc: list | None = None) -> list[tuple[float, str | None]]:
    """Every quantity stated in an evidence entry: numeric fields (with their unit) and numbers inside its texts."""
    acc = [] if acc is None else acc
    if isinstance(entry, dict):
        unit = _unit(str(entry.get("unit") or "")) if "unit" in entry else None
        for k, v in entry.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                u = "%" if k.endswith("_pct") else unit if k in ("value", "before", "after") else None
                acc.append((float(v) * (AREA.get(u, 1.0) if u in AREA else 1.0), "m²" if u in AREA else u))
            else:
                _allowed(v, acc)
    elif isinstance(entry, list):
        for v in entry:
            _allowed(v, acc)
    elif isinstance(entry, str):
        acc.extend((v, u) for v, u, _ in quantities(entry))
    return acc


def _matches(q: tuple[float, str | None, float], allowed: list[tuple[float, str | None]]) -> bool:
    v, u, tol = q
    for a, au in allowed:
        cands = [(a, au)]
        if au in (None, "%"):
            cands += [(a * 100, "%")] if au is None else [(a / 100, None)]
        for c, cu in cands:
            if u is not None and cu is not None and u != cu:
                continue
            if abs(v - c) <= max(tol, 0.01 * abs(c), 1e-9):
                return True
    return False


def _claims(out: dict):
    for block in ("site_status", "top_issues", "attention_first", "limitations"):
        for c in out.get(block) or []:
            yield block, c
    for a in out.get("answers") or []:
        for c in (a.get("claims") or []) if isinstance(a, dict) else []:
            yield "answers", c


def parse(raw: str | dict) -> tuple[dict | None, list[str]]:
    if isinstance(raw, dict):
        return raw, []
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return None, ["The output is not valid JSON."]
    if not isinstance(out, dict):
        return None, ["The output must be a JSON object."]
    return out, []


def check_claim(c, idx: dict, pack: dict, global_allowed: list, where: str) -> list[str]:
    errs = []
    if not isinstance(c, dict) or not isinstance(c.get("text"), str) or not isinstance(c.get("evidence_ids", []), list):
        return [f"{where}: a claim must be an object with text and evidence_ids."]
    text, ctype, ids = c["text"], c.get("claim_type"), [str(i) for i in c.get("evidence_ids") or []]
    if ctype not in CLAIM_TYPES:
        errs.append(f"{where}: unknown claim_type {ctype!r}.")
    unknown = [i for i in ids if i not in idx]
    if unknown:
        errs.append(f"{where}: unknown evidence id(s) {', '.join(unknown)}.")
    cited = [idx[i] for i in ids if i in idx]
    allowed = list(global_allowed)
    for e in cited:
        allowed += _allowed(e)
        for mid in (mid for v in e.get("values") or [] for mid in v.get("metric_ids") or []):
            if mid in idx:
                allowed += _allowed(idx[mid])
    numbers = {h["number"]: h for h in pack["hotspots"]}
    by_id = {h["id"]: h["number"] for h in pack["hotspots"]}
    cited_hs = {by_id[e["id"]] for e in cited if e.get("id") in by_id} | {by_id[e["hotspot_id"]] for e in cited if e.get("hotspot_id") in by_id}
    for m in HOTSPOT_REF.finditer(text):
        n = int(m.group(1))
        if n not in numbers:
            errs.append(f"{where}: there is no hotspot {n}.")
        elif n not in cited_hs:
            errs.append(f"{where}: hotspot {n} is mentioned but its id ({numbers[n]['id']}) is not cited.")
    body = HOTSPOT_REF.sub(" ", text)
    bad = [q for q in quantities(body) if not _matches(q, allowed)]
    for v, u, _ in bad:
        errs.append(f"{where}: the number {v:g}{(' ' + u) if u else ''} is not in the cited evidence.")
    if ctype == "cause":
        hs = next((e for e in cited if e.get("id", "").startswith("hs_")), None)
        codes = {x["code"] for x in hs["causes"]} if hs else set()
        if not hs or c.get("code") not in codes:
            errs.append(f"{where}: a cause claim must cite a hotspot and one of its attributed cause codes ({', '.join(sorted(codes)) or 'none'}); got {c.get('code')!r}.")
    if ctype == "hotspot_location" and not any(e.get("id", "").startswith("hs_") for e in cited):
        errs.append(f"{where}: a hotspot_location claim must cite the hotspot.")
    if PREDICTION.search(text):
        errs.append(f"{where}: a temperature change is predicted for the site; the engine does not predict temperatures.")
    for m in SIMULATION.finditer(text):
        if not NEGATION.search(text[:m.start()]):
            errs.append(f"{where}: claims a simulation or microclimate model was run (it was not).")
            break
    for rx, label in FORBIDDEN:
        if rx.search(text):
            errs.append(f"{where}: forbidden claim ({label}).")
    return errs


def validate_answer(raw: str | dict, pack: dict) -> tuple[dict | None, list[str]]:
    """An answer to a free-text question: {"answer": [claims]}, each claim checked like an insight claim."""
    out, errs = parse(raw)
    if out is None:
        return None, errs
    if not isinstance(out.get("answer"), list) or not out["answer"]:
        return out, ["The object must have a non-empty 'answer' list of claims."]
    idx, allowed = index(pack), _allowed(pack["site"])
    for c in out["answer"]:
        errs += check_claim(c, idx, pack, allowed, "answer")
    return out, errs


def validate(raw: str | dict, pack: dict, *, require_blocks: bool = True) -> tuple[dict | None, list[str]]:
    """Return (output, errors). errors == [] means the output may be shown."""
    out, errs = parse(raw)
    if out is None:
        return None, errs
    idx = index(pack)
    global_allowed = _allowed(pack["site"])
    if require_blocks:
        missing = [b for b in BLOCKS if not isinstance(out.get(b), list)]
        if missing:
            errs.append(f"Missing block(s): {', '.join(missing)} (each must be a list).")
    for where, c in _claims(out):
        errs += check_claim(c, idx, pack, global_allowed, where)
    if require_blocks:
        seen = []
        for c in out.get("top_issues") or []:
            for i in (c.get("evidence_ids") or []) if isinstance(c, dict) else []:
                if str(i).startswith("hs_") and str(i) not in seen:
                    seen.append(str(i))
        if seen != pack["order"][:len(seen)]:
            errs.append("top_issues must follow the computed priority order: " + ", ".join(pack["order"][:max(len(seen), 3)]) + ".")
        cited = {str(i) for _, c in _claims(out) if isinstance(c, dict) and c.get("claim_type") == "limitation" for i in c.get("evidence_ids") or []}
        gap = [lim["id"] for lim in pack["limitations"] if lim["id"] not in cited]
        if gap:
            errs.append(f"Disclosure gap: limitation(s) {', '.join(gap)} are not disclosed by any limitation claim.")
        wanted = [q["id"] for q in pack["questions"]]
        got = [a.get("question_id") for a in out.get("answers") or [] if isinstance(a, dict)]
        if sorted(got) != sorted(wanted):
            errs.append(f"answers must contain exactly one entry per question id: {', '.join(wanted) or 'none'}.")
    return out, errs
