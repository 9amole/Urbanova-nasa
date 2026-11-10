"""Project dashboard (stage S10): every important output of a project on one page, read from what is stored.

Nothing is recalculated and no model is called. The figures come from the latest successful baseline run and the latest
run of each active alternative; the option per hotspot is the same choice the report and the 1 · Site summary explain
(report_front.suited). Missing values stay missing (state and reason), and costs are never added across currencies.
"""

from __future__ import annotations

from . import comparison, cost, db, orchestrator, projects, readiness
from .report_front import _options_by_hotspot, suited

TILE_METRICS = [
    ("green_space_pct", "Green-space share"),
    ("heat_stress_hard_surface_very_strong_m2h", "Sun-exposed hard surface at very strong heat stress"),
    ("solar_open_space_shade_hours", "Shade on open space"),
    ("building_coverage_ratio_pct", "Building coverage"),
]
DESIGN_INDICATORS = [k for k, v in comparison.CRITERIA.items() if v["source"] == "metric"] + ["hotspot_total_area_m2", "building_coverage_ratio_pct"]
CLIMATE_METRICS = ["climate_hot_season_mean_tmax_c", "climate_mean_annual_TX35_days", "climate_mean_annual_TR_days", "climate_tmax_record_c"]
SURFACE_METRICS = ["lst_site_median_c", "lst_site_minus_surroundings_k", "lst_site_percentile_in_surroundings", "ndvi_site_mean"]


def _metric(m: dict | None, name: str, label: str | None = None) -> dict:
    """A stored metric as a dashboard value; a missing one keeps its state and reason (never 0)."""
    if not m:
        return {"name": name, "label": label or name, "state": "unavailable", "value": None, "unit": None,
                "reason": "Not calculated in this run (the analysis was not selected or could not run)."}
    ok = m["state"] == "available"
    return {"name": name, "label": label or m["label"], "state": m["state"], "value": m["value"] if ok else None, "unit": m["unit"],
            "reason": None if ok else m.get("reason"), "output_kind": m.get("output_kind"), "condition": m.get("condition"), "id": m["id"]}


def _checks(run: dict) -> list[dict]:
    return (((run.get("module_status") or {}).get("planning_compliance") or {}).get("details") or {}).get("checks") or []


def _hotspot_rows(bundle: dict) -> tuple[list[dict], dict]:
    """Every hotspot with its suited option, and the cost totals of those options per currency."""
    opts = _options_by_hotspot(bundle)
    rows, totals, priced, unpriced, test = [], {}, 0, 0, False
    for h in sorted(bundle["hotspots"], key=lambda x: x["number"]):
        o, why = suited(opts.get(h["id"]) or [])
        option = None
        if o:
            c, e = o["cost"], o["effect"]
            option = {"id": o["id"], "title": o["title"], "size": o["intensity_label"], "why": why,
                      "effect": {"label": e.get("label"), "unit": e.get("unit"), "before": e.get("before"), "after": e.get("after"), "change_pct": e.get("change_pct")},
                      "cost": {"status": c.get("status"), "low": c.get("low"), "typical": c.get("typical"), "high": c.get("high"), "currency": c.get("currency"),
                               "confidence": (c.get("confidence") or {}).get("level")}}
            if c.get("status") == "priced" and c.get("currency"):
                t = totals.setdefault(c["currency"], {"low": 0.0, "typical": 0.0, "high": 0.0, "options": 0})
                for k in ("low", "typical", "high"):
                    t[k] += float(c.get(k) or 0)
                t["options"] += 1
                priced += 1
                test = test or c["currency"] == "XTS" or "TEST" in str(c.get("source") or "").upper()
            else:
                unpriced += 1
        rows.append({"id": h["id"], "number": h["number"], "types": h["type_labels"], "severity": h["severity"], "severity_capped": bool(h.get("severity_capped")),
                     "area_m2": h["area_m2"], "site_share_pct": h.get("site_share_pct"), "site_wide": bool(h.get("site_wide")),
                     "main_cause": h["likely_causes"][0]["text"] if h["likely_causes"] else None, "option": option})
    costs = {"by_currency": {k: {**v, **{x: round(v[x], 2) for x in ("low", "typical", "high")}} for k, v in totals.items()},
             "priced_options": priced, "price_required_options": unpriced, "test_prices": test}
    return rows, costs


