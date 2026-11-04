"""Scenario comparison and optional transparent multi-criteria scoring (FR-26, FR-27, Module 6)."""

from __future__ import annotations

from . import cost, db
from . import values as V
from .orchestrator import stale_modules

# Preferred direction for a heat-focused Phase 1 assessment. Metrics not listed
# are reported as "changed" without judging better/worse.
PREFERRED = {
    "green_space_pct": "higher", "green_space_m2": "higher", "open_space_pct": "higher",
    "hard_surface_pct": "lower",
    "solar_open_space_exposed_pct_0900": "lower", "solar_open_space_exposed_pct_1200": "lower", "solar_open_space_exposed_pct_1500": "lower",
    "solar_site_ground_exposed_pct_0900": "lower", "solar_site_ground_exposed_pct_1200": "lower", "solar_site_ground_exposed_pct_1500": "lower",
    "solar_open_space_shade_hours": "higher",
}
DIRECTION_NOTE = "Better/worse labels use a heat-focused preference (more green space and shade, less hard surface and sun exposure in the hot season). They may not suit cold climates or winter conditions."
EPS = 1e-6
COMPARABLE_CONFIG_KEYS = ("modules", "climate_period", "hot_months", "solar_date", "context_buffer_m", "lst_buffer_m", "ndvi_buffer_m", "max_scene_cloud_pct", "min_valid_site_fraction")


class ComparisonError(ValueError):
    pass


def latest_run(scenario_id: str) -> dict | None:
    runs = db.select("runs", "scenario_id = ? AND status IN ('succeeded', 'partially_succeeded')", [scenario_id], order="completed_at DESC")
    return runs[0] if runs else None


def _metrics(run_id: str) -> dict[str, dict]:
    return {m["metric_name"]: m for m in db.select("metrics", "run_id = ?", [run_id])}


def compare(project: dict, user: dict, baseline_id: str, alt_ids: list[str], criteria: list[dict] | None = None) -> dict:
    base = db.get("scenarios", baseline_id)
    if not base or base["project_id"] != project["id"] or base["kind"] != "baseline":
        raise ComparisonError("Select the project's baseline scenario as the reference.")
    if not alt_ids:
        raise ComparisonError("Select at least one alternative scenario.")
    alts = []
    for aid in alt_ids:
        a = db.get("scenarios", aid)
        if not a or a["project_id"] != project["id"] or a["kind"] != "alternative":
            raise ComparisonError(f"Scenario {aid} is not an alternative of this project.")
        alts.append(a)
    runs = {s["id"]: latest_run(s["id"]) for s in [base] + alts}
    missing = [s["name"] for s in [base] + alts if not runs[s["id"]]]
    if missing:
        raise ComparisonError(f"Run the analysis for: {', '.join(missing)}. Use 'Run all scenarios' so they share the same settings.")
    issues = []
    b_run = runs[base["id"]]
    for s in [base] + alts:
        st = stale_modules(runs[s["id"]])
        design_stale = [m for m, why in st.items() if "design" in why]
        other_stale = sorted({w for m, why in st.items() for w in why if w != "design"})
        if design_stale:
            issues.append(f"'{s['name']}' was edited after its last run ({', '.join(design_stale)}); re-run it before comparing.")
        elif other_stale:
            issues.append(f"Inputs changed after '{s['name']}' was analysed ({', '.join(other_stale)}); re-run all scenarios before comparing.")
    for a in alts:
        r = runs[a["id"]]
        diffs = [k for k in COMPARABLE_CONFIG_KEYS if b_run["configuration"].get(k) != r["configuration"].get(k)]
        if r["analysis_version"] != b_run["analysis_version"]:
            diffs.append("analysis_version")
        # The same climate/solar inputs must apply; otherwise the "same assumptions" requirement fails.
        if (b_run.get("fingerprint") or {}).get("period") != (r.get("fingerprint") or {}).get("period"):
            diffs.append("analysis period")
        if diffs:
            issues.append(f"'{a['name']}' was analysed with different settings ({', '.join(diffs)}); results are not comparable. Re-run all scenarios together.")
    if issues:
        return _store(project, user, base, alts, runs, criteria, {"comparable": False, "issues": issues})
    metrics = {s["id"]: _metrics(runs[s["id"]]["id"]) for s in [base] + alts}
    checks = {s["id"]: ((runs[s["id"]]["module_status"] or {}).get("planning_compliance", {}).get("details", {}) or {}).get("checks", []) for s in [base] + alts}
    findings_by = {s["id"]: db.select("findings", "run_id = ? AND classification != 'data_quality_issue'", [runs[s["id"]]["id"]]) for s in [base] + alts}
    costs = {a["id"]: cost.estimate(project, base, a) for a in alts}
    result = compare_core(base, alts, metrics, checks, findings_by, costs, criteria)
    return _store(project, user, base, alts, runs, criteria, result)


