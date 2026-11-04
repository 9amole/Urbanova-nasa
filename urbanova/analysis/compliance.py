"""Planning-rule checks against verified rules only (FR-16, FR-29, FR-44, FR-45)."""

from __future__ import annotations

from .. import documents
from .. import values as V
from ..settings import load_config
from .common import BLOCKED, COMPLETED, PARTIAL, Context, result


def plot_area_status(project: dict, rules: dict, site_area: float | None, tolerance_pct: float, ambiguities: list[dict]) -> dict:
    """Is the area denominator for ratio checks settled? (acceptance test 'Conflicting plot areas')."""
    ambiguities = [a for a in ambiguities if a["rule_type"] == "plot_area_m2"]
    if ambiguities:
        return {"ok": False, "reason": "The planning document states conflicting plot areas (" + ", ".join(f"{v:,.0f} m²" for v in ambiguities[0]["values"]) + "). Verify the correct figure on the Planning rules screen before ratio checks are run."}
    stated = rules.get("plot_area_m2")
    if stated and site_area:
        diff = abs(stated["value"] - site_area) / stated["value"] * 100
        if diff > tolerance_pct and not project.get("verified_site_area_m2"):
            return {"ok": False, "reason": f"The verified plot area ({stated['value']:,.0f} m²) differs from the site boundary area ({site_area:,.0f} m²) by {diff:.1f}%. Choose which area applies (Project → Site area basis) before ratio checks are run."}
    return {"ok": True}


def run(ctx: Context) -> dict:
    rules = ctx.rules.get("verified") or {}
    cfg = load_config("rules.json")
    checks_cfg = cfg["planning_checks"]
    checkable = {k: r for k, r in rules.items() if k in checks_cfg}
    if not checkable:
        return result(BLOCKED, reason="No verified planning rules. Planning compliance is not assessed (it is never assumed).",
                      details={"checks": []})
    uf = ctx.shared.get("urban_form")
    if not uf:
        return result(BLOCKED, reason="Urban-form indicators are unavailable (no design geometry), so verified rules cannot be checked.", details={"checks": []})
    site_area = uf.get("site_area_m2", {}).get("value")
    area_status = plot_area_status(ctx.project, rules, site_area, cfg["plot_area_tolerance_pct"], ctx.rules.get("ambiguities") or [])
    checks, metrics = [], []
    for rtype, rule in checkable.items():
        spec = documents.RULE_TYPES[rtype]
        mname = checks_cfg[rtype]["metric"]
        mval = uf.get(mname)
        check = {"rule_id": rule["id"], "rule_type": rtype, "label": spec["label"], "limit": rule["value"], "unit": spec["unit"], "operator": spec["operator"],
                 "rule_source": {"document": rule["document_name"], "page": rule["page"], "text": rule["exact_text"]}, "metric": mname}
        ratio = rtype.endswith("_pct") or rtype == "max_far"
        if ratio and not area_status["ok"]:
            check.update(status="not_assessed", reason=area_status["reason"])
        elif not mval or mval["state"] != V.AVAILABLE:
            check.update(status="not_assessed", reason=(mval or {}).get("reason") or "The required measurement is not available.")
        else:
            val = mval["value"]
            passes = val <= rule["value"] if spec["operator"] == "<=" else val >= rule["value"]
            check.update(status="passes" if passes else "fails", measured=val,
                         label_result=("Passes the configured geometric check" if passes else "Does not meet the verified rule") + " (not a statutory approval)")
        checks.append(check)
    ctx.shared["compliance_checks"] = checks
    unverified = int(ctx.rules.get("unverified_count") or 0)
    warnings = []
    if unverified:
        warnings.append(f"{unverified} candidate rule(s) are still unverified and were not applied.")
    status = COMPLETED if all(c["status"] != "not_assessed" for c in checks) else PARTIAL
    return result(status, metrics=metrics, warnings=warnings, details={"checks": checks, "plot_area": area_status},
                  limitations=["Results are labelled as geometric checks against user-verified rules; URBANOVA does not grant statutory approval."])

