"""Analysis report (FR-33) as self-contained HTML and PDF, plus structured exports (FR-34)."""

from __future__ import annotations

import csv
import html
import io
import json
import math
import zipfile
from pathlib import Path

from shapely.geometry import shape

from . import __version__, basis, db, files, plans
from .cost import STALE_PRICE_YEARS
from .orchestrator import run_bundle
from .readiness import ANALYSES, CONDITION
from .values import DESIGN, EXISTING, condition_of
from .settings import settings
from .values import OUTPUT_KIND_LABELS

STATUS_LABEL = {"completed": "Completed", "partial": "Completed with limitations", "blocked": "Blocked", "unavailable": "Data unavailable",
                "failed": "Failed", "skipped": "Not selected"}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _fmt(m: dict) -> str:
    if m["state"] != "available":
        return f"<em>{_e(m['state'].replace('_', ' '))}</em> — {_e(m['reason'])}"
    v = m["value"]
    return f"{v:,.4g} {_e(m['unit'] or '')}" if abs(v) < 1e5 else f"{v:,.0f} {_e(m['unit'] or '')}"


def _map_png(project: dict, scenario: dict | None, bundle: dict, out: Path) -> str | None:
    """Static map: site boundary, design features and, if available, the LST class overlay."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not project.get("boundary") and not scenario:
        return None
    fig, ax = plt.subplots(figsize=(6.5, 5), dpi=130)
    colors = {"building": "#6b6b6b", "road": "#d9a400", "green_space": "#2e8b57", "tree": "#1b5e20", "parking": "#9e9e9e",
              "open_space": "#c8b88a", "water": "#3a7bd5", "shade_structure": "#8d6e63"}
    if scenario:
        for f in scenario["features"]["features"]:
            g = shape(f["geometry"])
            c = colors.get(f["properties"].get("feature_type"), "#cccccc")
            if g.geom_type in ("Polygon", "MultiPolygon"):
                for p in getattr(g, "geoms", [g]):
                    ax.fill(*p.exterior.xy, color=c, alpha=0.75, lw=0.3)
            elif g.geom_type in ("LineString", "MultiLineString"):
                for ln in getattr(g, "geoms", [g]):
                    ax.plot(*ln.xy, color=c, lw=1)
            else:
                ax.plot(g.x, g.y, "o", color=c, ms=3)
    if project.get("boundary"):
        b = shape(project["boundary"])
        for p in getattr(b, "geoms", [b]):
            ax.plot(*p.exterior.xy, color="#c62828", lw=1.6, label="Site boundary")
    hot = next((f for f in bundle["findings"] if f["rule_id"] == "HEAT-LST-02" and f.get("geometry")), None)
    if hot:
        g = shape(hot["geometry"])
        for p in getattr(g, "geoms", [g]):
            ax.fill(*p.exterior.xy, facecolor="none", edgecolor="#d7191c", hatch="///", lw=0.8)
    from .analysis.hotspots import SEVERITY_COLORS as sev_color

    for hs in bundle.get("hotspots") or []:
        if not hs.get("geometry"):
            continue
        g = shape(hs["geometry"])
        for p in getattr(g, "geoms", [g]):
            ax.fill(*p.exterior.xy, facecolor=sev_color.get(hs["severity"], "#999999"), edgecolor="#5a0000", alpha=0.45, lw=0.8)
        pt = g.representative_point()
        ax.annotate(str(hs["number"]), (pt.x, pt.y), ha="center", va="center", fontsize=8, fontweight="bold", color="white",
                    bbox={"boxstyle": "circle,pad=0.2", "fc": "#5a0000", "ec": "none"})
    ax.set_aspect(1 / max(0.2, math.cos(math.radians(project["latitude"]))))
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title("Site and design features" + (" (hatched: hottest-decile surfaces)" if hot else "")
                 + (" · numbered: design hotspots" if any(h.get("geometry") for h in bundle.get("hotspots") or []) else ""), fontsize=9)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    path = out / "report_map.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def _tradeoff_png(comparison: dict | None, out: Path) -> str | None:
    """Cost against heat-stress area-hours, Pareto front joined (S5)."""
    t = ((comparison or {}).get("result") or {}).get("tradeoffs")
    pts = [p for p in (t or {}).get("points", []) if not p["missing"]]
    if not pts:
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 3.6), dpi=130)
    front = [next(p for p in pts if p["id"] == i) for i in t["front"]]
    if len(front) > 1:
        ax.plot([p["cost"] for p in front], [p["heat_m2h"] for p in front], "-", color="#2c7bb6", lw=1.5, label="Pareto front")
    for p in pts:
        ax.plot(p["cost"], p["heat_m2h"], "o", color="#2c7bb6" if p.get("pareto") else "#999999", ms=7)
        ax.annotate(p["name"], (p["cost"], p["heat_m2h"]), textcoords="offset points", xytext=(6, 6), fontsize=8)
    ax.set_xlabel(f"Indicative cost (typical){' — ' + pts[0]['currency'] if pts[0].get('currency') else ''}", fontsize=8)
    ax.set_ylabel("Heat stress (m²·h)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.set_title("Trade-off: cost against heat stress (lower-left is better)", fontsize=9)
    fig.tight_layout()
    path = out / "report_tradeoff.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def _comparison_lines(res: dict, names: dict) -> list[tuple[str, list[str]]]:
    """Choice, robustness, trade-offs, give-ups and hotspots of an S5 comparison, as (heading, lines)."""
    out = []
    ch = res.get("choice")
    if ch:
        lines = [ch["statement"]]
        if ch["mode"] != "no_winner":
            lines.append("Weights used: " + "; ".join(f"{w['label']} ×{w['weight']:g}" for w in ch["weights"])
                         + (f"; budget {ch['budget']['amount']:,.0f} {ch['budget']['currency']}" if ch.get("budget") else ""))
            lines += [f"Not eligible: {x['name']} ({x['why']})" for x in ch["excluded"]]
            lines += [ch["robustness"]["statement"], ch["robustness"]["cost_note"], ch["rule"]]
        out.append(("The choice", lines))
    t = res.get("tradeoffs")
    if t:
        c = t["chart"]
        lines = []
        for p in c["points"]:
            cost_txt = "price required" if p["cost"] is None else f"{p['cost']:,.0f} {p['currency'] or ''}".strip()
            heat_txt = "—" if p["heat_m2h"] is None else f"{p['heat_m2h']:,.0f} m²·h"
            lines.append(f"{p['name']}: {cost_txt}, {heat_txt}" + (" (Pareto front)" if p.get("pareto") else ""))
        out.append(("Trade-off: cost against heat stress", lines + [c["note"]]))
        g_lines = []
        for g in t["give_up"]:
            if "parking_spaces_lost" not in g:
                continue
            water = f"{g['irrigation_l_per_day']:,.0f} L/day" if g.get("irrigation_l_per_day") is not None else (g.get("irrigation_note") or "")
            maint = ", ".join(f"{v:,.0f} {k}" for k, v in (g.get("maintenance_per_year") or {}).items()) or (g.get("maintenance_note") or "")
            g_lines.append(f"{names.get(g['alternative_id'], g['alternative_id'])}: {g['parking_spaces_lost']} parking spaces; {g['building_footprint_lost_m2']:,.0f} m² footprint and "
                           f"{g['floor_area_lost_m2']:,.0f} m² floor area; {g['trees_added']} trees and {g['planting_added_m2']:,.0f} m² planting ({water}); maintenance: {maint}")
        if g_lines:
            out.append(("What each alternative gives up", g_lines))
    h = res.get("hotspots")
    if h:
        out.append(("Design hotspots", [f"{names.get(sid, sid)}: {s['count']} hotspots ({s['high_count']} high, {s['moderate_count']} moderate, {s['low_count']} low), "
                                        f"{s['located_area_m2']:,.0f} m² located" + (f"; {s['vs_baseline']['outcome']} vs baseline ({s['vs_baseline']['located_area_m2']:+,.0f} m²)" if s.get("vs_baseline") else "")
                                        for sid, s in h.items()]))
    return out


def _climate_png(bundle: dict, out: Path) -> str | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    det = ((bundle["run"].get("module_status") or {}).get("climate_baseline") or {}).get("details") or {}
    clim = (det.get("summary") or {}).get("monthly_climatology") or {}
    if not clim.get("tmax_c"):
        return None
    months = list(range(1, 13))
    fig, ax = plt.subplots(figsize=(6.5, 2.8), dpi=130)
    ax.plot(months, clim["tmax_c"], "-o", color="#d7191c", ms=3, label="Mean daily max")
    if clim.get("tmin_c"):
        ax.plot(months, clim["tmin_c"], "-o", color="#2c7bb6", ms=3, label="Mean daily min")
    ax.set_xticks(months, ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.set_ylabel("Air temperature (°C)")
    ax.set_title(f"Monthly climatology {det['period']['start_year']}–{det['period']['end_year']} — {det['provider_name']}", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = out / "report_climate.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


def build(project: dict, run: dict, user: dict, comparison: dict | None = None) -> dict:
    bundle = run_bundle(run)
    scenario = db.get("scenarios", run["scenario_id"]) if run.get("scenario_id") else None
    out = files.outputs_dir(project["id"], run["id"])
    ms = run.get("module_status") or {}
    ran = {k: v for k, v in ms.items() if v["status"] != "skipped"}
    incomplete = [k for k, v in ran.items() if v["status"] != "completed"]
    stale = bundle["run"]["stale_modules"]
    complete = not incomplete and not stale
    skipped = [k for k, v in ms.items() if v["status"] == "skipped"]
    if not complete:
        label = "Partial — some analyses were limited, blocked, unavailable or are out of date"
    elif skipped:
        label = f"Complete for the {len(ran)} selected analyses; {len(skipped)} analyses were not selected for this run"
    else:
        label = "Complete"
    files_ = db.select("files", "project_id = ?", [project["id"]], order="uploaded_at")
    rules = db.select("rules", "project_id = ? AND status IN ('verified','candidate','rejected')", [project["id"]], order="rule_type")
    map_png = _map_png(project, scenario, bundle, out)
    clim_png = _climate_png(bundle, out)
    if comparison:
        comparison = {**comparison, "_tradeoff_png": _tradeoff_png(comparison, out)}
    summary = {"label": label, "complete": complete, "incomplete_modules": incomplete, "stale_modules": stale,
               "findings": len([f for f in bundle["findings"] if f["classification"] != "data_quality_issue"]),
               "data_quality_issues": len([f for f in bundle["findings"] if f["classification"] == "data_quality_issue"])}
    h = _html(project, run, bundle, scenario, files_, rules, comparison, summary, map_png, clim_png)
    html_path = out / "report.html"
    html_path.write_text(h, encoding="utf-8")
    pdf_path = out / "report.pdf"
    _pdf(pdf_path, project, run, bundle, scenario, files_, rules, comparison, summary, map_png, clim_png)
    rep = {"id": db.new_id("rpt"), "project_id": project["id"], "run_id": run["id"], "comparison_id": (comparison or {}).get("id"),
           "html_path": str(html_path), "pdf_path": str(pdf_path), "status": "complete" if complete else "partial", "summary": summary,
           "created_by": user["id"], "created_at": db.now()}
    db.insert("reports", rep)
    return rep


def _img_tag(path: str | None, alt: str) -> str:
    if not path:
        return ""
    import base64

    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{data}" alt="{_e(alt)}" style="max-width:100%">'


# One list of section titles for the HTML and PDF reports, covering FR-33 items 1-15.
SECTIONS = [
    "1. Project and site", "2. Objectives", "3. Inputs and verification status", "4. Analyses run", "5. Data sources",
    "6. Metrics", "7. Findings", "8. Recommendations", "9. Planning checks", "10. Scenario comparison and costs",
    "11. Assumptions, limitations and uncertainty", "12. Data-quality issues and unavailable analyses", "13. Methodology",
    "14. Reproducibility record",
]


HOTSPOT_HEADER = ["No.", "Type", "Area", "Size", "Intensity", "Severity and basis", "UTCI sun / shade", "Likely causes"]


def _hotspot_rows(bundle: dict) -> list[list[str]]:
    rows = []
    for h in bundle.get("hotspots") or []:
        sm = {s["name"]: s["value"] for s in h["supporting_metrics"]}
        utci = f"{sm['utci_sun_peak_c']:g} / {sm['utci_shade_peak_c']:g} °C" if "utci_sun_peak_c" in sm and "utci_shade_peak_c" in sm else "—"
        sev = h["severity"].upper() + (" (PROVISIONAL, capped)" if h.get("severity_capped") else "")
        rows.append([str(h["number"]), " + ".join(h["type_labels"]), f"{h['area_m2']:,.0f} m² ({h['site_share_pct']:g} % of site)" + (" site-wide" if h.get("site_wide") else ""),
                     h["size_class"], h["intensity"], f"{sev}: {h['severity_basis']}", utci, "; ".join(c["text"] for c in h["likely_causes"]) or "none of the listed rules matched"])
    return rows


OPTION_HEADER = ["Hotspot", "Option", "Measured effect", "Quantity", "Cost", "Confidence (effect / cost)", "Role", "Water"]


def _cost_text(c: dict) -> str:
    if not c or c.get("status") == "not_applicable":
        return "—"
    if c["status"] != "priced":
        return "price required"
    m = f"; maintenance {c['maintenance_per_year']:,.0f}/year" if c.get("maintenance_per_year") is not None else ""
    return f"{c['low']:,.0f}–{c['high']:,.0f} {c['currency']} (typical {c['typical']:,.0f}){m}" + (f" — price older than {STALE_PRICE_YEARS} years" if c.get("stale") else "")


def _option_rows(bundle: dict) -> list[list[str]]:
    num = {h["id"]: h["number"] for h in bundle.get("hotspots") or []}
    rows = []
    for r in bundle["recommendations"]:
        for o in r.get("options") or []:
            e = o["effect"]
            eff = (f"{e['treated_m2']:,.0f} m² treated (not ranked; heat effect not modelled)" if o["separate"]
                   else f"{e['before']:,.0f} → {e['after']:,.0f} {e['unit']} (−{e['change_pct']:g} %)")
            rows.append([str(num.get(r.get("hotspot_id"), "")), f"{o['title']} — {o['intensity_label']}", eff,
                         f"{o['quantity']['value']:,.0f} {o['quantity']['unit'] or ''}" + (f"; −{o['lost_parking_spaces']} parking spaces" if o.get("lost_parking_spaces") else ""),
                         _cost_text(o["cost"]), f"{o['effect_confidence']['level']} / {(o['cost'].get('confidence') or {}).get('level', '—')}",
                         ", ".join(o["roles"]) + (" (Pareto)" if o["pareto"] else ""), (o.get("water") or {}).get("note") or ("" if not o.get("water") else f"{o['water']['litres_per_day']} L/day")])
    return rows


def _assumptions(run: dict, ms: dict) -> list[str]:
    """Assumptions and limitations stated identically in the HTML and PDF reports."""
    out = sorted({lim for st in ms.values() for lim in (st.get("limitations") or [])})
    cfg = run.get("configuration") or {}
    if cfg:
        settings_txt = ", ".join(f"{k}={v}" for k, v in sorted(cfg.items()) if k != "modules")
        out.append(f"Run settings used as stated assumptions: {settings_txt}." if settings_txt else "Run settings: defaults.")
    out.append("Findings marked PROVISIONAL THRESHOLD use severity bands that have not been approved by the AI/Analysis team; they are "
               "not standards or legal limits, and their severity is capped at moderate until approved.")
    out.append("Regional climate statistics describe the reanalysis grid cell (~25 km), not conditions within the site.")
    out.append("Unknown, unavailable, blocked and not-applicable values are shown as such and never as zero.")
    return out


def _methodology(run: dict, bundle: dict) -> list[tuple[str, list[str]]]:
    """Per analysis, the distinct calculation methods recorded on its metrics."""
    out = []
    for mod in (run.get("module_status") or {}):
        methods = []
        for m in bundle["metrics"]:
            if m["module"] == mod and m.get("method") and m["method"] not in methods:
                methods.append(m["method"])
        if methods:
            out.append((ANALYSES[mod], methods))
    return out


def _basis_rows(run: dict) -> list[list[str]]:
    """Every value the run's modules use, with its basis (PHASE2_SPEC §3.1)."""
    rows = []
    for r in basis.used_by(set((run.get("configuration") or {}).get("modules") or [])):
        refs = ", ".join(r["references"]) or "—"
        status = "PROVISIONAL" if basis.is_provisional(r["id"]) else "approved " + r["approved_on"] if r.get("approved_by") else "accepted by source"
        val = r["value"] if not isinstance(r["value"], (dict, list)) else json.dumps(r["value"], ensure_ascii=False)
        rows.append([r["id"], f"{val}{' ' + r['unit'] if r.get('unit') else ''}", basis.BASIS_LABELS[r["basis_type"]], refs,
                     r["applicability"] + (f" ({r['reason']})" if r.get("reason") else ""), status])
    return rows