def _designs(project: dict, base: dict, base_run: dict) -> dict:
    """Baseline and active alternatives with their latest runs and the comparison indicators."""
    scen = [base] + db.select("scenarios", "project_id = ? AND kind = 'alternative' AND status = 'active'", [project["id"]], order="created_at")
    out = []
    for s in scen:
        r = base_run if s["id"] == base["id"] else comparison.latest_run(s["id"])
        if not r:
            out.append({"scenario_id": s["id"], "name": s["name"], "kind": s["kind"], "run": None, "note": "Not analysed yet."})
            continue
        ms = comparison._metrics(r["id"])
        hs = db.select("hotspots", "run_id = ?", [r["id"]])
        checks = [c for c in _checks(r) if c["status"] in ("passes", "fails")]
        d = {"scenario_id": s["id"], "name": s["name"], "kind": s["kind"], "run": {"id": r["id"], "status": r["status"], "completed_at": r.get("completed_at")},
             "indicators": {k: _metric(ms.get(k), k, (comparison.CRITERIA.get(k) or {}).get("label")) for k in DESIGN_INDICATORS},
             "hotspots": {"count": len(hs), **{lvl: sum(h["severity"] == lvl for h in hs) for lvl in ("high", "moderate", "low")}},
             "rules_failed": sum(c["status"] == "fails" for c in checks) if checks else None}
        if s["kind"] == "alternative":
            est = cost.estimate(project, base, s)
            d["cost"] = {"status": est["status"], "totals_by_currency": est.get("totals_by_currency") or {}}
            diffs = [k for k in comparison.COMPARABLE_CONFIG_KEYS if (base_run.get("configuration") or {}).get(k) != (r.get("configuration") or {}).get(k)]
            if r.get("analysis_version") != base_run.get("analysis_version"):
                diffs.append("analysis_version")
            d["comparable"] = not diffs
            d["not_comparable_because"] = diffs
        out.append(d)
    latest = next(iter(db.select("comparisons", "project_id = ?", [project["id"]], order="created_at DESC")), None)
    verdict = None
    if latest:
        res = latest.get("result") or {}
        verdict = {"id": latest["id"], "created_at": latest["created_at"], "comparable": res.get("comparable"), "issues": res.get("issues") or [],
                   "mode": (res.get("choice") or {}).get("mode"), "statement": (res.get("choice") or {}).get("statement")}
    return {"designs": out, "directions": {k: comparison.DIRECTIONS.get(k) for k in DESIGN_INDICATORS}, "latest_comparison": verdict}