def compare_core(base: dict, alts: list[dict], metrics: dict[str, dict[str, dict]], checks: dict[str, list[dict]],
                 findings_by: dict[str, list[dict]], costs: dict[str, dict], criteria: list[dict] | None) -> dict:
    """Compare already-analysed designs. No database: everything is passed in, keyed by design id.
    base/alts need "id" and "name"; metrics[design_id] maps metric_name to a metric record."""
    bm = metrics[base["id"]]
    rows = []
    site_condition = []
    for a in alts:
        am = metrics[a["id"]]
        names = sorted({n for n, m in bm.items() if m["design_dependent"]} | {n for n, m in am.items() if m["design_dependent"]})
        for n in names:
            b, x = bm.get(n), am.get(n)
            row = {"alternative_id": a["id"], "metric": n, "label": (b or x)["label"], "unit": (b or x)["unit"],
                   "baseline": _val(b), "alternative": _val(x), "output_kind": (b or x)["output_kind"], "method": (b or x)["method"]}
            if not b or not x or b["state"] != V.AVAILABLE or x["state"] != V.AVAILABLE:
                why = (b or {}).get("reason") if not b or b["state"] != V.AVAILABLE else (x or {}).get("reason")
                row.update(outcome="not_comparable", reason=why or "Metric missing in one scenario.")
            elif b["unit"] != x["unit"] or b["method"] != x["method"]:
                row.update(outcome="not_comparable", reason="Different units or calculation methods.")
            else:
                d = x["value"] - b["value"]
                row["difference"] = round(d, 4)
                row["difference_pct"] = round(d / abs(b["value"]) * 100, 2) if abs(b["value"]) > EPS else None
                pref = PREFERRED.get(n)
                if abs(d) < EPS:
                    row["outcome"] = "unchanged"
                elif not pref:
                    row["outcome"] = "changed"
                else:
                    row["outcome"] = "improvement" if (d > 0) == (pref == "higher") else "regression"
                row["preferred_direction"] = pref
            rows.append(row)
        if not site_condition:
            for n, m in bm.items():
                if not m["design_dependent"] and m["state"] == V.AVAILABLE:
                    site_condition.append({"metric": n, "label": m["label"], "value": m["value"], "unit": m["unit"]})
    planning = []
    for a in alts:
        for c in checks[a["id"]]:
            bc = next((x for x in checks[base["id"]] if x["rule_type"] == c["rule_type"]), None)
            planning.append({"alternative_id": a["id"], "rule": c["label"], "baseline": (bc or {}).get("status", "not_assessed"), "alternative": c["status"],
                             "baseline_measured": (bc or {}).get("measured"), "alternative_measured": c.get("measured"), "limit": c["limit"]})
    findings_summary = {}
    for s in [base] + alts:
        fs = [f for f in findings_by.get(s["id"], []) if f["classification"] != "data_quality_issue"]
        findings_summary[s["id"]] = {lvl: sum(1 for f in fs if f["severity"] == lvl) for lvl in ("high", "moderate", "low")}
    not_comparable = {r["metric"] for r in rows if r["outcome"] == "not_comparable"}
    ranking = _rank(criteria, base, alts, bm, {a["id"]: metrics[a["id"]] for a in alts}, not_comparable) if criteria else None
    return {
        "comparable": True, "rows": rows, "site_conditions_shared": site_condition,
        "site_conditions_note": "Climate, satellite LST and NDVI describe the existing site and are identical for every scenario. Phase 1 has no validated model to predict how a design changes them, so they are not compared.",
        "planning": planning, "findings_by_severity": findings_summary, "costs": costs, "ranking": ranking, "direction_note": DIRECTION_NOTE,
        "statement": "This comparison shows differences in supported indicators. It does not identify a universally optimal design.",
    }