BASIS_HEADER = ["Value", "Setting", "Basis", "References", "Applies to", "Status"]


def _insights(run: dict) -> dict | None:
    rows = db.select("insights", "run_id = ?", [run["id"]], order="created_at DESC")
    return rows[0] if rows else None


def _insight_lines(ins: dict) -> list[tuple[str, list[str]]]:
    """(heading, ["text [ids]", ...]) for the executive insights block."""
    titles = (("site_status", "Site status"), ("top_issues", "Main problems (priority order)"), ("attention_first", "What needs attention first"),
              ("limitations", "Limitations"))
    out = [(t, [f"{c['text']} [{', '.join(c.get('evidence_ids') or []) or 'run summary'}]" for c in ins["blocks"].get(k) or []]) for k, t in titles]
    return [x for x in out if x[1]]


def _html(project, run, bundle, scenario, files_, rules, comparison, summary, map_png, clim_png) -> str:
    ms = run.get("module_status") or {}
    rows = []
    sec = []
    sec.append(f"<h1>URBANOVA analysis report — {_e(project['name'])}</h1>")
    banner = "ok" if summary["complete"] else "warn"
    sec.append(f'<p class="banner {banner}"><strong>Report status: {_e(summary["label"])}.</strong> URBANOVA is a decision-support tool; this report is not a statutory approval, engineering design or professional sign-off.</p>')
    ins = _insights(run)
    if ins:
        label = ins["label"] + (f" ({ins['model']})" if ins.get("model") else "")
        sec.append(f"<section class='insights'><h2>Insights</h2><p><em>{_e(label)}. Evidence ids in brackets refer to records in this report.</em></p>")
        for head, lines in _insight_lines(ins):
            sec.append(f"<h3>{_e(head)}</h3><ul>" + "".join(f"<li>{_e(x)}</li>" for x in lines) + "</ul>")
        sec.append("</section>")
    sec.append("<h2>" + SECTIONS[0] + "</h2><table>")
    for k, v in (("Project ID", project["id"]), ("Type", project.get("project_type") or "not stated"), ("Location", f"{project['latitude']:.5f}, {project['longitude']:.5f} (WGS 84)"),
                 ("Country / address", " / ".join(x for x in (project.get("country"), project.get("city_or_address")) if x) or "not stated"),
                 ("Site boundary", "supplied" if project.get("boundary") else "not supplied — point-buffer statistics"),
                 ("Location confirmed", project.get("location_confirmed_at") or "no"),
                 ("Scenario analysed", f"{scenario['name']} ({scenario['kind']}, {scenario['id']})" if scenario else "none — site context only"),
                 ("Run", f"{run['id']} · status {run['status']} · {run.get('completed_at')}"), ("Software / methods", f"{__version__} / {run['analysis_version']}"),
                 ("Data-licence context", "Non-commercial evaluation prototype — some sources (e.g. Open-Meteo free tier) may not be used commercially" if settings.usage_context != "commercial" else "Commercial")):
        sec.append(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>")
    sec.append("</table>")
    sec.append(_img_tag(map_png, "Map of site boundary and design features"))
    sec.append("<h2>" + SECTIONS[1] + "</h2><ul>")
    for o in bundle["objectives"]:
        status = ("cannot be answered with the available data" if not o["answerable"]
                  else "partly answered — some evidence unavailable" if o.get("not_available") else "answered with available evidence")
        extra = "" if not o.get("not_available") else " — missing: " + "; ".join(o["not_available"])
        sec.append(f"<li>{_e(o['question'])} <em>({_e(status)})</em>{_e(extra)}</li>")
    sec.append("</ul>")
    sec.append("<h2>" + SECTIONS[2] + "</h2><table><tr><th>File</th><th>Category</th><th>Detected type</th><th>Status</th><th>SHA-256</th></tr>")
    for f in files_:
        sec.append(f"<tr><td>{_e(f['original_filename'])}</td><td>{_e(f['category'])}</td><td>{_e(f['detected_type'])}</td><td>{_e(f['status'])}</td><td><code>{_e(f['checksum'][:16])}…</code></td></tr>")
    if not files_:
        sec.append('<tr><td colspan="5">No files uploaded (coordinate-only project).</td></tr>')
    sec.append("</table>")
    sec.append("<h2>" + SECTIONS[3] + "</h2><table><tr><th>Analysis</th><th>Status</th><th>Notes</th></tr>")
    for mod, st in ms.items():
        notes = st.get("reason") or "; ".join(st.get("warnings") or [])
        stale = bundle["run"]["stale_modules"].get(mod)
        if stale:
            notes += f" OUT OF DATE: inputs changed since this run ({', '.join(stale)})."
        sec.append(f"<tr><td>{_e(st['label'])}</td><td class='st-{_e(st['status'])}'>{_e(STATUS_LABEL.get(st['status'], st['status']))}</td><td>{_e(notes)}</td></tr>")
    sec.append("</table>")
    sec.append("<h2>" + SECTIONS[4] + "</h2><table><tr><th>Dataset</th><th>Source</th><th>Nature</th><th>Coverage</th><th>Resolution</th><th>Retrieved</th><th>Licence / attribution</th><th>Source status</th></tr>")
    for d in bundle["datasets"]:
        if not d:
            continue
        cov = d.get("time_coverage") or {}
        cov_txt = cov.get("start", "") + (" – " + cov.get("end", "") if cov.get("end") else "") if "start" in cov else ", ".join(s[:10] for s in cov.get("scenes", [])) or cov.get("osm_snapshot", "")
        sec.append(f"<tr><td>{_e(d['dataset_type'])}<br><code>{_e(d['id'])}</code></td><td>{_e(d['source_id'])}</td><td>{_e(d.get('data_nature'))}</td><td>{_e(cov_txt)}</td><td>{_e(d.get('spatial_resolution'))}</td><td>{_e(d['created_at'])}</td><td>{_e(d.get('license'))}<br>{_e(d.get('attribution'))}</td><td>{_e(d.get('approval_label'))}; usage: {_e(d.get('usage_context'))}</td></tr>")
    sec.append("</table>")
    sec.append(_img_tag(clim_png, "Monthly climatology chart"))
    sec.append("<h2>" + SECTIONS[5] + "</h2><p>Kind of value: " + "; ".join(f"<b>{_e(v)}</b>" for v in OUTPUT_KIND_LABELS.values()) + ".</p>")
    for mod in ms:
        mm = [m for m in bundle["metrics"] if m["module"] == mod]
        if not mm:
            continue
        sec.append(f"<h3>{_e(ANALYSES[mod])} <small>({'proposed design' if CONDITION.get(mod) == DESIGN else 'existing land'})</small></h3><table><tr><th>Metric</th><th>Value</th><th>Kind</th><th>Method</th></tr>")
        for m in mm:
            note = " <small>(same for every design)</small>" if CONDITION.get(mod) == DESIGN and condition_of(m) == EXISTING else ""
            sec.append(f"<tr><td>{_e(m['label'])}{note}</td><td>{_fmt(m)}</td><td>{_e(OUTPUT_KIND_LABELS.get(m['output_kind']))}</td><td>{_e(m['method'])}</td></tr>")
        sec.append("</table>")
    sec.append("<h2>" + SECTIONS[6] + "</h2>")
    sec.append("<h3 id='hotspots'>Design hotspots</h3>")
    if bundle.get("hotspots"):
        sec.append("<p>Places on the drawing with a measured problem, numbered by priority (severity, then area) and shown on the map above. "
                   "Severity follows the PHASE2_SPEC §4.2 table; heat stress uses the UTCI scale.</p><table><tr>" + "".join(f"<th>{_e(c)}</th>" for c in HOTSPOT_HEADER) + "</tr>")
        for row in _hotspot_rows(bundle):
            sec.append("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>")
        sec.append("</table>")
    else:
        notes = ((ms.get("hotspots") or {}).get("details") or {}).get("notes") or []
        sec.append(f"<p>No design hotspots. {_e(' '.join(notes) or (ms.get('hotspots') or {}).get('reason') or 'Hotspot detection was not part of this run.')}</p>")
    for c in bundle.get("site_context") or []:
        sec.append(f"<div class='context'><h3>Site context (today's land)</h3><p>{_e(c['statement'])}</p><p><small>{_e(c['method'])}. {_e(c['note'])}</small></p></div>")
    concerns = [f for f in bundle["findings"] if f["classification"] != "data_quality_issue"]
    if not concerns:
        sec.append("<p>No rule-based environmental or planning findings were triggered by the available evidence. This does not mean the site has no issues — see the analyses that could not run.</p>")
    for f in concerns:
        sec.append(f"<div class='finding sev-{_e(f['severity'])}'><h3>[{_e(f['severity'].upper())}]{' [PROVISIONAL THRESHOLD — NOT APPROVED]' if (f.get('threshold') or {}).get('status') == 'provisional_not_approved' else ''} {_e(f['title'])}</h3><p>{_e(f['summary'])}</p>"
                   + (f"<p><b>Affected area:</b> {_e(f['affected_area'])}</p>" if f.get("affected_area") else "")
                   + f"<p><b>Classification:</b> {_e(f['classification'].replace('_', ' '))} · <b>Confidence:</b> {_e(f['confidence'])} · <b>Rule:</b> {_e(f['severity_rule'])}</p>"
                   f"<ul>{''.join('<li>' + _e(x) + '</li>' for x in f.get('limitations') or [])}</ul></div>")
    sec.append("<h2>" + SECTIONS[7] + "</h2>")
    for r in bundle["recommendations"]:
        sec.append(f"<div class='rec'><h3>{_e(r['title'])}</h3><p><b>Problem:</b> {_e(r['problem'])}</p><p><b>Intervention:</b> {_e(r['intervention'])}</p>"
                   f"<p><b>Expected direction (not quantified):</b> {_e(r['expected_direction'])}</p><p><b>Trade-offs:</b> {_e('; '.join(r['tradeoffs']))}</p>"
                   f"<p><b>How to evaluate:</b> {_e(r['evaluation_method'])}</p><p><b>Review status:</b> {_e(r['review_status'])}</p></div>")
    if not bundle["recommendations"]:
        sec.append("<p>No recommendations (no supporting findings).</p>")
    opt_rows = _option_rows(bundle)
    if opt_rows:
        sec.append("<h3 id='options'>Measured options per hotspot</h3><p>Each option was applied to a copy of the design and measured by re-running the design analyses. "
                   "Costs come only from supplied rates or the approved cost library; otherwise 'price required'. Effect and cost confidences are kept separate.</p><table><tr>"
                   + "".join(f"<th>{_e(c)}</th>" for c in OPTION_HEADER) + "</tr>" + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in opt_rows) + "</table>")
    sec.append("<h2>" + SECTIONS[8] + "</h2>")
    checks = (ms.get("planning_compliance", {}).get("details") or {}).get("checks") or []
    if not checks:
        sec.append(f"<p><b>Not assessed.</b> {_e(ms.get('planning_compliance', {}).get('reason') or 'Planning compliance was not part of this run.')}</p>")
    else:
        sec.append("<table><tr><th>Rule</th><th>Limit</th><th>Measured</th><th>Result</th><th>Source</th></tr>")
        for c in checks:
            sec.append(f"<tr><td>{_e(c['label'])}</td><td>{_e(c['operator'])} {_e(c['limit'])} {_e(c['unit'] or '')}</td><td>{_e(c.get('measured', '—'))}</td>"
                       f"<td>{_e(c.get('label_result') or c.get('reason'))}</td><td>{_e(c['rule_source']['document'])} p.{_e(c['rule_source']['page'])}</td></tr>")
        sec.append("</table>")
    sec.append("<table><tr><th>Rule record</th><th>Status</th><th>Value</th><th>Source text</th></tr>")
    for r in rules:
        sec.append(f"<tr><td>{_e(r['rule_type'])}</td><td>{_e(r['status'])}</td><td>{_e(r['value'] if r['value'] is not None else r['value_text'])}</td><td>{_e((r['exact_text'] or '')[:220])} ({_e(r['document_name'])}, p.{_e(r['page'])})</td></tr>")
    sec.append("</table>")
    sec.append("<h2>" + SECTIONS[9] + "</h2>")
    if comparison and comparison["result"].get("comparable"):
        res = comparison["result"]
        names = comparison.get("scenario_names") or {}
        for head, lines in _comparison_lines(res, names):
            sec.append(f"<h3>{_e(head)}</h3><ul>" + "".join(f"<li>{_e(x)}</li>" for x in lines) + "</ul>")
            if head.startswith("Trade-off"):
                sec.append(_img_tag(comparison.get("_tradeoff_png"), "Trade-off chart: cost against heat stress"))
        sec.append(f"<p>{_e(res['statement'])} {_e(res['direction_note'])}</p><table><tr><th>Alternative</th><th>Metric</th><th>Baseline</th><th>Alternative</th><th>Difference</th><th>Outcome</th></tr>")
        for r in res["rows"]:
            b = r["baseline"].get("value")
            a = r["alternative"].get("value")
            sec.append(f"<tr><td>{_e(names.get(r['alternative_id'], r['alternative_id']))}</td><td>{_e(r['label'])}</td><td>{_e(b if b is not None else r['baseline'].get('state'))}</td><td>{_e(a if a is not None else r['alternative'].get('state'))}</td>"
                       f"<td>{_e(r.get('difference', '—'))}</td><td>{_e(r['outcome'].replace('_', ' '))}{(' — ' + _e(r.get('reason'))) if r.get('reason') else ''}</td></tr>")
        sec.append(f"</table><p><em>{_e(res['site_conditions_note'])}</em></p>")
        for aid, c in res["costs"].items():
            sec.append(f"<h3>Indicative cost — {_e(names.get(aid, aid))}</h3><p>{_e(c['label'])} Status: {_e(c['status'])}.</p><table><tr><th>Item</th><th>Quantity</th><th>Rate</th><th>Subtotal</th><th>Source</th></tr>")
            for ln in c["lines"]:
                sec.append(f"<tr><td>{_e(ln['label'])}</td><td>{_e(ln['quantity'])} {_e(ln['unit'])}</td><td>{_e(ln.get('rate', '—'))} {_e(ln.get('currency', ''))}</td><td>{_e(ln.get('subtotal', 'price required'))}</td><td>{_e(ln.get('rate_source', ln.get('note')))}</td></tr>")
            sec.append(f"</table><p>Totals: {_e(c['totals_by_currency'] or 'none')}. Exclusions: {_e('; '.join(c['exclusions']))}</p>")
    elif comparison:
        sec.append("<p><b>Comparison not valid:</b> " + _e(" ".join(comparison["result"].get("issues", []))) + "</p>")
    else:
        sec.append("<p>No scenario comparison is included in this report.</p>")
    sec.append(f"<h2>{SECTIONS[10]}</h2><ul>")
    for lim in _assumptions(run, ms):
        sec.append(f"<li>{_e(lim)}</li>")
    sec.append(f"</ul><h2>{SECTIONS[11]}</h2><ul>")
    dq = [f for f in bundle["findings"] if f["classification"] == "data_quality_issue"]
    for f in dq:
        sec.append(f"<li><b>{_e(f['title'])}</b> — {_e(f['summary'])}</li>")
    if not dq:
        sec.append("<li>None recorded for this run.</li>")
    sec.append(f"</ul><h2>{SECTIONS[12]}</h2><p>Analysis methods version {_e(run['analysis_version'])}. The calculation recorded for each metric, by analysis:</p>")
    for label, methods in _methodology(run, bundle):
        sec.append(f"<h3>{_e(label)}</h3><ul>" + "".join(f"<li>{_e(x)}</li>" for x in methods) + "</ul>")
    sec.append("<h3>Values used and their basis</h3><table><tr>" + "".join(f"<th>{h}</th>" for h in BASIS_HEADER) + "</tr>"
               + "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in _basis_rows(run)) + "</table>")
    sec.append("<p>Full descriptions, data sources and thresholds: docs/METHODOLOGY.md, urbanova/config/rules.json and urbanova/config/basis.json in the project repository. References are listed in docs/PHASE2_SPEC.md §12.</p>")
    sec.append(f"<h2>{SECTIONS[13]}</h2><pre>{_e(json.dumps({'run_id': run['id'], 'configuration': run['configuration'], 'fingerprint': run['fingerprint'], 'datasets': run.get('input_dataset_ids')}, indent=1))}</pre>")
    css = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1d1d1d;line-height:1.45}