def build(project: dict) -> dict:
    base = next(iter(db.select("scenarios", "project_id = ? AND kind = 'baseline' AND status = 'active'", [project["id"]])), None)
    run = comparison.latest_run(base["id"]) if base else None
    if run:  # the run bundle is built once below; only the readiness counts are needed from the summary's logic
        rd = readiness.assess(project)
        summary = {"completeness": {"readiness": {s: sum(1 for a in rd["analyses"] if a["status"] == s) for s in ("ready", "ready_with_limitations", "blocked")}}}
    else:
        summary = projects.dashboard(project)  # its empty state names the next step
    runs = db.select("runs", "project_id = ?", [project["id"]], order="COALESCE(completed_at, started_at, id) DESC")
    names = {s["id"]: s["name"] for s in db.select("scenarios", "project_id = ?", [project["id"]])}
    activity = [{"id": r["id"], "scenario": names.get(r.get("scenario_id")), "status": r["status"], "started_at": r.get("started_at"),
                 "completed_at": r.get("completed_at")} for r in runs[:5]]
    reports = [{"id": r["id"], "run_id": r["run_id"], "status": r["status"], "created_at": r["created_at"], "pdf": bool(r.get("pdf_path")), "html": bool(r.get("html_path"))}
               for r in db.select("reports", "project_id = ?", [project["id"]], order="created_at DESC")[:5]]
    head = {"project": {"id": project["id"], "name": project["name"], "synthetic_data": bool(project.get("synthetic_data"))},
            "readiness": summary["completeness"]["readiness"], "activity": activity, "reports": reports}
    if not run:
        return {**head, "state": "empty", "next_step": summary.get("next_step")}

    b = orchestrator.run_bundle(run)
    metrics = {m["metric_name"]: m for m in b["metrics"]}
    rows, costs = _hotspot_rows(b)
    checks = _checks(run)
    assessed = [c for c in checks if c["status"] in ("passes", "fails")]
    hs_status = ((run.get("module_status") or {}).get("hotspots") or {})
    located = [r for r in rows if not r["site_wide"]]
    tiles = {
        "hotspots": {"assessed": hs_status.get("status") in ("completed", "partial"), "count": len(rows),
                     **{lvl: sum(r["severity"] == lvl for r in rows) for lvl in ("high", "moderate", "low")},
                     "reason": None if hs_status.get("status") in ("completed", "partial") else (hs_status.get("reason") or "Design hotspots were not part of this run.")},
        "hotspot_area": {"area_m2": round(sum(r["area_m2"] for r in located), 1), "site_share_pct": round(sum(r["site_share_pct"] or 0 for r in located), 1)},
        "metrics": [_metric(metrics.get(k), k, label) for k, label in TILE_METRICS],
        "rules": {"checked": len(assessed), "failed": sum(c["status"] == "fails" for c in assessed), "not_checked": len(checks) - len(assessed)},
        "costs": costs,
    }
    lc = sorted([m for m in b["metrics"] if m["metric_name"].startswith("landcover_site_") and m["state"] == "available" and (m["value"] or 0) > 0],
                key=lambda m: -m["value"])
    ms = run.get("module_status") or {}
    ins = next(iter(db.select("insights", "run_id = ?", [run["id"]], order="created_at DESC")), None)
    blocks = (ins or {}).get("blocks") or {}
    provisional = [{"number": r["number"], "severity": r["severity"]} for r in rows if r["severity_capped"]]
    provisional_findings = [f["title"] for f in b["findings"] if ((f.get("threshold") or {}).get("status") == "provisional_not_approved")]
    return {
        **head, "state": "ready", "next_step": None,
        "run": {"id": run["id"], "status": run["status"], "completed_at": run.get("completed_at"), "scenario": names.get(run.get("scenario_id")),
                "stale": bool(b["run"]["stale_modules"]), "stale_inputs": sorted({w for why in b["run"]["stale_modules"].values() for w in why})},
        "tiles": tiles,
        "hotspots": rows,
        "designs": _designs(project, base, run),
        "rules": [{"label": c["label"], "status": c["status"], "measured": c.get("measured"), "limit": c.get("limit"), "unit": c.get("unit"),
                   "operator": c.get("operator"), "source": c.get("rule_source"), "note": c.get("label_result") or c.get("reason")} for c in checks],
        "existing": {"context": [c["statement"] for c in b.get("site_context") or []],
                     "land_cover": [{"label": m["label"], "value": m["value"], "unit": m["unit"], "id": m["id"]} for m in lc],
                     "climate": [_metric(metrics.get(k), k) for k in CLIMATE_METRICS],
                     "climate_limitations": (ms.get("climate_baseline") or {}).get("limitations") or [],
                     "surface": [_metric(metrics.get(k), k) for k in SURFACE_METRICS]},
        "insights": ({"generated_by": ins["generated_by"], "label": ins.get("label"), "model": ins.get("model"),
                      "site_status": blocks.get("site_status") or [], "attention_first": blocks.get("attention_first") or []} if ins else None),
        "quality": {"modules": [{"key": k, "label": v.get("label") or k, "status": v["status"], "reason": v.get("reason")} for k, v in ms.items() if v.get("status") != "skipped"],
                    "provisional_hotspots": provisional, "provisional_findings": provisional_findings,
                    "datasets": [{"source_id": d.get("source_id"), "dataset_type": d.get("dataset_type"), "license": d.get("license"), "attribution": d.get("attribution"),
                                  "approval_label": d.get("approval_label")} for d in b["datasets"] if d],
                    "limitations": sorted({t for v in ms.values() for t in (v.get("limitations") or [])})[:20]},
    }
