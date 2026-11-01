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

from . import __version__, db, files, plans
from .orchestrator import run_bundle
from .readiness import ANALYSES
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
    ax.set_aspect(1 / max(0.2, math.cos(math.radians(project["latitude"]))))
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.set_title("Site and design features" + (" (hatched: hottest-decile surfaces)" if hot else ""), fontsize=9)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    path = out / "report_map.png"
    fig.savefig(path)
    plt.close(fig)
    return str(path)


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


def _html(project, run, bundle, scenario, files_, rules, comparison, summary, map_png, clim_png) -> str:
    ms = run.get("module_status") or {}
    rows = []
    sec = []
    sec.append(f"<h1>URBANOVA analysis report — {_e(project['name'])}</h1>")
    banner = "ok" if summary["complete"] else "warn"
    sec.append(f'<p class="banner {banner}"><strong>Report status: {_e(summary["label"])}.</strong> URBANOVA is a decision-support tool; this report is not a statutory approval, engineering design or professional sign-off.</p>')
    sec.append("<h2>1. Project and site</h2><table>")
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
    sec.append("<h2>2. Objectives</h2><ul>")
    for o in bundle["objectives"]:
        status = ("cannot be answered with the available data" if not o["answerable"]
                  else "partly answered — some evidence unavailable" if o.get("not_available") else "answered with available evidence")
        extra = "" if not o.get("not_available") else " — missing: " + "; ".join(o["not_available"])
        sec.append(f"<li>{_e(o['question'])} <em>({_e(status)})</em>{_e(extra)}</li>")
    sec.append("</ul>")
    sec.append("<h2>3. Inputs and verification status</h2><table><tr><th>File</th><th>Category</th><th>Detected type</th><th>Status</th><th>SHA-256</th></tr>")
    for f in files_:
        sec.append(f"<tr><td>{_e(f['original_filename'])}</td><td>{_e(f['category'])}</td><td>{_e(f['detected_type'])}</td><td>{_e(f['status'])}</td><td><code>{_e(f['checksum'][:16])}…</code></td></tr>")
    if not files_:
        sec.append('<tr><td colspan="5">No files uploaded (coordinate-only project).</td></tr>')
    sec.append("</table>")
    sec.append("<h2>4. Analyses run</h2><table><tr><th>Analysis</th><th>Status</th><th>Notes</th></tr>")
    for mod, st in ms.items():
        notes = st.get("reason") or "; ".join(st.get("warnings") or [])
        stale = bundle["run"]["stale_modules"].get(mod)
        if stale:
            notes += f" OUT OF DATE: inputs changed since this run ({', '.join(stale)})."
        sec.append(f"<tr><td>{_e(st['label'])}</td><td class='st-{_e(st['status'])}'>{_e(STATUS_LABEL.get(st['status'], st['status']))}</td><td>{_e(notes)}</td></tr>")
    sec.append("</table>")
    sec.append("<h2>5. Data sources</h2><table><tr><th>Dataset</th><th>Source</th><th>Nature</th><th>Coverage</th><th>Resolution</th><th>Retrieved</th><th>Licence / attribution</th><th>Source status</th></tr>")
    for d in bundle["datasets"]:
        if not d:
            continue
        cov = d.get("time_coverage") or {}
        cov_txt = cov.get("start", "") + (" – " + cov.get("end", "") if cov.get("end") else "") if "start" in cov else ", ".join(s[:10] for s in cov.get("scenes", [])) or cov.get("osm_snapshot", "")
        sec.append(f"<tr><td>{_e(d['dataset_type'])}<br><code>{_e(d['id'])}</code></td><td>{_e(d['source_id'])}</td><td>{_e(d.get('data_nature'))}</td><td>{_e(cov_txt)}</td><td>{_e(d.get('spatial_resolution'))}</td><td>{_e(d['created_at'])}</td><td>{_e(d.get('license'))}<br>{_e(d.get('attribution'))}</td><td>{_e(d.get('approval_label'))}; usage: {_e(d.get('usage_context'))}</td></tr>")
    sec.append("</table>")
    sec.append(_img_tag(clim_png, "Monthly climatology chart"))
    sec.append("<h2>6. Metrics</h2><p>Kind of value: " + "; ".join(f"<b>{_e(v)}</b>" for v in OUTPUT_KIND_LABELS.values()) + ".</p>")
    for mod in ms:
        mm = [m for m in bundle["metrics"] if m["module"] == mod]
        if not mm:
            continue
        sec.append(f"<h3>{_e(ANALYSES[mod])}</h3><table><tr><th>Metric</th><th>Value</th><th>Kind</th><th>Method</th></tr>")
        for m in mm:
            sec.append(f"<tr><td>{_e(m['label'])}</td><td>{_fmt(m)}</td><td>{_e(OUTPUT_KIND_LABELS.get(m['output_kind']))}</td><td>{_e(m['method'])}</td></tr>")
        sec.append("</table>")
    sec.append("<h2>7. Findings</h2>")
    concerns = [f for f in bundle["findings"] if f["classification"] != "data_quality_issue"]
    if not concerns:
        sec.append("<p>No rule-based environmental or planning findings were triggered by the available evidence. This does not mean the site has no issues — see the analyses that could not run.</p>")
    for f in concerns:
        sec.append(f"<div class='finding sev-{_e(f['severity'])}'><h3>[{_e(f['severity'].upper())}]{' [PROVISIONAL THRESHOLD — NOT APPROVED]' if (f.get('threshold') or {}).get('status') == 'provisional_not_approved' else ''} {_e(f['title'])}</h3><p>{_e(f['summary'])}</p>"
                   f"<p><b>Classification:</b> {_e(f['classification'].replace('_', ' '))} · <b>Confidence:</b> {_e(f['confidence'])} · <b>Rule:</b> {_e(f['severity_rule'])}</p>"
                   f"<ul>{''.join('<li>' + _e(x) + '</li>' for x in f.get('limitations') or [])}</ul></div>")
    sec.append("<h2>8. Recommendations</h2>")
    for r in bundle["recommendations"]:
        sec.append(f"<div class='rec'><h3>{_e(r['title'])}</h3><p><b>Problem:</b> {_e(r['problem'])}</p><p><b>Intervention:</b> {_e(r['intervention'])}</p>"
                   f"<p><b>Expected direction (not quantified):</b> {_e(r['expected_direction'])}</p><p><b>Trade-offs:</b> {_e('; '.join(r['tradeoffs']))}</p>"
                   f"<p><b>How to evaluate:</b> {_e(r['evaluation_method'])}</p><p><b>Review status:</b> {_e(r['review_status'])}</p></div>")
    if not bundle["recommendations"]:
        sec.append("<p>No recommendations (no supporting findings).</p>")
    sec.append("<h2>9. Planning checks</h2>")
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
    sec.append("<h2>10. Scenario comparison and costs</h2>")
    if comparison and comparison["result"].get("comparable"):
        res = comparison["result"]
        names = comparison.get("scenario_names") or {}
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
    sec.append("<h2>11. Assumptions, limitations and uncertainty</h2><ul>")
    lims = sorted({lim for st in ms.values() for lim in (st.get("limitations") or [])})
    for lim in lims:
        sec.append(f"<li>{_e(lim)}</li>")
    sec.append("<li>Findings marked PROVISIONAL THRESHOLD use severity bands that have not been approved by the AI/Analysis team; they are not standards or legal limits.</li>")
    sec.append("<li>Unknown, unavailable, blocked and not-applicable values are shown as such and never as zero.</li></ul>")
    sec.append("<h2>12. Data-quality issues and unavailable analyses</h2><ul>")
    for f in bundle["findings"]:
        if f["classification"] == "data_quality_issue":
            sec.append(f"<li><b>{_e(f['title'])}</b> — {_e(f['summary'])}</li>")
    sec.append("</ul><h2>13. Methodology</h2><p>See docs/METHODOLOGY.md in the project repository for calculation details. Each metric above states its method; every dataset is listed with its ID for traceability.</p>")
    sec.append(f"<h2>14. Reproducibility record</h2><pre>{_e(json.dumps({'run_id': run['id'], 'configuration': run['configuration'], 'fingerprint': run['fingerprint'], 'datasets': run.get('input_dataset_ids')}, indent=1))}</pre>")
    css = """body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#1d1d1d;line-height:1.45}
table{border-collapse:collapse;width:100%;margin:8px 0 16px;font-size:13px}th,td{border:1px solid #ccc;padding:5px 7px;text-align:left;vertical-align:top}th{background:#f2f2f2}
.banner{padding:10px 12px;border-radius:6px}.banner.ok{background:#e8f5e9}.banner.warn{background:#fff3e0;border:1px solid #f0a020}
.finding,.rec{border-left:4px solid #888;padding:4px 12px;margin:10px 0;background:#fafafa}.sev-high{border-color:#c62828}.sev-moderate{border-color:#ef6c00}.sev-low{border-color:#f9a825}
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
    story += [Paragraph("Analyses run", ss["Heading2"]),
              table([["Analysis", "Status", "Notes"]] + [[st["label"], STATUS_LABEL.get(st["status"], st["status"]), (st.get("reason") or "; ".join(st.get("warnings") or []))[:300]] for st in ms.values()], [6 * cm, 3.2 * cm, 8 * cm])]
    story += [Paragraph("Data sources", ss["Heading2"]),
              table([["Dataset", "Source", "Nature", "Licence / attribution", "Source status"]] + [[f"{d['dataset_type']} ({d['id']})", d["source_id"], d.get("data_nature") or "", f"{d.get('license') or ''} — {d.get('attribution') or ''}", d.get("approval_label") or ""] for d in bundle["datasets"] if d],
                    [3.4 * cm, 2.8 * cm, 1.8 * cm, 5 * cm, 4.2 * cm])]
    if clim_png:
        story.append(Image(clim_png, width=16 * cm, height=6.9 * cm, kind="proportional"))
    story.append(Paragraph("Metrics", ss["Heading2"]))
    rows = [["Analysis", "Metric", "Value", "Kind"]]
    for m in bundle["metrics"]:
        val = f"{m['value']:,.4g} {m['unit'] or ''}" if m["state"] == "available" else f"{m['state']}: {m['reason']}"
        rows.append([ANALYSES[m["module"]], m["label"], val, OUTPUT_KIND_LABELS.get(m["output_kind"], "")])
    story.append(table(rows, [3.4 * cm, 6.2 * cm, 4.6 * cm, 3 * cm]))
    story.append(Paragraph("Findings", ss["Heading2"]))
    for f in bundle["findings"]:
        prov = " [PROVISIONAL THRESHOLD — NOT APPROVED]" if (f.get("threshold") or {}).get("status") == "provisional_not_approved" else ""
        story.append(Paragraph(f"<b>[{_e(f['severity'].upper())}]{prov} {_e(f['title'])}</b> — {_e(f['summary'])} <i>({_e(f['classification'].replace('_', ' '))}; {_e(f['severity_rule'])})</i>", small))
        for lim in f.get("limitations") or []:
            story.append(Paragraph(f"&nbsp;&nbsp;• {_e(lim)}", small))
    story.append(Paragraph("Recommendations", ss["Heading2"]))
    for r in bundle["recommendations"]:
        story.append(Paragraph(f"<b>{_e(r['title'])}</b> — {_e(r['intervention'])} <i>Expected direction: {_e(r['expected_direction'])}</i> Evaluate by: {_e(r['evaluation_method'])}", small))
    story.append(Paragraph("Planning checks", ss["Heading2"]))
    checks = (ms.get("planning_compliance", {}).get("details") or {}).get("checks") or []
    if checks:
        story.append(table([["Rule", "Limit", "Measured", "Result"]] + [[c["label"], f"{c['operator']} {c['limit']} {c['unit'] or ''}", str(c.get("measured", "—")), c.get("label_result") or c.get("reason")] for c in checks], [4.5 * cm, 3 * cm, 2.5 * cm, 7.2 * cm]))
    else:
        story.append(Paragraph("Not assessed — " + _e(ms.get("planning_compliance", {}).get("reason") or "not part of this run."), small))
    if comparison and comparison["result"].get("comparable"):
        names = comparison.get("scenario_names") or {}
        story.append(Paragraph("Scenario comparison", ss["Heading2"]))
        story.append(Paragraph(_e(comparison["result"]["statement"] + " " + comparison["result"]["direction_note"]), small))
        story.append(table([["Alternative", "Metric", "Baseline", "Alternative", "Outcome"]] + [[names.get(r["alternative_id"], ""), r["label"], str(r["baseline"].get("value", r["baseline"].get("state"))), str(r["alternative"].get("value", r["alternative"].get("state"))), r["outcome"]] for r in comparison["result"]["rows"]],
                           [3 * cm, 6 * cm, 2.6 * cm, 2.6 * cm, 3 * cm]))
        for aid, c in comparison["result"]["costs"].items():
            story.append(Paragraph(f"Indicative cost — {_e(names.get(aid, aid))}: status {_e(c['status'])}; totals {_e(c['totals_by_currency'] or 'none')}. {_e(c['label'])}", small))
    story.append(Paragraph("Limitations and assumptions", ss["Heading2"]))
    for lim in sorted({lim for st in ms.values() for lim in (st.get("limitations") or [])}):
        story.append(Paragraph(f"• {_e(lim)}", small))
    story.append(Paragraph("Reproducibility", ss["Heading2"]))
    story.append(Paragraph(_e(json.dumps({"run": run["id"], "analysis_version": run["analysis_version"], "configuration": run["configuration"], "datasets": run.get("input_dataset_ids")})), small))
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
        z.writestr("findings.geojson", json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": f.get("geometry"), "properties": {k: f[k] for k in ("id", "title", "severity", "classification", "rule_id", "summary")}} for f in bundle["findings"]]}))
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
