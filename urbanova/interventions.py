"""Intervention options for design hotspots, measured by partial re-runs, and the optimiser (A10, PHASE2_SPEC §6).

For each of the highest-priority design hotspots:
1. candidates: every catalogue intervention (config/interventions.json) whose `applies_to` matches the hotspot's
   type and one of its causes, at each configured intensity; each is a geometry change to a copy of the design;
2. evaluation: a partial re-run of only the design analyses (urban form, shade, heat stress; planning checks for
   H4) on the copy, reusing the site data of the main run; the effect is the change in the hotspot's own benefit
   unit (H1: sun-exposed hard-surface area-hours at very strong heat stress; H3: shade-deficit area; H4: shortfall);
3. only options with a positive measured effect are kept; the Pareto front is taken on benefit and typical cost,
   or, without common prices, on benefit and quantity within each intervention (stated as such);
4. roles: best value (benefit per cost, or per unit of quantity), largest benefit, cheapest effective;
5. with a budget, a site-wide package of best-value options is re-evaluated together and returned as a draft design.
Cool paving (I4) is reported separately and never ranked against shade; I7 is a check, not a generated option.
"""

from __future__ import annotations

import copy
import tempfile
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import unary_union

from . import basis, cost, geo, recording
from . import values as V
from .analysis.common import BLOCKED, COMPLETED, PARTIAL, Context, result
from .analysis.heat_stress import sunlit_area_hours
from .analysis.hotspots import h4_candidates, setback_band, shade_deficit
from .analysis.urban_form import HARD_TYPES, POLY, design_surfaces, is_hard
from .settings import load_config

# Values of ctx.shared that describe the site, not the design; a candidate re-run starts from these.
SITE_SHARED = ("hot_day", "hottest_months", "climate_provider")
DESIGN_MODULES = ["urban_form", "solar_shade", "heat_stress"]
UNITS = {"H1": ("sun_exposed_hard_m2h", "m²·h", "Sun-exposed hard surface during very strong heat stress (area × hours)"),
         "H3": ("shade_deficit_m2", "m²", "Open space in sun at 15:00 and farther than D from shade"),
         "H4": ("shortfall_m2", "m²", "Shortfall against the verified rule")}


@lru_cache(maxsize=1)
def catalogue() -> dict[str, dict]:
    return load_config("interventions.json")["interventions"]


def param(name: str):
    return basis.get(f"interventions.{name}")["value"]


def intensities(iid: str) -> list:
    """The intensities tried for an intervention: from its basis record (`intensity_basis`, "record" or "record.key")."""
    ref = catalogue()[iid].get("intensity_basis")
    if not ref:
        return catalogue()[iid].get("intensities", [])
    name, _, key = ref.partition(".")
    v = param(name)
    return v[key] if key else v


def unit_of(types: list[str]) -> str:
    return "H4" if types == ["H4"] else "H1" if "H1" in types else "H3"


# ---------------------------------------------------------------- geometry helpers
def shrink_to(geom, target_area: float):
    """`geom` buffered inwards until its area is `target_area` (bisection); the whole geometry when target ≥ area."""
    if target_area >= geom.area:
        return geom
    lo, hi = 0.0, max(geom.bounds[2] - geom.bounds[0], geom.bounds[3] - geom.bounds[1])
    for _ in range(40):
        mid = (lo + hi) / 2
        if geom.buffer(-mid).area > target_area:
            lo = mid
        else:
            hi = mid
    return geom.buffer(-lo)


