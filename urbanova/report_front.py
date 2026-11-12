"""Front sections of the report, organised around the supervisors' six questions (PHASE2_SPEC §8.1).

Content model: a list of (title, blocks); a block is ("p", text), ("h3", text), ("h4", text), ("ul", [text]),
("img", path, alt) or ("table", header, rows). report.py renders it to HTML and PDF. Every factual sentence ends with
its source ids in brackets, built only from stored records (metrics, hotspots, options, comparison, insights).
"""

from __future__ import annotations

import math
from pathlib import Path

from shapely.geometry import shape

from . import basis, db

MAIN_SECTIONS = ["1. Executive summary", "2. Design hotspots", "3. Site context (existing land)", "4. Design comparison and trade-offs",
                 "5. Recommended design and next steps"]
PARTS = ["1. Where is the problem?", "2. Why is it a problem?", "3. What causes it?", "4. What solutions are available?",
         "5. What does each cost, and what effect does it have?", "6. Which option suits the project, and why?"]
SYNTHETIC_LABEL = "Contains synthetic or test data (e.g. TEST prices in XTS) — for demonstration, not for project decisions."


def _ids(*ids) -> str:
    flat = [i for i in ids if i]
    return f" [{', '.join(flat)}]" if flat else " [run summary]"


def _n(v, d=0) -> str:
    return f"{v:,.{d}f}" if isinstance(v, (int, float)) else str(v)


def _range(lo, hi, cur) -> str:
    """'a–b CUR', or one number when a single-value rate makes low and high equal."""
    return f"{_n(lo)} {cur}" if abs(lo - hi) < 0.5 else f"{_n(lo)}–{_n(hi)} {cur}"


def is_synthetic(project: dict) -> bool:
    if project.get("synthetic_data"):
        return True
    return any(r["currency"] == "XTS" or "TEST" in (r.get("source") or "").upper() for r in db.select("rate_items", "project_id = ?", [project["id"]]))


