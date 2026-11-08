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
    "heat_stress_hard_surface_very_strong_m2h": "lower", "heat_stress_open_space_very_strong_m2h": "lower",
    "hotspot_total_area_m2": "lower",
}
# Criteria a user may weight (PHASE2_SPEC §7; decided 2026-11-08). Water joins when a sourced litres/day value exists.
CRITERIA = {
    "heat_stress_hard_surface_very_strong_m2h": {"label": "Heat stress: sun-exposed hard surface (m²·h at very strong UTCI)", "direction": "lower", "source": "metric"},
    "heat_stress_open_space_very_strong_m2h": {"label": "Heat stress: sun-exposed open space (m²·h at very strong UTCI)", "direction": "lower", "source": "metric"},
    "green_space_pct": {"label": "Green-space share (%)", "direction": "higher", "source": "metric"},
    "solar_open_space_shade_hours": {"label": "Shade hours on open space (h)", "direction": "higher", "source": "metric"},
    "cost_typical": {"label": "Indicative cost (typical)", "direction": "lower", "source": "derived"},
    "compliance_failures": {"label": "Failed verified planning rules (count)", "direction": "lower", "source": "derived"},
}
DIRECTIONS = {**PREFERRED, **{k: v["direction"] for k, v in CRITERIA.items()}}
TRADEOFF_METRIC = "heat_stress_hard_surface_very_strong_m2h"
WEIGHT_STEP = 0.2  # robustness: each weight ±20 %, one at a time
NO_WINNER = ("There is no single best design: the choice depends on the project's priorities (for example heat stress, green space, cost "
             "or compliance). Set weights to rank the designs under your priorities.")
DIRECTION_NOTE = "Better/worse labels use a heat-focused preference (more green space and shade, less hard surface and sun exposure in the hot season). They may not suit cold climates or winter conditions."
EPS = 1e-6
COMPARABLE_CONFIG_KEYS = ("modules", "climate_period", "hot_months", "solar_date", "context_buffer_m", "lst_buffer_m", "ndvi_buffer_m", "max_scene_cloud_pct", "min_valid_site_fraction", "lst_downscale")


class ComparisonError(ValueError):
    pass


def latest_run(scenario_id: str) -> dict | None:
    runs = db.select("runs", "scenario_id = ? AND status IN ('succeeded', 'partially_succeeded')", [scenario_id], order="completed_at DESC")
    return runs[0] if runs else None


def _metrics(run_id: str) -> dict[str, dict]:
    return {m["metric_name"]: m for m in db.select("metrics", "run_id = ?", [run_id])}


def compare(project: dict, user: dict, baseline_id: str, alt_ids: list[str], criteria: list[dict] | None = None, budget: dict | None = None) -> dict:
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
    hotspots = {s["id"]: db.select("hotspots", "run_id = ?", [runs[s["id"]]["id"]]) for s in [base] + alts}
    result = compare_core(base, alts, metrics, checks, findings_by, costs, criteria, hotspots=hotspots, budget=budget)
    return _store(project, user, base, alts, runs, criteria, result)


