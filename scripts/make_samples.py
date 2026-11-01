"""Generate the demonstration inputs in samples/.

All files are SYNTHETIC and exist only to demonstrate and test the workflow:
* sample_site_boundary.geojson — a 200 m × 200 m site at an arbitrary desert point (30.1200 N, 31.8500 E), hot arid climate; not a real project
* sample_plan_baseline.geojson — a fictitious layout (buildings with heights, roads, parking, small green area)
* sample_alternative_additions.geojson — trees with canopy sizes and shade canopies to add to an alternative
* sample_planning_conditions.pdf — a fictitious planning-conditions letter, deliberately containing
  conflicting plot areas and an ambiguous coverage denominator
* sample_rates_TEST_ONLY.csv — test rates in ISO 4217 test currency XTS (NOT real prices)

Run: .venv/Scripts/python scripts/make_samples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
CENTER = (31.8500, 30.1200)  # lon, lat — arbitrary desert point east of Cairo; not a project site
CRS = "EPSG:32636"
fwd = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
cx, cy = fwd.transform(*CENTER)


def rect(x0, y0, w, h):
    pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0)]
    return {"type": "Polygon", "coordinates": [[list(map(lambda v: round(v, 7), inv.transform(cx + x, cy + y))) for x, y in pts]]}


def point(x, y):
    return {"type": "Point", "coordinates": [round(v, 7) for v in inv.transform(cx + x, cy + y)]}


def feat(geom, **props):
    return {"type": "Feature", "geometry": geom, "properties": props}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    boundary = rect(-100, -100, 200, 200)
    (OUT / "sample_site_boundary.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [feat(boundary, name="SYNTHETIC sample site")]}, indent=1))
    feats = [
        feat(rect(-85, 40, 50, 35), feature_type="building", height_m=15, floors=5, name="Block A"),
        feat(rect(-20, 40, 50, 35), feature_type="building", height_m=15, floors=5, name="Block B"),
        feat(rect(45, 40, 40, 35), feature_type="building", height_m=18, floors=6, name="Block C"),
        feat(rect(-85, -60, 45, 30), feature_type="building", height_m=12, floors=4, name="Block D"),
        feat(rect(-25, -60, 45, 30), feature_type="building", height_m=12, floors=4, name="Block E"),
        feat(rect(-100, 10, 200, 14), feature_type="road", name="Internal street"),
        feat(rect(-6, -100, 12, 110), feature_type="road", name="Access road"),
        feat(rect(35, -90, 60, 55), feature_type="parking", name="Surface car park"),
        feat(rect(-85, -25, 75, 28), feature_type="open_space", name="Paved plaza"),
        feat(rect(-95, -95, 85, 25), feature_type="green_space", name="Pocket park"),
        feat(rect(30, -20, 60, 25), feature_type="green_space", name="Courtyard garden"),
        feat(point(50, -5), feature_type="tree", height_m=6, canopy_radius_m=3, name="Existing tree"),
    ]
    (OUT / "sample_plan_baseline.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": "SYNTHETIC baseline design — demonstration only", "features": feats}, indent=1))
    adds = [feat(point(-80 + 12 * i, -12), feature_type="tree", height_m=7, canopy_radius_m=4, name=f"New tree {i + 1}") for i in range(6)]
    adds += [feat(rect(-60, -24, 30, 10), feature_type="shade_structure", height_m=3.5, name="Plaza shade canopy")]
    # Intended use: remove the baseline "Surface car park" in the alternative, then import these additions.
    adds += [feat(rect(35, -90, 60, 25), feature_type="green_space", name="Car park converted to garden")]
    adds += [feat(rect(35, -65, 60, 30), feature_type="parking", name="Reduced car park")]
    (OUT / "sample_alternative_additions.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": "SYNTHETIC alternative additions", "features": adds}, indent=1))
    (OUT / "sample_rates_TEST_ONLY.csv").write_text(
        "quantity_type,description,unit,rate,currency,price_date,source,region\n"
        "green_space_added_m2,TEST soft landscaping,m2,100,XTS,2026-09-01,TEST VALUES - not real prices,test\n"
        "trees_added_count,TEST semi-mature tree supply and plant,each,1000,XTS,2026-09-01,TEST VALUES - not real prices,test\n"
        "shade_structure_added_m2,TEST shade canopy,m2,500,XTS,2026-09-01,TEST VALUES - not real prices,test\n", encoding="utf-8")
    make_pdf(OUT / "sample_planning_conditions.pdf")
    print("Samples written to", OUT)


def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    p = lambda t: Paragraph(t, ss["BodyText"])  # noqa: E731
    story = [
        Paragraph("SYNTHETIC SAMPLE — Planning Conditions Letter (demonstration only, not an official document)", ss["Title"]),
        p("Reference: DEMO/2026/001. Issued: 2026-09-15. Issuing authority: Example Planning Department (fictitious)."),
        Spacer(1, 10),
        p("1. The plot area is 40,000 m2 as recorded in the allocation schedule."),
        p("2. Permitted land uses: residential with ground-floor retail."),
        p("3. Building coverage shall not exceed 35% of the net plot area."),
        p("4. Maximum building height: 18 m above finished ground level."),
        p("5. Buildings shall not exceed 6 floors (G+5)."),
        p("6. A minimum setback of 6 m shall be maintained from all plot boundaries."),
        PageBreak(),
        p("7. Green space shall be at least 20% of the plot area."),
        p("8. One parking space per residential unit shall be provided."),
        p("9. For the avoidance of doubt, the total area of the plot is 42,000 m2 including the shared access strip."),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, title="SYNTHETIC planning conditions").build(story)


if __name__ == "__main__":
    sys.exit(main())