def _options_by_hotspot(bundle: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in bundle["recommendations"]:
        if r.get("hotspot_id") and r.get("options"):
            out.setdefault(r["hotspot_id"], []).extend({**o, "_tradeoffs": r["tradeoffs"]} for o in r["options"])
    return out


def suited(opts: list[dict]) -> tuple[dict | None, str]:
    """The option that suits best and why: best value (per cost, or per quantity without prices), else largest benefit."""
    ranked = [o for o in opts if not o.get("separate")]
    best = next((o for o in ranked if "best_value" in o["roles"] and o.get("ranking_mode") == "benefit_and_cost"), None)
    if best:
        return best, "it gives the largest measured benefit per unit of typical cost among the priced options"
    big = next((o for o in ranked if "largest_benefit" in o["roles"]), None)
    if big:
        return big, ("it has the largest measured benefit; without comparable prices options are compared only within each "
                     "intervention, so obtain quotations before choosing on cost")
    return None, ""


def _hotspot_png(project: dict, scenario: dict | None, h: dict, out: Path) -> str | None:
    """Map extract: the design around the hotspot, the hotspot outlined (part 1 'Where')."""
    if not h.get("geometry"):
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = shape(h["geometry"])
    minx, miny, maxx, maxy = g.bounds
    pad = max(maxx - minx, maxy - miny) * 0.6 + 2e-4
    fig, ax = plt.subplots(figsize=(3.4, 3.0), dpi=120)
    colors = {"building": "#6b6b6b", "road": "#d9a400", "green_space": "#2e8b57", "parking": "#9e9e9e", "open_space": "#c8b88a", "shade_structure": "#8d6e63"}
    for f in (scenario or {}).get("features", {}).get("features", []):
        fg = shape(f["geometry"])
        if fg.geom_type in ("Polygon", "MultiPolygon"):
            for p in getattr(fg, "geoms", [fg]):
                ax.fill(*p.exterior.xy, color=colors.get(f["properties"].get("feature_type"), "#cccccc"), alpha=0.5, lw=0.3)
        elif fg.geom_type == "Point":
            ax.plot(fg.x, fg.y, "o", color="#1b5e20", ms=3)
    if project.get("boundary"):
        b = shape(project["boundary"])
        for p in getattr(b, "geoms", [b]):
            ax.plot(*p.exterior.xy, color="#c62828", lw=1)
    for p in getattr(g, "geoms", [g]):
        ax.fill(*p.exterior.xy, facecolor="#b2182b", alpha=0.35, edgecolor="#5a0000", lw=1.6)
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect(1 / max(0.2, math.cos(math.radians(project["latitude"]))))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"Hotspot {h['number']}", fontsize=9)
    fig.tight_layout()
    path = out / f"report_hotspot_{h['number']}.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def build(project: dict, run: dict, bundle: dict, scenario: dict | None, comparison: dict | None, out: Path, insights: dict | None) -> list[tuple[str, list]]:
    hs = bundle.get("hotspots") or []
    opts = _options_by_hotspot(bundle)
    metrics = {m["metric_name"]: m for m in bundle["metrics"] if m["state"] == "available"}
    sections = []

    # ---------------------------------------------------------------- 1. executive summary
    ex = []
    sev = {lvl: sum(1 for h in hs if h["severity"] == lvl) for lvl in ("high", "moderate", "low")}
    located = sum(h["area_m2"] for h in hs if not h.get("site_wide"))
    if hs:
        ex.append(f"Design: {len(hs)} hotspots were found on the drawing ({sev['high']} high, {sev['moderate']} moderate, {sev['low']} low severity), "
                  f"covering {_n(located)} m² of located area." + _ids(*[h["id"] for h in hs]))
    elif not run.get("scenario_id"):
        ex.append("Site analysis: this run describes the land as it is today; there is no design yet, so there are no design hotspots, options or costs.")
    else:
        reason = ((run.get("module_status") or {}).get("hotspots") or {}).get("details", {}).get("notes") or ["hotspot detection was not part of this run"]
        ex.append(f"Design: no design hotspot is reported ({'; '.join(reason)}).")
    for c in bundle.get("site_context") or []:
        mid = (metrics.get(c.get("metric")) or {}).get("id")
        ex.append(f"Existing land (context, not a design problem): {c['statement']}" + _ids(mid))
    for h in hs[:3]:
        cause = h["likely_causes"][0]["text"] if h["likely_causes"] else "no listed cause rule matched"
        ex.append(f"Priority {h['number']}: hotspot {h['number']} ({' + '.join(h['type_labels']).lower()}), {_n(h['area_m2'])} m², severity {h['severity']}; "
                  f"main cause: {cause}." + _ids(h["id"]))
    chosen = []
    for h in hs:
        o, _ = suited(opts.get(h["id"], []))
        if o:
            chosen.append((h, o))
    priced = [o for _, o in chosen if o["cost"].get("status") == "priced"]
    curs = {o["cost"]["currency"] for o in priced}
    if chosen and len(priced) == len(chosen) and len(curs) == 1:
        lo, hi = sum(o["cost"]["low"] for o in priced), sum(o["cost"]["high"] for o in priced)
        ex.append(f"Indicative cost of the suited option for each optimised hotspot: {_range(lo, hi, next(iter(curs)))}." + _ids(*[o["id"] for _, o in chosen]))
    elif chosen:
        ex.append(f"Indicative cost: price required for {len(chosen) - len(priced)} of the {len(chosen)} suited options (no supplied rate)."
                  + _ids(*[o["id"] for _, o in chosen]))
    if chosen:
        eff = {lvl: sum(1 for _, o in chosen if o["effect_confidence"]["level"] == lvl) for lvl in ("high", "medium", "low")}
        cst = {lvl: sum(1 for _, o in chosen if (o["cost"].get("confidence") or {}).get("level") == lvl) for lvl in ("high", "medium", "low")}
        ex.append(f"Confidence in the effects: {eff['high']} high, {eff['medium']} medium, {eff['low']} low; in the costs: {cst['high']} high, "
                  f"{cst['medium']} medium, {cst['low']} low (kept separate, never combined)." + _ids(*[o["id"] for _, o in chosen]))
    if comparison and (comparison.get("result") or {}).get("comparable"):
        ch = comparison["result"].get("choice") or {}
        ex.append(f"Design comparison: {ch.get('statement', 'see section 4')}" + _ids(comparison.get("id")))
    blocks = [("ul", ex)]
    if insights:
        label = insights["label"] + (f" ({insights['model']})" if insights.get("model") else "")
        blocks.append(("h3", "Insights"))
        blocks.append(("p", label + ". Evidence ids in brackets refer to records in this report."))
        for key, head in (("site_status", "Site status"), ("top_issues", "Main problems"), ("attention_first", "What needs attention first")):
            lines = [c["text"] + _ids(*c.get("evidence_ids") or []) for c in insights["blocks"].get(key) or []]
            if lines:
                blocks += [("h4", head), ("ul", lines)]
    sections.append((MAIN_SECTIONS[0], blocks))

    # ---------------------------------------------------------------- 2. one section per hotspot
    blocks = []
    if not hs:
        blocks.append(("p", "No design hotspot is reported for this run."))
    not_optimised = ((run.get("module_status") or {}).get("interventions") or {}).get("details", {}).get("not_optimised") or []
    for h in hs:
        hid = h["id"]
        sm = {s["name"]: s for s in h["supporting_metrics"]}
        blocks.append(("h3", f"Hotspot {h['number']} — {' + '.join(h['type_labels'])} (severity {h['severity']})"))
        # 1 where
        blocks.append(("h4", PARTS[0]))
        png = _hotspot_png(project, (scenario or None), h, out)
        if png:
            blocks.append(("img", png, f"Map extract of hotspot {h['number']}"))
        where = "site-wide (a shortfall against a verified ratio, not a single place)" if h.get("site_wide") else f"{_n(h['site_share_pct'], 1)} % of the site"
        comp = sm.get("composition_pct", {}).get("value") or {}
        made = ("; made of " + ", ".join(f"{k} {v:g} %" for k, v in comp.items())) if comp else ""
        blocks.append(("p", f"{_n(h['area_m2'])} m², {where}{made}." + _ids(hid)))
        # 2 why
        blocks.append(("h4", PARTS[1]))
        why = []
        if "utci_sun_peak_c" in sm and "utci_shade_peak_c" in sm:
            why.append(f"UTCI in sun reaches {sm['utci_sun_peak_c']['value']:g} °C against {sm['utci_shade_peak_c']['value']:g} °C in shade (UTCI assessment scale, R-26)."
                       + _ids(hid, *sm["utci_sun_peak_c"].get("source_metric_ids") or []))
        if "very_strong_sun_hours" in sm:
            why.append(f"In sun at very strong or extreme heat stress for {sm['very_strong_sun_hours']['value']:g} h on the hot day (area-weighted)."
                       + _ids(hid, *sm["very_strong_sun_hours"].get("source_metric_ids") or []))
        if h.get("rule"):
            r = h["rule"]
            why.append(f"{r['note']}: measured {r['measured']:g} against the verified limit {r['limit']:g} {r.get('unit') or ''} ({r['relative_shortfall_pct']:g} % short).".replace(" .", ".") + _ids(hid))
        why.append(f"Severity {h['severity']}: {h['severity_basis']}." + _ids(hid))
        blocks.append(("ul", why))
        # 3 causes
        blocks.append(("h4", PARTS[2]))
        blocks.append(("ul", [f"{c['code']}: {c['text']}." + _ids(hid, *c.get("evidence_ids") or []) for c in h["likely_causes"]]
                       or ["None of the listed cause rules matched." + _ids(hid)]))
        # 4 solutions, 5 cost and effect, 6 which suits
        ho = opts.get(hid, [])
        blocks.append(("h4", PARTS[3]))
        if not ho:
            why_none = next((n for n in not_optimised if f"Hotspot {h['number']} " in n), None) or \
                "No catalogue intervention improved this hotspot when measured, or options were not evaluated in this run."
            blocks.append(("p", why_none + _ids(hid)))
            blocks += [("h4", PARTS[4]), ("p", "No option to cost." + _ids(hid)), ("h4", PARTS[5]), ("p", "No option can be recommended yet." + _ids(hid))]
            continue
        front = [o for o in ho if o.get("pareto") or o.get("separate")]
        blocks.append(("ul", [f"{o['title']} ({o['intensity_label']})" + (" — listed separately, effect on pedestrian heat not modelled" if o.get("separate") else "")
                              + _ids(o["id"]) for o in front]))
        blocks.append(("h4", PARTS[4]))
        rows = []
        for o in front:
            e = o["effect"]
            effect = (f"{_n(e['treated_m2'])} m² treated" if o.get("separate") else f"{_n(e['before'])} → {_n(e['after'])} {e['unit']} (−{e['change_pct']:g} %)")
            c = o["cost"]
            cost = (_range(c["low"], c["high"], c["currency"]) + (f"; +{_n(c['maintenance_per_year'])}/yr" if c.get("maintenance_per_year") is not None else "")
                    if c.get("status") == "priced" else "price required")
            water = (o.get("water") or {}).get("note") or ("" if not o.get("water") else f"{o['water']['litres_per_day']} L/day")
            trade = "; ".join(t for t in o["_tradeoffs"] if "water" not in t.lower())[:160]
            rows.append([f"{o['title']} ({o['intensity_label']})", effect, cost,
                         f"effect {o['effect_confidence']['level']} / cost {(c.get('confidence') or {}).get('level', '—')}", "; ".join(x for x in (water, trade) if x), o["id"]])
        blocks.append(("table", ["Option", "Measured effect", "Cost", "Confidence", "Water and trade-offs", "Source id"], rows))
        blocks.append(("h4", PARTS[5]))
        o, reason = suited(ho)
        if o:
            e = o["effect"]
            blocks.append(("p", f"{o['title']} ({o['intensity_label']}): {_n(e['before'])} → {_n(e['after'])} {e['unit']} (−{e['change_pct']:g} %), because {reason}."
                           + _ids(o["id"], hid)))
        else:
            blocks.append(("p", "Only options reported separately (not ranked) exist for this hotspot; none is recommended on heat stress." + _ids(hid)))
    sections.append((MAIN_SECTIONS[1], blocks))

    # ---------------------------------------------------------------- 3. site context
    blocks = [("p", "Existing land and regional climate describe today's site and are the same for every design. They are context, never a design problem or a cause.")]
    lines = [c["statement"] + _ids((metrics.get(c.get("metric")) or {}).get("id")) for c in bundle.get("site_context") or []]
    for name in ("climate_hot_season_mean_tmax_c", "climate_mean_annual_TX90p_pct", "climate_hot_day_tmax_c", "lst_site_median_c", "lst_site_percentile_in_surroundings",
                 "ndvi_site_vegetated_share_pct", "utci_sun_peak_c", "utci_shade_peak_c"):
        m = metrics.get(name)
        if m:
            lines.append(f"{m['label']}: {m['value']:g} {m['unit'] or ''}".rstrip() + "." + _ids(m["id"]))
    ds = metrics.get("lst_ds_site_median_c")
    if ds:
        q = ds.get("quality") or {}
        scale = "" if q.get("scale_check_rmse_c") is None else f"; scale-check error {q['scale_check_rmse_c']:g} °C against a published urban range of {q['published_rmse_range_c'][0]:g}–{q['published_rmse_range_c'][1]:g} °C"
        lines.append(f"{ds['label']}: {ds['value']:g} °C ({q.get('label', 'ML-downscaled, modelled')}{scale})." + _ids(ds["id"]))
    blocks.append(("ul", lines or ["No existing-land analysis produced values in this run (see Appendix L)."]))
    sections.append((MAIN_SECTIONS[2], blocks))

    # ---------------------------------------------------------------- 4. comparison
    blocks = []
    if comparison and (comparison.get("result") or {}).get("comparable"):
        from .report import _comparison_lines

        names = comparison.get("scenario_names") or {}
        for head, lines in _comparison_lines(comparison["result"], names):
            blocks += [("h3", head), ("ul", [x + _ids(comparison.get("id")) for x in lines])]
            if head.startswith("Trade-off") and comparison.get("_tradeoff_png"):
                blocks.append(("img", comparison["_tradeoff_png"], "Trade-off chart: cost against heat stress"))
        blocks.append(("p", "The full metric-by-metric comparison is in Appendix J."))
    elif comparison:
        blocks.append(("p", "The included comparison is not valid: " + " ".join(comparison["result"].get("issues", []))))
    else:
        blocks.append(("p", "No design comparison is included. Analyse the baseline and alternatives with the same settings and compare them on the Compare tab, "
                            "then choose the comparison when generating the report."))
    sections.append((MAIN_SECTIONS[3], blocks))

    # ---------------------------------------------------------------- 5. recommended design and next steps
    rec = []
    ch = ((comparison or {}).get("result") or {}).get("choice") if comparison else None
    if ch and ch.get("best"):
        rec.append(f"Recommended design under your priorities: {ch['best_name']} ({ch['robustness']['statement']})" + _ids(comparison.get("id")))
    elif ch and ch.get("mode") == "no_eligible_design":
        rec.append("No compared design is eligible: " + "; ".join(f"{x['name']} {x['why']}" for x in ch["excluded"]) + "." + _ids(comparison.get("id")))
    for h, o in chosen[:5]:
        rec.append(f"For hotspot {h['number']}: {o['title']} ({o['intensity_label']}); apply it as a draft alternative, approve it and analyse it to confirm the effect in the whole design."
                   + _ids(o["id"], h["id"]))
    steps = []
    if any(o["cost"].get("status") != "priced" for _, o in chosen):
        steps.append("Obtain quotations for the options marked 'price required'.")
    prov = sorted({b for h in hs for b in h.get("provisional_basis") or []})
    if prov:
        steps.append("Have the team approve the project-choice values that cap severities: " + ", ".join(prov) + ".")
    if basis.get("interventions.water_l_per_tree_day")["value"] is None:
        steps.append("Record sourced irrigation-water values (litres/day per tree and per m² of planting).")
    checks = (((run.get("module_status") or {}).get("planning_compliance") or {}).get("details") or {}).get("checks") or []
    if not checks or any(c["status"] == "not_assessed" for c in checks):
        steps.append("Verify the planning rules so that every compliance check can be assessed.")
    if hs:
        steps.append(f"Commission a detailed microclimate study for hotspot {hs[0]['number']} before final design decisions (this engine uses geometric shade and a composite hot day).")
    sections.append((MAIN_SECTIONS[4], [("ul", rec or ["No option or design can be recommended from this run yet."]), ("h3", "Next steps"), ("ul", steps or ["None."])]))
    return sections