class Design:
    """A design FeatureCollection with its features in the local CRS, for generators."""

    def __init__(self, fc: dict, crs: str):
        self.fc, self.crs = copy.deepcopy(fc), crs

    def features(self, *types: str):
        for f in self.fc["features"]:
            if f["properties"].get("feature_type") in types:
                yield f, geo.to_local(shape(f["geometry"]), self.crs)

    def set_geom(self, f: dict, g) -> None:
        f["geometry"] = mapping(geo.to_wgs84(g, self.crs))

    def add(self, g, props: dict) -> None:
        self.fc["features"].append({"type": "Feature", "id": recording.new_id("fea"), "geometry": mapping(geo.to_wgs84(g, self.crs)),
                                    "properties": {**props, "review_status": "proposed"}})

    def drop_small(self) -> None:
        self.fc["features"] = [f for f in self.fc["features"] if f["geometry"]["type"] not in POLY or geo.to_local(shape(f["geometry"]), self.crs).area > 1.0]


def _clip_out(d: Design, types: tuple[str, ...], region, only=None) -> None:
    """Remove `region` from every polygon feature of `types` (optionally only those for which only(feature) is true)."""
    for f, g in list(d.features(*types)):
        if g.geom_type in POLY and (only is None or only(f)) and g.intersects(region):
            d.set_geom(f, g.difference(region))
    d.drop_small()


# ---------------------------------------------------------------- generators (deterministic, so recipes can be replayed)
def gen_trees(d: Design, hs, spacing: float, iid: str) -> float | None:
    t = param("tree")
    buildings = unary_union([g for _, g in d.features("building") if g.geom_type in POLY] or [Polygon()])
    minx, miny, maxx, maxy = hs.bounds
    n = 0
    y = miny + spacing / 2
    while y < maxy:
        x = minx + spacing / 2
        while x < maxx:
            p = Point(x, y)
            if hs.contains(p) and (buildings.is_empty or p.distance(buildings) >= t["building_clearance_m"]):
                n += 1
                d.add(p, {"feature_type": "tree", "height_m": t["height_m"], "canopy_radius_m": t["canopy_radius_m"], "name": f"{iid} tree {n}", "generated_by": iid})
            x += spacing
        y += spacing
    return float(n) or None


def gen_shade(d: Design, hs, coverage: float, iid: str) -> float | None:
    g = shrink_to(hs, coverage * hs.area)
    if g.is_empty:
        return None
    d.add(g, {"feature_type": "shade_structure", "height_m": param("shade")["height_m"], "name": f"{iid} shade structure", "generated_by": iid})
    return g.area


def _hard(f: dict, g) -> bool:
    return g.geom_type in POLY and is_hard(f["properties"]["feature_type"], f["properties"])


def _paved_in(d: Design, hs, types=HARD_TYPES):
    return unary_union([g.intersection(hs) for f, g in d.features(*types) if _hard(f, g)] or [Polygon()])


def gen_convert(d: Design, hs, share: float, iid: str) -> float | None:
    paved = _paved_in(d, hs)
    if paved.area <= 1:
        return None
    region = shrink_to(paved, share * paved.area)
    _clip_out(d, HARD_TYPES, region, lambda f: is_hard(f["properties"]["feature_type"], f["properties"]))
    d.add(region, {"feature_type": "green_space", "surface": "planted", "name": f"{iid} planting", "generated_by": iid})
    return region.area


def gen_reduce_parking(d: Design, hs, share: float, iid: str) -> float | None:
    parking = _paved_in(d, hs, ("parking",))
    if parking.area <= 1:
        return None
    region = shrink_to(parking, share * parking.area)
    _clip_out(d, ("parking",), region)
    return region.area


def gen_fix_rule(d: Design, hs_record: dict, site, iid: str) -> tuple[float, str] | None:
    """Setback: trim building parts out of the band. Green / open-space minimum: convert the largest paved areas."""
    r = hs_record["rule"]
    if r["rule_type"] == "min_setback_m":
        band = setback_band(site, r["limit"])
        removed = sum(g.intersection(band).area for _, g in d.features("building") if g.geom_type in POLY)
        _clip_out(d, ("building",), band)
        return (removed, "building_footprint_removed_m2") if removed > 0 else None
    if r["rule_type"] in ("min_green_space_pct", "min_open_space_pct"):
        need = hs_record["area_m2"] * 1.001  # tiny margin so rounding never leaves the check just short
        pieces = sorted(((f, g.intersection(site)) for f, g in d.features(*HARD_TYPES) if _hard(f, g)), key=lambda x: -x[1].area)
        added = 0.0
        for f, g in pieces:
            if added >= need:
                break
            take = shrink_to(g, need - added)
            d.set_geom(f, geo.to_local(shape(f["geometry"]), d.crs).difference(take))
            d.add(take, {"feature_type": "green_space", "surface": "planted", "name": f"{iid} green to meet the rule", "generated_by": iid})
            added += take.area
        d.drop_small()
        return (added, "green_space_added_m2") if added >= need * 0.999 else None
    return None  # coverage, FAR, height and floor limits need a redesign of the buildings: not generated


