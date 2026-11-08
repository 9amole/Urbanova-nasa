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
MAX_METRICS = 40


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
    cited = {mid for h in hotspots for s in h["supporting_metrics"] for mid in s.get("source_metric_ids") or []}
    keep = metric_rows[:MAX_METRICS] + [m for m in metric_rows[MAX_METRICS:] if m["id"] in cited]  # every id a hotspot cites stays citable
    metrics = [{"id": m["id"], "label": m["label"], "value": _num(m["value"]), "unit": m["unit"], "module": m["module"],
                "condition": m.get("condition") or CONDITION.get(m["module"])} for m in keep]
    options = []
    for r in bundle.get("recommendations") or []:
        for o in r.get("options") or []:
            if o.get("separate") or not set(o.get("roles") or []) & {"best_value", "largest_benefit"}:
                continue
            e, c = o["effect"], o["cost"]
            options.append({"id": o["id"], "hotspot_id": r.get("hotspot_id"), "intervention": o["title"], "size": o["intensity_label"],
                            "roles": o["roles"], "effect": {"unit": e["unit"], "before": _num(e["before"]), "after": _num(e["after"]), "change_pct": e["change_pct"]},
                            "cost": {"status": c.get("status"), "typical": c.get("typical"), "currency": c.get("currency")}})
    lims, seen = [], set()
    for mod, st in ran.items():
        for text in st.get("limitations") or []:
            if text not in seen:
                seen.add(text)
                lims.append({"id": f"lim_{len(lims) + 1}", "module": mod, "text": text})
    if any(h.get("severity_capped") for h in hotspots):
        lims.append({"id": f"lim_{len(lims) + 1}", "module": "hotspots",
                     "text": "Hotspot severities are capped at moderate while some project-choice values are not approved."})
    pack = {
        "pack_version": "ev-1",
        "site": {"run_id": run["id"], "status": run["status"], "hotspot_count": len(hotspots), "analyses_run": len(ran),
                 "analyses_completed": sum(1 for v in ran.values() if v["status"] == "completed"), "solar_hours": "07:00–17:00 solar time (11 hours)",
                 "modules": [{"id": k, "label": v.get("label"), "status": v["status"], "reason": v.get("reason")} for k, v in ran.items()]},
        "order": [h["id"] for h in hotspots],
        "hotspots": [{"id": h["id"], "number": h["number"], "types": h["types"], "type_labels": h["type_labels"], "area_m2": h["area_m2"],
                      "site_share_pct": h["site_share_pct"], "size_class": h["size_class"], "intensity": h["intensity"], "severity": h["severity"],
                      "severity_capped": bool(h.get("severity_capped")), "site_wide": bool(h.get("site_wide")),
                      "causes": [{"code": c["code"], "text": c["text"]} for c in h["likely_causes"]],
                      "values": [{"name": s["name"], "label": s["label"], "value": s["value"], "unit": s["unit"], "metric_ids": s.get("source_metric_ids") or []}
                                 for s in h["supporting_metrics"]]} for h in hotspots],
        "site_context": [{"id": f"ctx_{i + 1}", "statement": c["statement"], "note": c.get("note")} for i, c in enumerate(bundle.get("site_context") or [])],
        "findings": [{"id": f["id"], "title": f["title"], "severity": f["severity"], "classification": f["classification"], "summary": f.get("summary")}
                     for f in bundle["findings"]],
        "metrics": metrics,
        "options": options,
        "limitations": lims,
        "questions": [{"id": f"q_{o['objective']}", "objective": o["objective"], "question": o["question"], "answerable": o["answerable"],
                       "supported_by": o.get("supported_by") or [], "not_available": o.get("not_available") or [], "explanation": o.get("explanation")}
                      for o in bundle.get("objectives") or []],
    }
    return pack


def canonical(pack: dict) -> str:
    return json.dumps(pack, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def pack_hash(pack: dict) -> str:
    return hashlib.sha256(canonical(pack).encode("utf-8")).hexdigest()[:24]


def index(pack: dict) -> dict[str, dict]:
    """Every citable id → its entry in the pack."""
    out = {}
    for key in ("hotspots", "site_context", "findings", "metrics", "options", "limitations", "questions"):
        for e in pack[key]:
            out[e["id"]] = e
    return out
