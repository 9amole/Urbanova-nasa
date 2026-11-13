"""Grounded explanations (FR-24, Module 9).

This build uses deterministic, template-based explanations generated only from
stored metrics, findings and sources — no generative model is called, so nothing
can be invented. Every answer lists the record IDs it is based on. A generative
LLM layer is a documented Phase 2 decision (docs/DECISIONS.md).
"""

from __future__ import annotations

import re

GLOSSARY = {
    "lst": ("Land surface temperature (LST)", "The radiative temperature of the ground, roofs and other surfaces seen by a satellite's thermal sensor at overpass time. It is usually higher than air temperature on sunny days and is not the temperature a pedestrian feels."),
    "ndvi": ("NDVI", "Normalised Difference Vegetation Index = (NIR − Red) / (NIR + Red). Values near 0 or below indicate bare, built or water surfaces; higher values indicate green vegetation. It does not measure shade, canopy health or cooling."),
    "air temperature": ("Air temperature", "The temperature of the air at 2 m above ground. In this build it comes from gridded reanalysis (about 25 km), describing regional rather than site conditions."),
    "reanalysis": ("Reanalysis", "A gridded reconstruction of past weather that combines a weather model with historical observations. It is not a direct measurement at the site."),
    "building coverage": ("Building coverage ratio", "Total building footprint area divided by the site area, × 100. URBANOVA uses the site boundary area unless a verified plot area has been chosen as the basis."),
    "thermal comfort": ("Thermal comfort", "How people perceive heat, which depends on air temperature, radiation, wind, humidity, clothing and activity. Phase 1 does not model thermal comfort."),
    "solar time": ("True solar time", "Time based on the sun's position: 12:00 is when the sun is highest. It differs from clock time by the time-zone offset, longitude and the equation of time."),
    "hottest decile": ("Hottest decile", "The 10% of pixels in the study area with the highest land surface temperature in the composite. A site share far above 10% indicates a relative concentration of hot surfaces."),
    "etccdi": ("ETCCDI indices", "Standard climate-extremes indices defined by the WMO Expert Team on Climate Change Detection and Indices, e.g. SU (days with Tmax > 25 °C) and TR (nights with Tmin > 20 °C)."),
}
# Questions asking for a quantified future change. "How much" alone is an ordinary question ("how much sunlit paving is there?").
PREDICTION = re.compile(r"(how many degrees|by how many|reduce .*temperature by|cool(er)? by|predict|forecast|guarantee|"
                        r"how much (cooler|warmer|hotter|colder|lower|higher)|how much will .*(cool|temperature|warm|heat|utci)|will .*\b(be|get)\b.*(cooler|warmer|°c))", re.I)
HOTSPOT_Q = re.compile(r"\bhot ?spots?\b|\b(first|priorit|worst|most severe)", re.I)
TOPICS = [
    (re.compile(r"\b(trends?|warming|getting (hotter|warmer|colder|wetter|drier)|chang(ing|ed) (since|over)|per decade|since 19\d\d|significan|greener|green cover|losing green|heating faster|than (its|the) surround)", re.I), ("climate_trends", "site_trends"), "trend"),
    (re.compile(r"\b(lst|surface temp|hot ?spot|heat)", re.I), ("surface_heat", "climate_baseline"), "heat"),
    (re.compile(r"\b(ndvi|vegetat|green|tree|plant)", re.I), ("vegetation", "urban_form"), "vegetation"),
    (re.compile(r"\b(shade|shadow|sun|solar)", re.I), ("solar_shade",), "solar"),
    (re.compile(r"\b(coverage|compl|rule|planning|setback|height|far\b)", re.I), ("planning_compliance", "urban_form"), "planning"),
    (re.compile(r"\b(climate|air temp|hot days|weather|humid)", re.I), ("climate_baseline",), "heat"),
    (re.compile(r"\b(missing|data gap|quality|blocked|unavailable)", re.I), (), "data"),
]


RELEVANCE = [(re.compile(r"green|vegetat|tree|ndvi", re.I), "green"), (re.compile(r"hot|warm|heat|temperat|cool", re.I), "temperat"),
             (re.compile(r"rain|wet|dry|drought", re.I), "rain"), (re.compile(r"surround|neighbou?r|faster", re.I), "surround"),
             (re.compile(r"\bsite\b|plot|boundary", re.I), "site"), (re.compile(r"humid", re.I), "humid"), (re.compile(r"sun|solar|dimm", re.I), "sun")]


def _relevance(question: str, text: str) -> int:
    """How many of the question's topics a result line mentions (to put the asked-about rows first)."""
    return sum(1 for pat, word in RELEVANCE if pat.search(question) and word in text.lower())