GENERATORS = {"trees": gen_trees, "shade": gen_shade, "convert": gen_convert, "reduce_parking": gen_reduce_parking,
              "cool_paving": lambda d, hs, v, iid: _paved_in(d, hs).area or None}


# ---------------------------------------------------------------- partial re-run and benefit
def partial_run(ctx: Context, fc: dict, modules: list[str]) -> tuple[Context, dict]:
    """Re-run only the given design modules on a copy of the design, reusing the main run's site data.
    Partial mode writes no files and builds no findings (core.run_design, solar.run)."""
    from .engine.core import run_design

    c = Context(project=ctx.project, scenario={**(ctx.scenario or {}), "features": fc}, config=ctx.config, run_id=recording.new_id("run"),
                out_dir=Path(tempfile.gettempdir()), rules=ctx.rules, site_cache=ctx.site_cache, rates=ctx.rates, partial=True)
    c.shared.update({k: ctx.shared[k] for k in SITE_SHARED if k in ctx.shared})
    return c, run_design(c, modules)


def _check(c: Context, record: dict) -> dict | None:
    return next((x for x in c.shared.get("compliance_checks") or [] if x["rule_id"] == record["rule"]["rule_id"]), None)


def benefit(c: Context, unit: str, hs, record: dict) -> float | None:
    """The hotspot's benefit-unit value for the design analysed in context `c` (the same code before and after)."""
    surf = design_surfaces(c)
    if unit == "H4":
        check = _check(c, record)
        if not check or check.get("status") == "not_assessed":
            return None
        if check["status"] == "passes":
            return 0.0
        cands = h4_candidates([check], surf, c.project.get("verified_site_area_m2") or surf["site"].area)
        return cands[0]["area"] if cands else 0.0
    sol, hsd = c.shared.get("solar"), c.shared.get("heat_stress")
    if not sol or not hsd:
        return None
    if unit == "H1":
        return sunlit_area_hours(surf["hard"].intersection(hs), sol["shade_by_hour"], hsd["very_strong_hours"])
    return shade_deficit(surf, sol, within=hs).area


def very_strong_hours(c: Context, hs) -> float | None:
    """Area-weighted hours in sun at very strong heat stress over the hotspot: Σ_h sunlit area(h) ÷ area, which equals
    the area-weighted mean of the per-piece counts of hotspots.exposure_stats without splitting the polygon."""
    sol, hsd = c.shared.get("solar"), c.shared.get("heat_stress")
    if not sol or not hsd or hs.area <= 0:
        return None
    return round(sunlit_area_hours(hs, sol["shade_by_hour"], hsd["very_strong_hours"]) / hs.area, 2)


# ---------------------------------------------------------------- candidates and evaluation
def applies(iid: str, record: dict) -> bool:
    a = catalogue()[iid]["applies_to"]
    causes = {c["code"] for c in record["likely_causes"]}
    return bool(set(a["types"]) & set(record["types"])) and bool(set(a["causes"]) & causes)