def _val(m: dict | None):
    if not m:
        return {"state": "missing"}
    return {"state": m["state"], "value": m["value"], "reason": m["reason"]}


def _rank(criteria, base, alts, bm, alt_metrics, not_comparable: set) -> dict:
    """Min–max normalised weighted sum over user-chosen criteria. Missing criteria are excluded, never scored 0."""
    scen = [(base["id"], base["name"], bm)] + [(a["id"], a["name"], alt_metrics[a["id"]]) for a in alts]
    per_criterion, notes = [], []
    for c in criteria:
        name = c.get("metric")
        try:
            w = float(c.get("weight", 0))
        except (TypeError, ValueError):
            raise ComparisonError("Criterion weights must be numbers.")
        if name not in PREFERRED:
            raise ComparisonError(f"'{name}' has no agreed preferred direction and cannot be used as a ranking criterion.")
        if w <= 0:
            continue
        if name in not_comparable:
            notes.append(f"{name} is not comparable between the scenarios and was excluded from scoring.")
            continue
        vals = {sid: m[name]["value"] for sid, _, m in scen if name in m and m[name]["state"] == V.AVAILABLE}
        if len(vals) < len(scen):
            notes.append(f"{name} is missing for some scenarios; those scenarios are not scored on it (not scored as zero).")
        if len(vals) < 2:
            continue
        lo, hi = min(vals.values()), max(vals.values())
        scores = {sid: (1.0 if hi - lo < EPS else ((v - lo) / (hi - lo) if PREFERRED[name] == "higher" else (hi - v) / (hi - lo))) for sid, v in vals.items()}
        unit = next((m[name].get("unit") for _, _, m in scen if name in m), None)
        base_v = vals.get(base["id"])
        # Raw values and differences travel with the scores: with min-max scaling a 0.1-point and a
        # 40-point difference both score 0 vs 1, so the score alone can overstate small differences.
        per_criterion.append({"metric": name, "label": next((m[name].get("label") for _, _, m in scen if name in m), name), "unit": unit,
                              "weight": w, "direction": PREFERRED[name], "scores": scores,
                              "values": vals, "difference_vs_baseline": {sid: (round(v - base_v, 4) if base_v is not None else None) for sid, v in vals.items()},
                              "range": round(hi - lo, 4)})
    agg = {}
    for sid, sname, _ in scen:
        num = sum(pc["weight"] * pc["scores"][sid] for pc in per_criterion if sid in pc["scores"])
        den = sum(pc["weight"] for pc in per_criterion if sid in pc["scores"])
        agg[sid] = {"name": sname, "score": round(num / den, 3) if den else None, "weight_coverage": round(den / sum(pc["weight"] for pc in per_criterion), 2) if per_criterion else 0}
    small_set = None
    if len(scen) < 3:
        small_set = ("Only two scenarios are compared, so every criterion scores 1 for the better scenario and 0 for the other, however small "
                     "the difference. Read the raw values and differences shown with each score before relying on the ranking.")
    return {"method": "Min–max normalisation across the compared scenarios (0 = worst, 1 = best on that criterion) and a weighted mean using your weights.",
            "criteria": per_criterion, "aggregate": agg, "notes": notes, "small_set_warning": small_set, "scenario_count": len(scen),
            "caveat": "Scores are relative to the scenarios compared and depend entirely on the chosen weights; they do not show that any design is optimal."}


def _store(project, user, base, alts, runs, criteria, result) -> dict:
    rec = {"id": db.new_id("cmp"), "project_id": project["id"], "baseline_scenario_id": base["id"], "alternative_scenario_ids": [a["id"] for a in alts],
           "run_ids": {sid: (r or {}).get("id") for sid, r in runs.items()}, "criteria": criteria, "result": result, "created_by": user["id"], "created_at": db.now()}
    db.insert("comparisons", rec)
    rec["scenario_names"] = {base["id"]: base["name"], **{a["id"]: a["name"] for a in alts}}
    return rec
