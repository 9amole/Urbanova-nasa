"""The evidence pack: the only material the AI may use (PHASE2_SPEC §5 step 1).

Built from a run bundle (orchestrator.run_bundle or the same keys from the engine). It holds module statuses,
the ranked design hotspots with their causes and values, site context, findings, key design metrics, the measured
best options, every limitation (with an id) and the project's selected questions. It never holds files, drawings,
geometry, names of people or keys. The priority order is computed here, not by the model.
"""

from __future__ import annotations

import hashlib
import json

from ..readiness import CONDITION

SEVERITY_RANK = {"high": 3, "moderate": 2, "low": 1, "info": 0}
# Size limits keep the pack (with the instruction and the reply) inside free-tier per-request token limits (~8k tokens).
MAX_METRICS = 20
DETAILED_HOTSPOTS = 5
VALUE_NAMES = ("sun_hours_hot_day", "very_strong_sun_hours", "utci_sun_peak_c", "utci_shade_peak_c", "hard_surface_share_pct", "nearest_canopy_m",
               "relative_shortfall_pct", "composition_pct")


def _first_sentence(text: str, limit: int = 160) -> str:
    s = (text or "").split(". ")[0].strip().rstrip(".")
    return (s[: limit - 1] + "…") if len(s) > limit else s


def _num(v):
    return round(v, 2) if isinstance(v, float) else v


def build(bundle: dict) -> dict:
    run = bundle["run"]
    ms = run.get("module_status") or {}
    ran = {k: v for k, v in ms.items() if v.get("status") != "skipped"}
    hotspots = sorted(bundle.get("hotspots") or [], key=lambda h: h["number"])
    metric_rows = [m for m in bundle["metrics"] if m["state"] == "available" and m["module"] not in ("interventions",)]
    # Design metrics first (they are what the design can change), then existing-land context.
    metric_rows.sort(key=lambda m: (CONDITION.get(m["module"]) != "design", m["module"], m["metric_name"]))
    metrics = [{"id": m["id"], "label": m["label"], "value": _num(m["value"]), "unit": m["unit"],
                "condition": m.get("condition") or CONDITION.get(m["module"])} for m in metric_rows[:MAX_METRICS]]
    options = []
    for r in bundle.get("recommendations") or []:
        for o in r.get("options") or []:
            if o.get("separate") or "best_value" not in (o.get("roles") or []):
                continue
            e, c = o["effect"], o["cost"]
            options.append({"id": o["id"], "hotspot_id": r.get("hotspot_id"), "intervention": o["title"], "size": o["intensity_label"],
                            "effect": {"unit": e["unit"], "before": _num(e["before"]), "after": _num(e["after"]), "change_pct": e["change_pct"]},
                            "cost": c.get("typical") if c.get("status") == "priced" else "price required", "currency": c.get("currency")})
    # One limitation entry per analysis (its statements' first sentences), so every limitation is still disclosed.
    lims = []
    for mod, st in ran.items():
        texts = [_first_sentence(t, 110) for t in (st.get("limitations") or [])[:3]]
        if texts:
            lims.append({"id": f"lim_{len(lims) + 1}", "analysis": st.get("label") or mod, "text": "; ".join(dict.fromkeys(texts))})
    if any(h.get("severity_capped") for h in hotspots):
        lims.append({"id": f"lim_{len(lims) + 1}", "analysis": "Design hotspots",
                     "text": "Hotspot severities are capped at moderate while some project-choice values are not approved."})
    pack = {
        "pack_version": "ev-2",
        "site": {"status": run["status"], "hotspot_count": len(hotspots), "analyses_run": len(ran),
                 "analyses_completed": sum(1 for v in ran.values() if v["status"] == "completed"), "solar_hours": "07:00–17:00 solar time (11 hours)",
                 "incomplete": [{"analysis": v.get("label") or k, "status": v["status"], "reason": _first_sentence(v.get("reason") or "", 140)}
                                for k, v in ran.items() if v["status"] != "completed"]},
        "order": [h["id"] for h in hotspots],
        "hotspots": [_hotspot(h, detailed=i < DETAILED_HOTSPOTS) for i, h in enumerate(hotspots)],
        "site_context": [{"id": f"ctx_{i + 1}", "statement": c["statement"]} for i, c in enumerate(bundle.get("site_context") or [])],
        # Hotspot findings repeat their hotspot, so only the others are listed.
        "findings": [{"id": f["id"], "title": f["title"], "severity": f["severity"], "summary": _first_sentence(f.get("summary") or "", 160)}
                     for f in bundle["findings"] if not f.get("hotspot_ids") or f["classification"] == "planning_rule_check"],
        "metrics": metrics,
        "options": options,
        "limitations": lims,
        "questions": [{"id": f"q_{o['objective']}", "question": o["question"], "answerable": o["answerable"],
                       "supported_by": o.get("supported_by") or [], "not_available": o.get("not_available") or []}
                      for o in bundle.get("objectives") or []],
    }
    return pack


def _hotspot(h: dict, detailed: bool) -> dict:
    """A hotspot entry; the values are cited through the hotspot's own id. Lower-priority hotspots get a short entry.
    `made_of` says what the hotspot is (e.g. parking 100 %, or a site-wide shortfall) so text can name the right place."""
    comp = next((s["value"] for s in h["supporting_metrics"] if s["name"] == "composition_pct"), None)
    made_of = "site-wide (no single place on the drawing)" if h.get("site_wide") else ", ".join(f"{k} {v:g} %" for k, v in (comp or {}).items()) or "open or green space"
    out = {"id": h["id"], "number": h["number"], "type_labels": h["type_labels"], "made_of": made_of, "area_m2": h["area_m2"], "site_share_pct": h["site_share_pct"],
           "severity": h["severity"], "causes": [{"code": c["code"], "text": c["text"]} for c in h["likely_causes"]]}
    if detailed:
        out.update(size_class=h["size_class"], intensity=h["intensity"], severity_capped=bool(h.get("severity_capped")), site_wide=bool(h.get("site_wide")),
                   values=[{"label": s["label"], "value": s["value"], "unit": s["unit"]} for s in h["supporting_metrics"]
                           if s["name"] in VALUE_NAMES and s["name"] != "composition_pct"])
    return out


def for_question(pack: dict) -> dict:
    """A lighter pack for a free-text question (no limitation list or selected questions), to fit per-minute limits."""
    return {k: v for k, v in pack.items() if k not in ("limitations", "questions")}


def canonical(pack: dict) -> str:
    return json.dumps(pack, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def pack_hash(pack: dict) -> str:
    return hashlib.sha256(canonical(pack).encode("utf-8")).hexdigest()[:24]


def index(pack: dict) -> dict[str, dict]:
    """Every citable id → its entry in the pack ("site" is the run summary block)."""
    out = {"site": pack["site"]}
    for key in ("hotspots", "site_context", "findings", "metrics", "options", "limitations", "questions"):
        for e in pack.get(key, []):
            out[e["id"]] = e
    return out