def apply_recipe(design: Design, recipe: dict, hs, record: dict, site) -> tuple[float, str | None] | None:
    """Apply one option (intervention + intensity) to `design` in place; returns (quantity, quantity_type)."""
    iid, v = recipe["intervention_id"], recipe["intensity"]
    item = catalogue()[iid]
    if item["generator"] == "fix_rule":
        return gen_fix_rule(design, record, site, iid)
    gen = GENERATORS.get(item["generator"])
    q = gen(design, hs, v, iid) if gen else None
    return (q, item["quantity_type"]) if q else None


def _label(item: dict, v) -> str:
    return item["intensity_label"].format(v=v, p=100 * v if isinstance(v, (int, float)) else 0)


def _water(item: dict, qty: float) -> dict | None:
    drv = item.get("water_driver")
    if not drv:
        return None
    key, per = ("water_l_per_tree_day", "tree") if drv == "trees" else ("water_l_per_m2_day", "m² of planting")
    rate = param(key)
    return {"driver": drv, "quantity": round(qty, 1), "unit": per, "litres_per_day": None if rate is None else round(qty * rate, 1),
            "note": None if rate is not None else f"Irrigation water per {per}: value required (no sourced value yet)."}


def _effect_confidence(ctx: Context, item: dict) -> dict:
    if item.get("separate"):
        return {"level": "low", "reasons": ["Effect on the heat a pedestrian feels is not modelled (area treated only)"]}
    reasons = ["Measured in the hotspot's own benefit unit by re-running the design analyses on the changed design"]
    level = "high"
    feats = (ctx.scenario or {}).get("features", {}).get("features", [])
    flagged = sum(1 for f in feats if f["properties"].get("review_status") == "needs_review")
    if flagged:
        level, reasons = "medium", reasons + [f"{flagged} design feature(s) are still flagged for review"]
    if (ctx.shared.get("solar") or {}).get("buildings_without_height"):
        level, reasons = "medium", reasons + ["Some buildings have no height, so their shade is missing"]
    return {"level": level, "reasons": reasons}


