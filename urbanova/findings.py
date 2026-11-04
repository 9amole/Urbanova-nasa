"""Evidence-based findings and recommendations (FR-22, FR-23, FR-45, FR-46).

Findings come only from configured rules (urbanova/config/rules.json), verified planning
checks, or module data-quality outcomes. Severity is assigned by the stated rule,
never by AI intuition. Recommendations are templates linked to a finding.
"""

from __future__ import annotations

from typing import Any

from . import db
from . import values as V
from .settings import load_config

CLASSIFICATIONS = {
    "environmental_indicator": "Environmental indicator",
    "potential_environmental_concern": "Potential environmental concern",
    "planning_rule_check": "Planning-rule check",
    "data_quality_issue": "Data-quality issue",
    "modelled_outcome": "Modelled outcome",
}
SEVERITY_ORDER = {"high": 4, "moderate": 3, "low": 2, "info": 1}
# A threshold that has not been approved never produces the top severity (remediation B2):
# its band level is kept for transparency but the displayed severity is capped here.
PROVISIONAL_SEVERITY_CAP = "moderate"
# Modules whose metrics describe a regional grid cell, not the site (remediation B5).
REGIONAL_MODULES = {"climate_baseline": "Regional (~25 km ERA5 grid cell around the site, not the site itself)"}


def sort_key(f: dict) -> tuple:
    """Severity first; at equal severity, provisional-threshold findings rank below standard or verified ones."""
    provisional = (f.get("threshold") or {}).get("status") == "provisional_not_approved"
    return (-SEVERITY_ORDER.get(f["severity"], 0), provisional)


def _band(rule: dict, value: float) -> dict | None:
    below = rule.get("direction") == "below"
    for band in rule["severity"]:
        if below and value <= band["max"]:
            return band
        if not below and value >= band["min"]:
            return band
    return None


def _band_text(rule: dict) -> str:
    if rule.get("direction") == "below":
        return "; ".join(f"≤ {b['max']} → {b['level']}" for b in rule["severity"])
    return "; ".join(f"≥ {b['min']} → {b['level']}" for b in rule["severity"])


def _confidence(module_status: str, metric: dict) -> tuple[str, str]:
    q = metric.get("quality") or {}
    if module_status == "partial":
        return "limited", "The module completed with limitations (see warnings)."
    scenes = q.get("scenes")
    if scenes is not None and scenes < 3:
        return "limited", f"Based on only {scenes} clear-sky scene(s)."
    return "adequate", "Inputs passed the module's quality checks."