def compare_core(base: dict, alts: list[dict], metrics: dict[str, dict[str, dict]], checks: dict[str, list[dict]],
                 findings_by: dict[str, list[dict]], costs: dict[str, dict], criteria: list[dict] | None,
                 hotspots: dict[str, list[dict]] | None = None, budget: dict | None = None) -> dict:
    """Compare already-analysed designs. No database: everything is passed in, keyed by design id.
    base/alts need "id" and "name"; metrics[design_id] maps metric_name to a metric record."""
    bm = metrics[base["id"]]
    rows = []
    site_condition = []
    for a in alts:
        am = metrics[a["id"]]
        names = sorted({n for n, m in bm.items() if V.condition_of(m) == V.DESIGN} | {n for n, m in am.items() if V.condition_of(m) == V.DESIGN})
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
                if V.condition_of(m) == V.EXISTING and m["state"] == V.AVAILABLE:
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
    scen = [base] + alts
    derived = _derived(scen, costs, checks)
    full = {s["id"]: {**metrics[s["id"]], **derived[s["id"]]} for s in scen}
    ranking = _rank(criteria, base, alts, full[base["id"]], {a["id"]: full[a["id"]] for a in alts}, not_comparable) if criteria else None
    return {
        "hotspots": _hotspot_summary(scen, hotspots) if hotspots is not None else None,
        "tradeoffs": {"chart": _tradeoff_points(scen, full), "give_up": _give_up(alts, costs)},
        "choice": _choice(scen, full, criteria, ranking, budget, not_comparable),
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
        if name not in DIRECTIONS:
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
        scores = {sid: (1.0 if hi - lo < EPS else ((v - lo) / (hi - lo) if DIRECTIONS[name] == "higher" else (hi - v) / (hi - lo))) for sid, v in vals.items()}
        unit = next((m[name].get("unit") for _, _, m in scen if name in m), None)
        base_v = vals.get(base["id"])
        # Raw values and differences travel with the scores: with min-max scaling a 0.1-point and a
        # 40-point difference both score 0 vs 1, so the score alone can overstate small differences.
        per_criterion.append({"metric": name, "label": next((m[name].get("label") for _, _, m in scen if name in m), name), "unit": unit,
                              "weight": w, "direction": DIRECTIONS[name], "scores": scores,
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


# ---------------------------------------------------------------- S5: derived criteria, trade-offs, choice, robustness
def parse_budget(amount, currency) -> dict | None:
    """{"amount", "currency"} from request fields, or None when neither is given."""
    if amount in (None, "") and not currency:
        return None
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise ComparisonError("budget_amount must be a number.")
    cur = str(currency or "").strip().upper()
    if amt <= 0 or len(cur) != 3 or not cur.isalpha():
        raise ComparisonError("Give a positive budget_amount and a 3-letter budget_currency, or neither.")
    return {"amount": amt, "currency": cur}


def _design_cost(c: dict | None) -> tuple[float | None, str | None, str]:
    """(typical total, currency, why missing) of an alternative's estimate; the baseline costs nothing extra."""
    if c is None:
        return 0.0, None, ""
    if c["status"] == "no_quantities":
        return 0.0, None, ""
    if c["status"] != "complete" or len(c["totals_by_currency"]) != 1:
        return None, None, "price required for some items" if c["status"] != "complete" else "costs in more than one currency"
    (cur, total), = c["totals_by_currency"].items()
    return float(total), cur, ""


def _derived(scen: list[dict], costs: dict, checks: dict) -> dict[str, dict]:
    """Pseudo-metrics for the derived criteria, in the metric-record shape _rank reads. Missing stays missing."""
    out = {}
    currencies = {_design_cost(costs.get(s["id"]))[1] for s in scen} - {None}
    for s in scen:
        total, cur, why = _design_cost(costs.get(s["id"]))
        d = {}
        if total is None or len(currencies) > 1:
            d["cost_typical"] = {"state": "unknown", "value": None, "unit": None, "label": CRITERIA["cost_typical"]["label"],
                                 "reason": why or "costs in different currencies cannot be compared"}
        else:
            d["cost_typical"] = {"state": V.AVAILABLE, "value": total, "unit": next(iter(currencies), None), "label": CRITERIA["cost_typical"]["label"]}
        assessed = [c for c in checks.get(s["id"]) or [] if c["status"] in ("passes", "fails")]
        d["compliance_failures"] = ({"state": V.AVAILABLE, "value": float(sum(c["status"] == "fails" for c in assessed)), "unit": "count",
                                     "label": CRITERIA["compliance_failures"]["label"]} if assessed else
                                    {"state": "unknown", "value": None, "unit": "count", "label": CRITERIA["compliance_failures"]["label"],
                                     "reason": "no verified rule was checked"})
        out[s["id"]] = d
    return out


def _hotspot_summary(scen: list[dict], hotspots: dict) -> dict:
    out = {}
    for s in scen:
        hs = hotspots.get(s["id"]) or []
        out[s["id"]] = {"count": len(hs), **{f"{lvl}_count": sum(h["severity"] == lvl for h in hs) for lvl in ("high", "moderate", "low")},
                        "located_area_m2": round(sum(h["area_m2"] for h in hs if not h.get("site_wide")), 1),
                        **{f"{lvl}_area_m2": round(sum(h["area_m2"] for h in hs if h["severity"] == lvl and not h.get("site_wide")), 1) for lvl in ("high", "moderate", "low")}}
    b = out[scen[0]["id"]]
    for s in scen[1:]:
        a = out[s["id"]]
        d = a["located_area_m2"] - b["located_area_m2"]
        a["vs_baseline"] = {"located_area_m2": round(d, 1), "count": a["count"] - b["count"],
                            "outcome": "unchanged" if abs(d) < EPS and a["count"] == b["count"] else "improvement" if d < 0 or (abs(d) < EPS and a["count"] < b["count"]) else "regression"}
    return out


def _tradeoff_points(scen: list[dict], full: dict) -> dict:
    """Cost (x) against heat-stress area-hours (y); the Pareto front over designs with a known cost and value."""
    pts = []
    for s in scen:
        c, h = full[s["id"]].get("cost_typical"), full[s["id"]].get(TRADEOFF_METRIC)
        pts.append({"id": s["id"], "name": s["name"], "cost": c["value"] if c and c["state"] == V.AVAILABLE else None, "currency": (c or {}).get("unit"),
                    "heat_m2h": h["value"] if h and h["state"] == V.AVAILABLE else None,
                    "missing": [x for x, ok in (("price", c and c["state"] == V.AVAILABLE), ("heat stress", h and h["state"] == V.AVAILABLE)) if not ok]})
    known = [p for p in pts if not p["missing"]]
    for p in known:
        p["pareto"] = not any(q is not p and q["cost"] <= p["cost"] and q["heat_m2h"] <= p["heat_m2h"]
                              and (q["cost"] < p["cost"] or q["heat_m2h"] < p["heat_m2h"]) for q in known)
    return {"x": "Indicative cost (typical)", "y": CRITERIA[TRADEOFF_METRIC]["label"], "points": pts,
            "front": [p["id"] for p in sorted((p for p in known if p["pareto"]), key=lambda p: p["cost"])],
            "not_plotted": [{"id": p["id"], "name": p["name"], "missing": p["missing"]} for p in pts if p["missing"]],
            "note": "Lower-left is better. Designs on the Pareto front are not beaten on both cost and heat stress by another design. "
                    "Designs without a price or heat-stress value are listed, never plotted as zero."}


def _give_up(alts: list[dict], costs: dict) -> list[dict]:
    """What each alternative gives up against the baseline: parking, built area, irrigation water, maintenance."""
    from . import basis

    per_space = basis.get("interventions.parking_space_m2")["value"]
    water_tree, water_m2 = basis.get("interventions.water_l_per_tree_day")["value"], basis.get("interventions.water_l_per_m2_day")["value"]
    out = []
    for a in alts:
        c = costs.get(a["id"]) or {}
        q = c.get("quantities")
        if not q:
            out.append({"alternative_id": a["id"], "note": "Quantities unavailable."})
            continue
        qb, qa = q["baseline"], q["alternative"]
        parking = max(0.0, qb["areas"]["parking"] - qa["areas"]["parking"])
        trees = max(0, qa["trees"] - qb["trees"])
        planted = max(0.0, qa["areas"]["green_space"] - qb["areas"]["green_space"])
        water = None if water_tree is None or water_m2 is None else round(trees * water_tree + planted * water_m2, 1)
        maint = c.get("maintenance_per_year_by_currency") or {}
        out.append({
            "alternative_id": a["id"],
            "parking_area_removed_m2": round(parking, 1), "parking_spaces_lost": int(parking // per_space),
            "building_footprint_lost_m2": round(max(0.0, qb["areas"]["building"] - qa["areas"]["building"]), 1),
            "floor_area_lost_m2": round(max(0.0, qb["floor_area_m2"] - qa["floor_area_m2"]), 1),
            "floor_area_note": "Buildings without a floor count are not included." if qa["buildings_without_floors"] or qb["buildings_without_floors"] else None,
            "trees_added": trees, "planting_added_m2": round(planted, 1),
            "irrigation_l_per_day": water, "irrigation_note": None if water is not None else "Irrigation water: value required (no sourced litres/day value yet).",
            "maintenance_per_year": maint or None,
            "maintenance_note": (f"Maintenance: value required for {', '.join(c.get('maintenance_value_required') or [])}." if c.get("maintenance_value_required")
                                 else None if maint else "Maintenance: value required (no rate gives a yearly maintenance cost)."),
            "basis": f"Parking spaces at {per_space:g} m² per space (basis interventions.parking_space_m2).",
        })
    return out


def _best(scen: list[dict], full: dict, ranking: dict, budget: dict | None) -> tuple[str | None, list[dict]]:
    """Highest score among eligible designs: no failed verified rule, and within the budget when one is given."""
    excluded = []
    eligible = []
    for s in scen:
        sid = s["id"]
        if ranking["aggregate"][sid]["score"] is None:
            excluded.append({"id": sid, "name": s["name"], "why": "no score (no weighted criterion available)"})
            continue
        fails = full[sid].get("compliance_failures") or {}
        if fails.get("state") == V.AVAILABLE and fails["value"] > 0:
            excluded.append({"id": sid, "name": s["name"], "why": f"fails {fails['value']:g} verified planning rule(s)"})
            continue
        if budget:
            c = full[sid].get("cost_typical") or {}
            if c.get("state") != V.AVAILABLE:
                excluded.append({"id": sid, "name": s["name"], "why": "no complete price to check against the budget"})
                continue
            if c.get("unit") not in (None, budget["currency"]) or c["value"] > budget["amount"] + EPS:
                excluded.append({"id": sid, "name": s["name"], "why": f"over the budget of {budget['amount']:g} {budget['currency']}"
                                 if c.get("unit") in (None, budget["currency"]) else f"costed in {c.get('unit')}, not {budget['currency']}"})
                continue
        eligible.append(sid)
    best = max(eligible, key=lambda sid: ranking["aggregate"][sid]["score"], default=None)
    return best, excluded


def _choice(scen: list[dict], full: dict, criteria, ranking, budget, not_comparable) -> dict:
    if not criteria or not ranking or not ranking["criteria"]:
        return {"mode": "no_winner", "statement": NO_WINNER}
    names = {s["id"]: s["name"] for s in scen}
    best, excluded = _best(scen, full, ranking, budget)
    weights = [{"criterion": c["metric"], "label": c["label"], "weight": c["weight"]} for c in ranking["criteria"]]
    variants = []
    for c in criteria:
        if float(c.get("weight", 0)) <= 0:
            continue
        for f in (1 - WEIGHT_STEP, 1 + WEIGHT_STEP):
            alt = [{**x, "weight": float(x["weight"]) * (f if x["metric"] == c["metric"] else 1)} for x in criteria]
            r2 = _rank(alt, scen[0], scen[1:], full[scen[0]["id"]], {s["id"]: full[s["id"]] for s in scen[1:]}, not_comparable)
            b2, _ = _best(scen, full, r2, budget)
            variants.append({"variation": f"{c['metric']} weight {'-' if f < 1 else '+'}{WEIGHT_STEP:.0%}", "best": b2, "best_name": names.get(b2)})
    changed = [v for v in variants if v["best"] != best]
    if best is None:  # nothing eligible: there is no choice whose robustness could be tested
        changed, variants = [], []
    priced = [full[s["id"]].get("cost_typical") for s in scen]
    cost_note = ("Costs come from single-value rates (no low/high range), so the choice was not re-tested with low and high costs."
                 if any(c and c["state"] == V.AVAILABLE for c in priced) else "No complete prices, so cost variation was not tested.")
    return {
        "mode": "best_under_priorities" if best else "no_eligible_design",
        "best": best, "best_name": names.get(best),
        "statement": (f"Best under your priorities: {names[best]}." if best else "No design is eligible under your priorities (see the exclusions)."),
        "weights": weights, "budget": budget, "excluded": excluded,
        "robustness": {"result": "not_assessed" if best is None else "robust" if not changed else "sensitive", "variants": variants,
                       "sensitive_to": [v["variation"] for v in changed],
                       "statement": ("Not assessed: no design is eligible, so there is no choice to test." if best is None else
                                     "Robust: the same design is best when any single weight moves by ±20 %." if not changed else
                                     "Sensitive to: " + "; ".join(f"{v['variation']} (best becomes {v['best_name'] or 'none'})" for v in changed) + "."),
                       "cost_note": cost_note},
        "rule": "A design that fails a verified planning rule cannot be the best; with a budget, a design must have a complete price within it.",
    }