def evaluate_hotspot(ctx: Context, entry: dict, site) -> dict:
    record, hs = entry["record"], entry["geom"]
    unit = unit_of(record["types"])
    uname, uunit, ulabel = UNITS[unit]
    before = benefit(ctx, unit, hs, record) if hs is not None or unit == "H4" else None
    out = {"hotspot_number": record["number"], "unit": {"name": uname, "unit": uunit, "label": ulabel}, "before": None if before is None else round(before, 2),
           "options": [], "checks": [], "notes": []}
    if before is None:
        out["notes"].append("The hotspot's benefit unit could not be measured for the current design, so no option was evaluated.")
        return out
    vs_before = very_strong_hours(ctx, hs) if hs is not None and unit != "H4" else None
    for iid, item in catalogue().items():
        if not applies(iid, record):
            continue
        if item["generator"] == "check_only":
            cause = next(c for c in record["likely_causes"] if c["code"] in item["applies_to"]["causes"])
            out["checks"].append({"intervention_id": iid, "title": item["title"], "cause": cause["code"], "evidence": cause["evidence"], "evidence_ids": cause.get("evidence_ids", []),
                                  "text": cause["text"], "method": item["evaluation_method"]})
            continue
        conf = _effect_confidence(ctx, item)
        for v in intensities(iid):
            recipe = {"intervention_id": iid, "intensity": v, "hotspot_number": record["number"]}
            d = Design(ctx.scenario["features"], ctx.local_crs)
            applied = apply_recipe(d, recipe, hs, record, site)
            if not applied:
                continue
            qty, qtype = applied
            opt = {"id": recording.new_id("opt"), "intervention_id": iid, "title": item["title"], "intensity": v, "intensity_label": _label(item, v),
                   "recipe": recipe, "separate": bool(item.get("separate")),
                   "quantity": {"type": qtype, "value": round(qty, 2), "unit": cost.QUANTITY_TYPES.get(qtype, {}).get("unit"),
                                "label": cost.QUANTITY_TYPES.get(qtype, {}).get("label")},
                   "cost": cost.option_cost(qtype, qty, ctx.rates), "water": _water(item, qty), "roles": [], "pareto": False}
            if iid == "I5":
                opt["lost_parking_spaces"] = int(qty // param("parking_space_m2"))
            if item.get("separate"):
                opt["effect"] = {"unit": "m²", "label": "Paving treated (effect on pedestrian heat not modelled)", "treated_m2": round(qty, 1), "output_kind": V.MODELLED}
                opt["effect_confidence"] = conf
                out["options"].append(opt)
                continue
            c, _ = partial_run(ctx, d.fc, ["urban_form", "planning_compliance"] if unit == "H4" else DESIGN_MODULES)
            after = benefit(c, unit, hs, record)
            if after is None or before - after <= 1e-6:
                continue  # only a positive measured effect makes an option (§6.2)
            opt["evaluation_run_id"] = c.run_id
            opt["effect"] = {"unit": uunit, "label": ulabel, "before": round(before, 2), "after": round(after, 2), "change": round(before - after, 2),
                             "change_pct": round(100 * (before - after) / before, 1) if before else None, "output_kind": V.MODELLED,
                             "very_strong_hours_before": vs_before, "very_strong_hours_after": very_strong_hours(c, hs) if unit != "H4" else None}
            if unit == "H4":
                opt["effect"]["rule_passes_after"] = (_check(c, record) or {}).get("status") == "passes"
            opt["effect_confidence"] = conf
            opt["benefit_per_quantity"] = round((before - after) / qty, 4)
            out["options"].append(opt)
    rank_options(out["options"])
    return out


# ---------------------------------------------------------------- Pareto front and roles
def _front(group: list[dict], cost_key) -> None:
    """Mark the non-dominated options of `group` on (benefit ↑, cost_key ↓)."""
    for a in group:
        a["pareto"] = not any(b is not a and b["effect"]["change"] >= a["effect"]["change"] and cost_key(b) <= cost_key(a)
                              and (b["effect"]["change"] > a["effect"]["change"] or cost_key(b) < cost_key(a)) for b in group)


def rank_options(options: list[dict]) -> None:
    """Pareto front and roles on the ranked (non-separate) options.

    Options priced in the most common currency (at least two) are ranked on measured benefit and typical cost: best value
    (benefit per cost) and cheapest effective. Options without such a price are compared only within their own intervention,
    on benefit and quantity (same unit). Largest benefit is hotspot-wide (one benefit unit per hotspot)."""
    ranked = [o for o in options if not o["separate"]]
    cur = [o["cost"]["currency"] for o in ranked if o["cost"].get("status") == "priced"]
    main = max(set(cur), key=cur.count) if cur else None
    priced = [o for o in ranked if o["cost"].get("status") == "priced" and o["cost"]["currency"] == main]
    if len(priced) < 2:
        priced = []
    rest = [o for o in ranked if o not in priced]
    if priced:
        _front(priced, lambda o: o["cost"]["typical"])
        for o in priced:
            o["benefit_per_cost"] = round(o["effect"]["change"] / o["cost"]["typical"], 6) if o["cost"]["typical"] else None
            o["ranking_mode"] = "benefit_and_cost"
        front = [o for o in priced if o["pareto"]]
        max(front, key=lambda o: o["benefit_per_cost"] or 0)["roles"].append("best_value")
        min(front, key=lambda o: o["cost"]["typical"])["roles"].append("cheapest_effective")
    for iid in sorted({o["intervention_id"] for o in rest}):
        group = [o for o in rest if o["intervention_id"] == iid]
        _front(group, lambda o: o["quantity"]["value"])
        for o in group:
            o["ranking_mode"] = "benefit_per_quantity"
        if not priced:
            max(group, key=lambda o: o["benefit_per_quantity"])["roles"].append("best_value")
    if ranked:
        max(ranked, key=lambda o: o["effect"]["change"])["roles"].append("largest_benefit")


# ---------------------------------------------------------------- package within a budget
def package(ctx: Context, per_hotspot: list[dict], entries: dict[int, dict], site) -> dict | None:
    amount, currency = ctx.config.get("budget_amount"), (ctx.config.get("budget_currency") or "").upper()
    if not amount or not currency:
        return None
    picks = []
    for h in per_hotspot:
        best = next((o for o in h["options"] if "best_value" in o["roles"] and o["cost"].get("status") == "priced" and o["cost"]["currency"] == currency), None)
        if best:
            picks.append(best)
    picks.sort(key=lambda o: -(o.get("benefit_per_cost") or 0))
    chosen, total = [], 0.0
    for o in picks:
        if total + o["cost"]["typical"] <= float(amount):
            chosen.append(o)
            total += o["cost"]["typical"]
    out = {"budget": {"amount": float(amount), "currency": currency}, "option_ids": [o["id"] for o in chosen], "status": "draft",
           "note": "A draft design for review. It is analysed as a scenario only after you approve it."}
    if not chosen:
        out.update(status="none_within_budget", note=f"No priced best-value option fits within {amount:g} {currency}.")
        return out
    d = Design(ctx.scenario["features"], ctx.local_crs)
    for o in chosen:
        e = entries[o["recipe"]["hotspot_number"]]
        apply_recipe(d, o["recipe"], e["geom"], e["record"], site)
    c, _ = partial_run(ctx, d.fc, DESIGN_MODULES + ["planning_compliance"])
    effects = []
    for o in chosen:
        e = entries[o["recipe"]["hotspot_number"]]
        u = unit_of(e["record"]["types"])
        effects.append({"hotspot_number": e["record"]["number"], "option_id": o["id"], "before": o["effect"]["before"],
                        "after_together": None if (a := benefit(c, u, e["geom"], e["record"])) is None else round(a, 2), "unit": o["effect"]["unit"]})
    out.update(cost={k: round(sum(o["cost"][k] for o in chosen), 2) for k in ("low", "typical", "high")} | {"currency": currency},
               effects=effects, evaluation_run_id=c.run_id, features=d.fc)
    return out


# ---------------------------------------------------------------- module A10
def run(ctx: Context) -> dict:
    entries = ctx.shared.get("hotspots")
    if entries is None or not ctx.scenario:
        return result(BLOCKED, reason="Intervention options need the design hotspots of this run.")
    if not ctx.project.get("boundary"):
        return result(BLOCKED, reason="Intervention options need a site boundary.")
    site = geo.to_local(shape(ctx.project["boundary"]), ctx.local_crs)
    limit = int(param("max_hotspots"))
    chosen = entries[:limit]
    per_hotspot = [evaluate_hotspot(ctx, e, site) for e in chosen]
    pkg = package(ctx, per_hotspot, {e["record"]["number"]: e for e in chosen}, site)
    notes = [f"Hotspot {e['record']['number']} not optimised: only the {limit} highest-priority hotspots are (basis interventions.max_hotspots)." for e in entries[limit:]]
    n_opts = sum(1 for h in per_hotspot for o in h["options"] if not o["separate"])
    m = [V.metric("intervention_options_count", "Measured intervention options with a positive effect", n_opts, "count",
                  method="Catalogue interventions applied to copies of the design and measured by partial re-runs (PHASE2_SPEC §6)")]
    res = result(PARTIAL if notes else COMPLETED, metrics=m, warnings=[], details={"per_hotspot": per_hotspot, "not_optimised": notes, "package": pkg,
                 "ranking": "Options are ranked on measured benefit and typical cost when every option has a price in one currency; otherwise on benefit per unit of quantity within each intervention."},
                 limitations=["Effects are measured with the engine's geometric shade and UTCI model on the composite hot day; surface temperatures and long-wave radiation are not modelled.",
                              "Intervention sizes and spacings are project choices awaiting approval (basis interventions.*).",
                              "Costs come only from supplied rates or the approved cost library; otherwise 'price required'."])
    res["options_by_hotspot"] = {h["hotspot_number"]: h for h in per_hotspot}
    return res
