"""Deterministic sentence builder (PHASE2_SPEC §5 step 5): the same blocks as the AI output, built only from the
evidence pack. Used when the model is off, the project has not switched AI text on, the budget is used up, or an
AI output fails validation twice. Its output passes the same validator (tested)."""

from __future__ import annotations


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}".rstrip("0").rstrip(".") if abs(v) < 1e5 else f"{v:,.0f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def _hotspot_claims(h: dict) -> list[dict]:
    cap = " (capped while project-choice values await approval)" if h.get("severity_capped") else ""
    where = "site-wide" if h.get("site_wide") else f"{_fmt(h['site_share_pct'])} % of the site"
    out = [{"text": f"Hotspot {h['number']} ({' + '.join(h['type_labels']).lower()}): {_fmt(h['area_m2'])} m², {where}; severity {h['severity']}{cap}.",
            "claim_type": "hotspot_location", "evidence_ids": [h["id"]]}]
    vals = {v["label"]: v for v in h.get("values") or []}
    sun = next((v for k, v in vals.items() if k.startswith("Highest UTCI in sun")), None)
    shade = next((v for k, v in vals.items() if k.startswith("UTCI in shade")), None)
    if sun and shade:
        out.append({"text": f"UTCI in sun reaches {_fmt(sun['value'])} °C there, against {_fmt(shade['value'])} °C in shade.",
                    "claim_type": "metric_value", "evidence_ids": [h["id"]]})
    for c in h["causes"][:3]:
        out.append({"text": f"Likely cause ({c['code']}): {c['text']}.", "claim_type": "cause", "evidence_ids": [h["id"]], "code": c["code"]})
    return out


def build(pack: dict) -> dict:
    s = pack["site"]
    hs = pack["hotspots"]
    site = [{"text": f"{s['analyses_completed']} of {s['analyses_run']} analyses completed fully; the run status is {s['status'].replace('_', ' ')}.",
             "claim_type": "metric_value", "evidence_ids": []},
            {"text": (f"{s['hotspot_count']} design hotspots were found on the drawing." if s["hotspot_count"] else "No design hotspot was found on the drawing."),
             "claim_type": "metric_value", "evidence_ids": []}]
    site += [{"text": c["statement"], "claim_type": "metric_value", "evidence_ids": [c["id"]]} for c in pack["site_context"]]
    top = [c for h in hs[:3] for c in _hotspot_claims(h)]
    first = []
    if hs:
        h = hs[0]
        opt = next((o for o in pack["options"] if o["hotspot_id"] == h["id"]), None)  # the pack keeps only best-value options
        if opt:
            e = opt["effect"]
            first.append({"text": f"Address hotspot {h['number']} first. The measured best-value option is {opt['intervention'].lower()} ({opt['size']}): "
                                  f"{_fmt(e['before'])} → {_fmt(e['after'])} {e['unit']} (−{_fmt(e['change_pct'])} %).",
                          "claim_type": "recommendation", "evidence_ids": [h["id"], opt["id"]]})
        else:
            first.append({"text": f"Address hotspot {h['number']} first: it has the highest priority (severity, then area).",
                          "claim_type": "recommendation", "evidence_ids": [h["id"]]})
    answers = []
    for q in pack["questions"]:
        if q["answerable"]:
            basis = "; ".join(q["supported_by"]) or "the readiness check and data-quality findings"
            text = f"Answered with the available evidence from: {basis}." + (f" Still missing: {'; '.join(q['not_available'])}." if q["not_available"] else "")
            answers.append({"question_id": q["id"], "claims": [{"text": text, "claim_type": "explanation_of_term", "evidence_ids": [q["id"]]}]})
        else:
            why = "; ".join(q["not_available"]) or "the required results were not produced"
            answers.append({"question_id": q["id"], "claims": [{"text": f"Cannot be answered with the available data, because: {why}.",
                                                                "claim_type": "limitation", "evidence_ids": [q["id"]]}]})
    lims = [{"text": lim["text"], "claim_type": "limitation", "evidence_ids": [lim["id"]]} for lim in pack["limitations"]]
    return {"site_status": site, "top_issues": top, "attention_first": first, "answers": answers, "limitations": lims}