def explain_finding(f: dict, recs: list[dict]) -> dict:
    parts = [f"Finding: {f['title']} (classification: {f['classification'].replace('_', ' ')}; severity: {f['severity']})."]
    for e in f.get("evidence") or []:
        if "value" in e and e.get("label"):
            parts.append(f"Evidence: {e['label']} = {e['value']:g} {e.get('unit') or ''} — {e.get('method', '')}.")
        elif e.get("reason"):
            parts.append(f"Reason: {e['reason']}")
    if f.get("severity_rule"):
        parts.append(f"Why this severity: {f['severity_rule']}")
    for lim in f.get("limitations") or []:
        parts.append(f"Limitation: {lim}")
    for r in recs:
        parts.append(f"Related recommendation: {r['title']} — expected direction: {r['expected_direction']}")
    return {"text": " ".join(parts), "based_on": [f["id"]] + [e.get("metric_id") for e in f.get("evidence") or [] if e.get("metric_id")] + [r["id"] for r in recs],
            "generator": "deterministic template (no generative AI)"}


def answer(question: str, bundle: dict) -> dict:
    q = (question or "").strip()
    if not q:
        return {"answer": "Ask a question about this analysis run.", "based_on": [], "sufficient_evidence": False}
    if PREDICTION.search(q):
        return {"answer": "URBANOVA Phase 1 has no validated model for predicting quantified temperature or comfort changes, so it cannot give a number. "
                          "It can show how supported indicators (green-space share, hard-surface share, shade) differ between scenarios.",
                "based_on": [], "sufficient_evidence": False}
    for key, (term, text) in GLOSSARY.items():
        if re.search(rf"\b(what is|what's|define|meaning of|explain)\b.*{re.escape(key)}", q, re.I):
            return {"answer": f"{term}: {text}", "based_on": ["glossary"], "sufficient_evidence": True}
    hotspots = sorted(bundle.get("hotspots") or [], key=lambda h: h["number"])  # number 1 = highest priority
    if hotspots and HOTSPOT_Q.search(q):
        num = re.search(r"hot ?spot\s*#?(\d+)", q, re.I)
        picked = [h for h in hotspots if num and h["number"] == int(num.group(1))] or hotspots[:5]
        lines = [f"Hotspot {h['number']}: {' + '.join(h['type_labels'])}, {h['severity']} severity, {h['area_m2']:g} m²"
                 + (f"; likely cause: {h['likely_causes'][0]['text']}" if h.get("likely_causes") else "") for h in picked]
        head = "" if len(picked) == 1 else f"{len(hotspots)} design hotspot(s), highest priority first. "
        return {"answer": head + " • ".join(lines), "based_on": [h["id"] for h in picked], "sufficient_evidence": True,
                "note": "Answer assembled only from stored results of this run (no generative AI)."}
    for pattern, modules, category in TOPICS:
        if not pattern.search(q):
            continue
        status = bundle["run"].get("module_status") or {}
        lines, ids, found = [], [], []
        for mod in modules:
            st = status.get(mod, {})
            if st.get("status") in (None, "skipped"):
                continue
            if st.get("status") not in ("completed", "partial"):
                lines.append(f"{st.get('label', mod)} did not produce results ({st.get('status')}): {st.get('reason')}")
                continue
            steps = {r["metric_name"]: r["step"] for r in (st.get("details") or {}).get("rows") or [] if r.get("step")}
            for m in bundle["metrics"]:
                if m["module"] == mod and m["state"] == "available":
                    verdict = (m.get("extra") or {}).get("verdict")
                    s = steps.get(m["metric_name"])
                    found.append((m, f"{m['label']}: {m['value']:g} {m['unit'] or ''}" + (f" ({verdict.replace('_', ' ')})" if verdict else "")
                                  + (f"; step change around {s['year']}: {s['before']:.3g} before, {s['after']:.3g} after" if s else "")))
        # The rows the question is about come first (e.g. "green" → greenness), so they are never cut off (stable sort).
        found.sort(key=lambda x: -_relevance(q, x[0]["label"]))
        for m, line in found:
            lines.append(line)
            ids.append(m["id"])
        rel = [f for f in bundle["findings"] if f["category"] == category or (category == "data" and f["classification"] == "data_quality_issue")]
        for f in rel:
            lines.append(f"Finding ({f['severity']}): {f['title']}. {f.get('summary') or ''}")
            ids.append(f["id"])
        if not lines:
            break
        return {"answer": " • ".join(lines[:14]), "based_on": ids[:14], "sufficient_evidence": True,
                "note": "Answer assembled only from stored results of this run (no generative AI)."}
    return {"answer": "The analysis results of this run do not contain evidence to answer that question. URBANOVA answers only from its stored metrics, findings and sources.",
            "based_on": [], "sufficient_evidence": False}