def generate(run: dict, project: dict, module_results: dict[str, dict], metric_rows: list[dict], shared: dict) -> tuple[list[dict], list[dict]]:
    cfg = load_config("rules.json")
    templates = load_config("recommendations.json")["templates"]
    by_name = {m["metric_name"]: m for m in metric_rows}
    module_of = {m["metric_name"]: m["module"] for m in metric_rows}
    findings: list[dict] = []
    recs: list[dict] = []

    def add(f: dict, rec_keys: list[str], target: Any = None, extra_problem: str | None = None) -> None:
        f.update(id=db.new_id("fnd"), run_id=run["id"], created_at=db.now())
        findings.append(f)
        for key in rec_keys:
            t = templates[key]
            recs.append({
                "id": db.new_id("rec"), "run_id": run["id"], "finding_id": f["id"], "title": t["title"],
                "problem": extra_problem or f["title"], "evidence": f["evidence"], "intervention": t["intervention"],
                "target": target or {"description": "Site-wide" if not f.get("geometry") else "Highlighted area", "geometry": f.get("geometry")},
                "expected_direction": t["expected_direction"], "required_inputs": t["required_inputs"], "tradeoffs": t["tradeoffs"],
                "evaluation_method": t["evaluation_method"], "review_status": "proposed", "created_at": db.now(),
            })

    for rule in cfg["findings"]:
        m = by_name.get(rule["metric"])
        if not m or m["state"] != V.AVAILABLE:
            continue
        band = _band(rule, m["value"])
        if not band:
            continue
        mod = module_of[rule["metric"]]
        conf, conf_why = _confidence(module_results[mod]["status"], m)
        provisional = rule["status"] == "provisional_not_approved"
        level = band["level"]
        capped = provisional and SEVERITY_ORDER[level] > SEVERITY_ORDER[PROVISIONAL_SEVERITY_CAP]
        if capped:
            level = PROVISIONAL_SEVERITY_CAP
        geom = None
        regional = REGIONAL_MODULES.get(mod)
        if regional:
            area = regional  # a regional climate statistic is not located on the site boundary
        elif rule["id"] == "HEAT-LST-02":
            geom = (shared.get("lst") or {}).get("hotspot_geometry")
            area = "Hottest-decile pixels within the site" if geom else "Site"
        else:
            geom = project.get("boundary")
            area = "Site boundary" if geom else "Site location (no boundary supplied)"
        evidence = [{"metric_id": m["id"], "metric": m["metric_name"], "label": m["label"], "value": m["value"], "unit": m["unit"], "method": m["method"], "output_kind": m["output_kind"]}]
        if rule["id"] == "HEAT-LST-01" and by_name.get("lst_site_median_c", {}).get("state") == V.AVAILABLE:
            for extra in ("lst_site_median_c", "lst_surroundings_median_c"):
                if by_name.get(extra, {}).get("state") == V.AVAILABLE:
                    e = by_name[extra]
                    evidence.append({"metric_id": e["id"], "metric": extra, "label": e["label"], "value": e["value"], "unit": e["unit"], "method": e["method"], "output_kind": e["output_kind"]})
        if capped:
            rating = f"band '{band['level']}' under PROVISIONAL rule {rule['id']} (not approved); shown as {level}"
        else:
            rating = f"rated {level} by {'PROVISIONAL ' if provisional else ''}rule {rule['id']}{' (not approved)' if provisional else ''}"
        limitations = [rule["limitation"], conf_why]
        if provisional:
            limitations.append("The severity threshold is PROVISIONAL and has not been approved; the AI/Analysis team must confirm or replace it before the finding is relied on.")
        if capped:
            limitations.append(f"Severity is capped at '{PROVISIONAL_SEVERITY_CAP}' while the threshold is unapproved; the band it falls in is '{band['level']}'.")
        if regional:
            limitations.append("This is a regional climate statistic for the reanalysis grid cell; it does not vary within the site and does not locate heat on the site.")
        status_text = "PROVISIONAL threshold, not approved" if provisional else "recognised standard index" if rule["status"] == "standard" else rule["status"]
        add({
            "category": rule["category"], "classification": rule["classification"], "title": rule["title"],
            "summary": f"{m['label']}: {m['value']:g} {m['unit']} — {rating}.",
            "geometry": geom, "metric_ids": [m["id"]], "severity": level, "affected_area": area,
            "severity_rule": f"Rule {rule['id']} bands: {_band_text(rule)} ({status_text})." + (f" Capped at {PROVISIONAL_SEVERITY_CAP} until approved." if capped else ""),
            "rule_id": rule["id"], "threshold": {"bands": rule["severity"], "status": rule["status"], "justification": rule["justification"],
                                                 "band_level": band["level"], "severity_capped": capped},
            "evidence": evidence, "sources": _sources(m, module_results[mod]), "confidence": conf,
            "limitations": limitations,
        }, rule["recommendation_keys"], target={"description": area, "geometry": geom})

    for check in shared.get("compliance_checks", []):
        if check["status"] == "fails":
            add({
                "category": "planning", "classification": "planning_rule_check", "title": f"Design does not meet verified rule: {check['label']}",
                "summary": f"Measured {check['measured']:g} {check['unit'] or ''} vs verified limit {check['operator']} {check['limit']:g}.",
                "geometry": project.get("boundary"), "metric_ids": [], "severity": "high", "affected_area": "Site design (verified planning rule)",
                "severity_rule": "Any failure of a user-verified planning rule is rated high (fixed rule).", "rule_id": check["rule_id"],
                "threshold": {"limit": check["limit"], "operator": check["operator"], "source": check["rule_source"]},
                "evidence": [{"metric": check["metric"], "value": check["measured"], "unit": check["unit"], "rule_text": check["rule_source"]["text"]}],
                "sources": [{"type": "planning_rule", **check["rule_source"]}], "confidence": "adequate",
                "limitations": ["This is a geometric check against a user-verified rule, not a statutory determination."],
            }, ["review_compliance"])
        elif check["status"] == "not_assessed":
            add({
                "category": "planning", "classification": "data_quality_issue", "title": f"Planning check not assessed: {check['label']}",
                "summary": check["reason"], "geometry": None, "metric_ids": [], "severity": "info", "affected_area": None,
                "severity_rule": "Data-quality issues are informational.", "rule_id": check["rule_id"], "threshold": None,
                "evidence": [{"reason": check["reason"]}], "sources": [], "confidence": "n/a", "limitations": [],
            }, ["resolve_data_gap"])

    for mod, res in module_results.items():
        if res["status"] in ("blocked", "unavailable", "failed"):
            add({
                "category": "data", "classification": "data_quality_issue", "title": f"{mod.replace('_', ' ').capitalize()}: {res['status']}",
                "summary": res.get("reason") or "", "geometry": None, "metric_ids": [], "severity": "info" if res["status"] != "failed" else "low", "affected_area": None,
                "severity_rule": "Module outcomes that prevent an assessment are reported as data-quality issues.", "rule_id": None, "threshold": None,
                "evidence": [{"module": mod, "status": res["status"], "reason": res.get("reason"), "provider_failures": res.get("provider_failures")}],
                "sources": [], "confidence": "n/a", "limitations": ["No conclusion is drawn for this analysis."],
            }, ["resolve_data_gap"] if res["status"] == "blocked" else [])
    return findings, recs


def _sources(metric: dict, res: dict) -> list[dict]:
    d = res.get("details") or {}
    out = [{"type": "dataset", "dataset_id": ds} for ds in metric.get("source_dataset_ids") or []]
    if d.get("attribution"):
        out.append({"type": "attribution", "text": d["attribution"]})
    if d.get("accepted_scenes"):
        out.append({"type": "scenes", "dates": [s["datetime"][:10] for s in d["accepted_scenes"]]})
    if d.get("period"):
        out.append({"type": "period", "value": d["period"]})
    return out