table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13px}th,td{border:1px solid #ccc;padding:5px 7px;text-align:left;vertical-align:top}th{background:#f2f2f2}
.banner{padding:10px 12px;border-radius:6px}.banner.ok{background:#e8f5e9}.banner.warn{background:#fff3e0;border:1px solid #f0a020}
.finding,.rec,.context{border-left:4px solid #888;padding:4px 12px;margin:10px 0;background:#fafafa}.context{border-color:#2c7bb6}.sev-high{border-color:#c62828}.sev-moderate{border-color:#ef6c00}.sev-low{border-color:#f9a825}
.st-completed{color:#1b5e20}.st-partial{color:#e65100}.st-failed,.st-unavailable,.st-blocked{color:#b71c1c}h2{border-bottom:2px solid #ddd;padding-bottom:3px;margin-top:28px}
@media print{body{max-width:none}.finding,.rec,table{page-break-inside:avoid}}"""
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>URBANOVA report — {_e(project['name'])}</title><style>{css}</style></head><body>{''.join(sec)}<footer><p>Generated {_e(db.now())} by URBANOVA {_e(__version__)}.</p></footer></body></html>"


def _pdf(path: Path, project, run, bundle, scenario, files_, rules, comparison, summary, map_png, clim_png) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Base-14 PDF fonts lack —, ≥, →, × etc.; embed DejaVu Sans (bundled with matplotlib).
    import matplotlib

    font_dir = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(font_dir / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(font_dir / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Oblique", str(font_dir / "DejaVuSans-Oblique.ttf")))
        from reportlab.lib.fonts import addMapping

        addMapping("DejaVu", 0, 0, "DejaVu")
        addMapping("DejaVu", 1, 0, "DejaVu-Bold")
        addMapping("DejaVu", 0, 1, "DejaVu-Oblique")
        addMapping("DejaVu", 1, 1, "DejaVu-Bold")
    ss = getSampleStyleSheet()
    for name in ("Title", "Heading2", "BodyText"):
        ss[name].fontName = "DejaVu-Bold" if name != "BodyText" else "DejaVu"
    small = ss["BodyText"].clone("small", fontSize=8, leading=10, fontName="DejaVu")
    story = [Paragraph(f"URBANOVA analysis report — {_e(project['name'])}", ss["Title"]),
             Paragraph(f"<b>Report status: {_e(summary['label'])}.</b> Decision support only — not a statutory approval or professional sign-off.", ss["BodyText"]),
             Paragraph(f"Project {project['id']} · {project['latitude']:.5f}, {project['longitude']:.5f} · Run {run['id']} ({run['status']}) · Scenario: {_e(scenario['name']) if scenario else 'none'}", small),
             Spacer(1, 6)]
    if map_png:
        story.append(Image(map_png, width=15 * cm, height=11.5 * cm, kind="proportional"))

    def table(data, widths):
        t = Table([[Paragraph(_e(c) if not isinstance(c, Paragraph) else c.text, small) for c in row] for row in data], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    ms = run.get("module_status") or {}
    h = lambda i: Paragraph(SECTIONS[i], ss["Heading2"])
    bullet = lambda t: Paragraph(f"• {_e(t)}", small)
    ins = _insights(run)
    block = []
    if ins:
        label = ins["label"] + (f" ({ins['model']})" if ins.get("model") else "")
        block = [Paragraph("Insights", ss["Heading2"]), Paragraph(f"<i>{_e(label)}</i>", small)]
        for head, lines in _insight_lines(ins):
            block.append(Paragraph(f"<b>{_e(head)}</b>", small))
            block += [Paragraph(f"• {_e(x)}", small) for x in lines]
        story[3:3] = block
    story.insert(3 + len(block), h(0))
    story.insert(4 + len(block), table([["Item", "Value"]] + [
        ["Project", f"{project['name']} ({project['id']})"], ["Type", project.get("project_type") or "not stated"],
        ["Location", f"{project['latitude']:.5f}, {project['longitude']:.5f} (WGS 84)"],
        ["Site boundary", "supplied" if project.get("boundary") else "not supplied — point-buffer statistics"],
        ["Location confirmed", project.get("location_confirmed_at") or "no"],
        ["Scenario analysed", f"{scenario['name']} ({scenario['kind']})" if scenario else "none — site context only"],
        ["Software / methods", f"{__version__} / {run['analysis_version']}"],
        ["Data-licence context", "Non-commercial evaluation prototype" if settings.usage_context != "commercial" else "Commercial"]], [4.5 * cm, 12.7 * cm]))
    story.append(h(1))
    for o in bundle["objectives"]:
        status = ("cannot be answered with the available data" if not o["answerable"]
                  else "partly answered — some evidence unavailable" if o.get("not_available") else "answered with available evidence")
        story.append(bullet(f"{o['question']} ({status})" + ("" if not o.get("not_available") else " — missing: " + "; ".join(o["not_available"]))))
    story.append(h(2))
    story.append(table([["File", "Category", "Detected type", "Status"]] + [[f["original_filename"], f["category"], f["detected_type"], f["status"]] for f in files_]
                       if files_ else [["File"], ["No files uploaded (coordinate-only project)."]], [6 * cm, 3.5 * cm, 3 * cm, 4.7 * cm] if files_ else [17.2 * cm]))
    story += [h(3),
              table([["Analysis", "Status", "Notes"]] + [[st["label"], STATUS_LABEL.get(st["status"], st["status"]), (st.get("reason") or "; ".join(st.get("warnings") or []))[:300]] for st in ms.values()], [6 * cm, 3.2 * cm, 8 * cm])]
    story += [h(4),
              table([["Dataset", "Source", "Nature", "Licence / attribution", "Source status"]] + [[f"{d['dataset_type']} ({d['id']})", d["source_id"], d.get("data_nature") or "", f"{d.get('license') or ''} — {d.get('attribution') or ''}", d.get("approval_label") or ""] for d in bundle["datasets"] if d],
                    [3.4 * cm, 2.8 * cm, 1.8 * cm, 5 * cm, 4.2 * cm])]
    if clim_png:
        story.append(Image(clim_png, width=16 * cm, height=6.9 * cm, kind="proportional"))
    story.append(h(5))
    rows = [["Analysis", "Metric", "Value", "Kind"]]
    for m in bundle["metrics"]:
        val = f"{m['value']:,.4g} {m['unit'] or ''}" if m["state"] == "available" else f"{m['state']}: {m['reason']}"
        rows.append([ANALYSES[m["module"]], m["label"], val, OUTPUT_KIND_LABELS.get(m["output_kind"], "")])
    story.append(table(rows, [3.4 * cm, 6.2 * cm, 4.6 * cm, 3 * cm]))
    story.append(h(6))
    story.append(Paragraph("<b>Design hotspots</b>", small))
    if bundle.get("hotspots"):
        story.append(table([HOTSPOT_HEADER] + _hotspot_rows(bundle), [0.8 * cm, 2.3 * cm, 2.2 * cm, 1.3 * cm, 1.5 * cm, 4 * cm, 1.9 * cm, 3.2 * cm]))
    else:
        story.append(Paragraph("No design hotspots. " + _e(" ".join(((ms.get("hotspots") or {}).get("details") or {}).get("notes") or [])), small))
    for c in bundle.get("site_context") or []:
        story.append(Paragraph(f"<b>Site context (today's land):</b> {_e(c['statement'])} <i>{_e(c['note'])}</i>", small))
    concerns = [f for f in bundle["findings"] if f["classification"] != "data_quality_issue"]
    if not concerns:
        story.append(Paragraph("No rule-based environmental or planning findings were triggered by the available evidence. This does not mean the site has no issues.", small))
    for f in concerns:
        prov = " [PROVISIONAL THRESHOLD — NOT APPROVED]" if (f.get("threshold") or {}).get("status") == "provisional_not_approved" else ""
        area = f" Affected area: {_e(f['affected_area'])}." if f.get("affected_area") else ""
        story.append(Paragraph(f"<b>[{_e(f['severity'].upper())}]{prov} {_e(f['title'])}</b> — {_e(f['summary'])}{area} <i>({_e(f['classification'].replace('_', ' '))}; {_e(f['severity_rule'])})</i>", small))
        for lim in f.get("limitations") or []:
            story.append(Paragraph(f"&nbsp;&nbsp;• {_e(lim)}", small))
    story.append(h(7))
    for r in bundle["recommendations"]:
        story.append(Paragraph(f"<b>{_e(r['title'])}</b> — {_e(r['intervention'])} <i>Expected direction: {_e(r['expected_direction'])}</i> Evaluate by: {_e(r['evaluation_method'])}", small))
    if not bundle["recommendations"]:
        story.append(Paragraph("No recommendations (no supporting findings).", small))
    opt_rows = _option_rows(bundle)
    if opt_rows:
        story.append(Paragraph("<b>Measured options per hotspot</b> (applied to a copy of the design and re-measured; costs only from supplied rates)", small))
        story.append(table([OPTION_HEADER] + opt_rows, [1.2 * cm, 3.3 * cm, 3.2 * cm, 2 * cm, 2.6 * cm, 1.8 * cm, 1.6 * cm, 1.6 * cm]))
    story.append(h(8))
    checks = (ms.get("planning_compliance", {}).get("details") or {}).get("checks") or []
    if checks:
        story.append(table([["Rule", "Limit", "Measured", "Result"]] + [[c["label"], f"{c['operator']} {c['limit']} {c['unit'] or ''}", str(c.get("measured", "—")), c.get("label_result") or c.get("reason")] for c in checks], [4.5 * cm, 3 * cm, 2.5 * cm, 7.2 * cm]))
    else:
        story.append(Paragraph("Not assessed — " + _e(ms.get("planning_compliance", {}).get("reason") or "not part of this run."), small))
    story.append(h(9))
    if comparison and comparison["result"].get("comparable"):
        names = comparison.get("scenario_names") or {}
        for head, lines in _comparison_lines(comparison["result"], names):
            story.append(Paragraph(f"<b>{_e(head)}</b>", small))
            story += [bullet(x) for x in lines]
            if head.startswith("Trade-off") and comparison.get("_tradeoff_png"):
                story.append(Image(comparison["_tradeoff_png"], width=15 * cm, height=8.3 * cm, kind="proportional"))
        story.append(Paragraph(_e(comparison["result"]["statement"] + " " + comparison["result"]["direction_note"]), small))
        story.append(table([["Alternative", "Metric", "Baseline", "Alternative", "Difference", "Outcome"]] + [[names.get(r["alternative_id"], ""), r["label"], str(r["baseline"].get("value", r["baseline"].get("state"))), str(r["alternative"].get("value", r["alternative"].get("state"))), str(r.get("difference", "—")), r["outcome"]] for r in comparison["result"]["rows"]],
                           [2.8 * cm, 5.2 * cm, 2.3 * cm, 2.3 * cm, 2 * cm, 2.6 * cm]))
        for aid, c in comparison["result"]["costs"].items():
            story.append(Paragraph(f"Indicative cost — {_e(names.get(aid, aid))}: status {_e(c['status'])}; totals {_e(c['totals_by_currency'] or 'none')}. {_e(c['label'])}", small))
    elif comparison:
        story.append(Paragraph("Comparison not valid: " + _e(" ".join(comparison["result"].get("issues", []))), small))
    else:
        story.append(Paragraph("No scenario comparison is included in this report.", small))
    story.append(h(10))
    for lim in _assumptions(run, ms):
        story.append(bullet(lim))
    story.append(h(11))
    dq = [f for f in bundle["findings"] if f["classification"] == "data_quality_issue"]
    for f in dq:
        story.append(bullet(f"{f['title']} — {f['summary']}"))
    if not dq:
        story.append(bullet("None recorded for this run."))
    story.append(h(12))
    story.append(Paragraph(f"Analysis methods version {_e(run['analysis_version'])}. The calculation recorded for each metric, by analysis:", small))
    for label, methods in _methodology(run, bundle):
        story.append(Paragraph(f"<b>{_e(label)}</b>", small))
        for x in methods:
            story.append(bullet(x))
    story.append(Paragraph("<b>Values used and their basis</b>", small))
    story.append(table([BASIS_HEADER] + _basis_rows(run), [3.2 * cm, 2.6 * cm, 2.2 * cm, 2.4 * cm, 5 * cm, 2.2 * cm]))
    story.append(Paragraph("Full descriptions, data sources and thresholds: docs/METHODOLOGY.md, urbanova/config/rules.json and urbanova/config/basis.json in the project repository. References are listed in docs/PHASE2_SPEC.md §12.", small))
    story.append(h(13))
    story.append(Paragraph(_e(json.dumps({"run": run["id"], "analysis_version": run["analysis_version"], "configuration": run["configuration"], "fingerprint": run.get("fingerprint"), "datasets": run.get("input_dataset_ids")})), small))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title=f"URBANOVA report {project['name']}").build(story)


def export_zip(project: dict, run: dict) -> bytes:
    """JSON (metrics, findings, provenance), CSV (metrics), GeoJSON (design + findings), GeoTIFF rasters."""
    bundle = run_bundle(run)
    scenario = db.get("scenarios", run["scenario_id"]) if run.get("scenario_id") else None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("run.json", json.dumps({"project": {k: project[k] for k in ("id", "name", "latitude", "longitude", "boundary", "project_type")},
                                            **bundle, "crs": "EPSG:4326 for all vector geometry"}, indent=1, default=str))
        s = io.StringIO()
        w = csv.writer(s)
        w.writerow(["metric_id", "module", "metric_name", "label", "state", "value", "unit", "reason", "output_kind", "method", "source_dataset_ids"])
        for m in bundle["metrics"]:
            w.writerow([m["id"], m["module"], m["metric_name"], m["label"], m["state"], "" if m["value"] is None else m["value"], m["unit"] or "", m["reason"] or "", m["output_kind"], m["method"], ";".join(m["source_dataset_ids"] or [])])
        z.writestr("metrics.csv", s.getvalue())
        if scenario:
            z.writestr("design_features.geojson", json.dumps(scenario["features"]))
        opt = io.StringIO()
        ow = csv.writer(opt)
        ow.writerow(OPTION_HEADER + ["option_id", "intervention_id", "evaluation_run_id"])
        ids = [o for r in bundle["recommendations"] for o in r.get("options") or []]
        for row, o in zip(_option_rows(bundle), ids):
            ow.writerow(row + [o["id"], o["intervention_id"], o.get("evaluation_run_id") or ""])
        z.writestr("options.csv", opt.getvalue())
        z.writestr("findings.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": f.get("geometry"), "properties": {k: f.get(k) for k in ("id", "title", "severity", "classification", "rule_id", "summary", "affected_area")}} for f in bundle["findings"]]}))
        for lay in db.select("layers", "run_id = ?", [run["id"]]):
            tif = (lay.get("meta") or {}).get("geotiff")
            if tif and Path(tif).exists():
                z.write(tif, f"rasters/{Path(tif).name}")
            elif lay["path"] and lay["path"].endswith(".geojson") and Path(lay["path"]).exists():
                z.write(lay["path"], f"layers/{Path(lay['path']).name}")
            if lay["path"] and lay["path"].endswith(".png") and Path(lay["path"]).exists():
                z.write(lay["path"], f"images/{Path(lay['path']).name}")
        out = files.outputs_dir(project["id"], run["id"])
        for img in ("report_map.png", "report_climate.png"):  # presentation images (FR-33), present once a report was generated
            if (out / img).exists():
                z.write(out / img, f"images/{img}")
        z.writestr("README.txt", "URBANOVA export. Vector data in EPSG:4326 (lon/lat). Rasters are GeoTIFFs in the project's local UTM CRS (embedded). "
                                 "Metric 'state' distinguishes available values from unknown/unavailable/blocked/not_applicable (never zero).\n")
    return buf.getvalue()


def feature_layer(project_id: str) -> dict:
    return plans.feature_collection(plans.active_features(project_id))
