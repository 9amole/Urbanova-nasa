"""Generate the MOCK case study "NC-01" in case_studies/new_cairo_nc01/.

Everything here is FICTITIOUS and exists to exercise the full Phase 1 workflow on a realistic
Egyptian setting:
* The location is a real vacant desert parcel in New Cairo (5th Settlement), chosen only because it is
  empty in OpenStreetMap and recent imagery, with existing housing to the north and west. There is no
  real project, owner, permit or authority behind this case study.
* The design, the permit letter and the unit rates are invented. Rates use the ISO 4217 test currency XTS.

Outputs:
* nc01_site_boundary.geojson          site outline, WGS 84
* nc01_baseline_plan.geojson          baseline design with feature_type, heights and floors (WGS 84)
* nc01_baseline_plan.dxf              the same design as an AutoCAD 2018 DXF: UTM zone 36N (EPSG:32636),
                                       metres, closed polylines on named layers, building heights as
                                       polyline thickness, tree canopies as circles, labels on ANNOTATION
* nc01_building_heights.csv           heights/floors per building (DXF heights are not read by Phase 1)
* nc01_alternative_additions.geojson  features to add to the alternative
* nc01_alternative_full.geojson/.dxf  the complete alternative design (for 3D work, e.g. Manus)
* nc01_planning_conditions.pdf        fictitious permit-conditions letter
* nc01_unit_rates_TEST_ONLY.csv       test rates in XTS (NOT real prices)

Run: .venv/Scripts/python scripts/make_case_study_new_cairo.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import ezdxf
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "case_studies" / "new_cairo_nc01"
CRS = "EPSG:32636"
fwd = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
inv = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)

# Vacant parcel east of a main road in New Cairo (5th Settlement); site is 320 m x 300 m (9.6 ha).
ANCHOR = (31.48343, 29.99682)  # lon, lat of an empty point found in OSM, checked against imagery
SITE_W, SITE_H = 320.0, 300.0
ax, ay = fwd.transform(*ANCHOR)
OX, OY = ax - 90 - SITE_W / 2, ay - SITE_H / 2  # site south-west corner in UTM; west edge ~20 m from the road

FLOOR_H = 3.5  # metres per floor
PARAPET_H = 1.0
COLS = [(26, 62), (86, 122), (146, 182), (206, 242), (266, 302)]


def utm(x, y):
    return OX + x, OY + y


def ll(x, y):
    return [round(v, 7) for v in inv.transform(*utm(x, y))]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def item(kind, pts, name, layer, **props):
    return {"kind": kind, "pts": pts, "name": name, "layer": layer, "props": props}


def building(x0, y0, x1, y1, name, floors, layer="BUILDING_RESIDENTIAL"):
    return item("poly", rect(x0, y0, x1, y1), name, layer, feature_type="building", floors=floors, height_m=round(floors * FLOOR_H + PARAPET_H, 1))


def area(x0, y0, x1, y1, ftype, name, layer):
    return item("poly", rect(x0, y0, x1, y1), name, layer, feature_type=ftype)


def tree(x, y, name, height=6.0, r=3.0):
    return item("tree", (x, y, r), name, "TREE_CANOPY", feature_type="tree", height_m=height, canopy_radius_m=r)


def baseline() -> list[dict]:
    f = []
    # Access: gate from the external road, ring road inside the site, central east-west spine.
    f.append(area(0, 140, 14, 160, "road", "Main gate and entrance road", "ROAD"))
    f += [area(14, 6, 306, 14, "road", "Ring road south", "ROAD"), area(14, 286, 306, 294, "road", "Ring road north", "ROAD"),
          area(6, 6, 14, 140, "road", "Ring road west (south part)", "ROAD"), area(6, 160, 14, 294, "road", "Ring road west (north part)", "ROAD"),
          area(306, 6, 314, 294, "road", "Ring road east", "ROAD"), area(14, 145, 306, 155, "road", "Central spine road", "ROAD")]
    f += [area(14, 130, 306, 145, "open_space", "Spine sidewalk south (paved)", "OPEN_SPACE_PAVED"),
          area(14, 155, 306, 170, "open_space", "Spine sidewalk north (paved)", "OPEN_SPACE_PAVED")]
    # North: two rows of five apartment blocks (G+3) with a garden court between them.
    for row, (y0, y1) in (("N1", (170, 192)), ("N2", (240, 262))):
        for i, (x0, x1) in enumerate(COLS, 1):
            if row == "N2" and i == 3:
                f.append(building(x0, y0, x1, y1, "Block N2-3 (landmark, G+4)", 5))  # deliberately breaks the height rule
            else:
                f.append(building(x0, y0, x1, y1, f"Block {row}-{i}", 4))
    f.append(area(14, 205, 306, 228, "green_space", "North garden court", "GREEN_LANDSCAPE"))
    f += [area(14, 192, 306, 205, "open_space", "North walkway 1 (paved)", "OPEN_SPACE_PAVED"),
          area(14, 228, 306, 240, "open_space", "North walkway 2 (paved)", "OPEN_SPACE_PAVED"),
          area(14, 262, 306, 286, "parking", "North resident parking", "PARKING")]
    # South: four blocks, central plaza, pool, clubhouse, pocket park, large surface car park.
    for i in (1, 2, 4, 5):
        x0, x1 = COLS[i - 1]
        f.append(building(x0, 108, x1, 130, f"Block S1-{i}", 4))
    f += [building(26, 24, 62, 56, "Block S2-1", 4), building(86, 24, 122, 56, "Block S2-2", 4),
          building(146, 22, 186, 58, "Clubhouse", 2, layer="BUILDING_CLUBHOUSE")]
    f += [area(134, 92, 194, 130, "open_space", "Central plaza (paved)", "OPEN_SPACE_PAVED"),
          area(140, 66, 188, 86, "water", "Swimming pool", "WATER_POOL"),
          area(14, 66, 122, 100, "green_space", "Pocket park", "GREEN_LANDSCAPE"),
          area(206, 14, 306, 92, "parking", "Surface car park (south-east)", "PARKING")]
    f += [tree(30 + 30 * i, 162.5, f"Street tree {i + 1}") for i in range(10)]
    # Everything not drawn above inside the site is bare, unlandscaped ground (setbacks, gaps between blocks).
    f += bare_ground(f)
    return f


def bare_ground(feats: list[dict]) -> list[dict]:
    """Cover the remaining site area with axis-aligned rectangles of bare ground so nothing is left unassigned."""
    rects = [(p["pts"][0][0], p["pts"][0][1], p["pts"][2][0], p["pts"][2][1]) for p in feats if p["kind"] == "poly"]
    xs = sorted({0.0, SITE_W, *[v for r in rects for v in (r[0], r[2])]})
    ys = sorted({0.0, SITE_H, *[v for r in rects for v in (r[1], r[3])]})
    covered = lambda cx, cy: any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)  # noqa: E731
    runs = []  # horizontal runs of free cells per y band
    for j in range(len(ys) - 1):
        i = 0
        while i < len(xs) - 1:
            if covered((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2):
                i += 1
                continue
            k = i
            while k < len(xs) - 1 and not covered((xs[k] + xs[k + 1]) / 2, (ys[j] + ys[j + 1]) / 2):
                k += 1
            runs.append([xs[i], ys[j], xs[k], ys[j + 1]])
            i = k
    merged = []  # merge vertically adjacent runs with the same x-span
    for r in runs:
        m = next((m for m in merged if m[0] == r[0] and m[2] == r[2] and m[3] == r[1]), None)
        if m:
            m[3] = r[3]
        else:
            merged.append(r)
    return [area(*r, "open_space", f"Bare ground {n + 1} (unlandscaped)", "OPEN_SPACE_BARE") for n, r in enumerate(merged)]


def alternative_additions() -> list[dict]:
    a = [area(206, 14, 306, 62, "green_space", "Car park converted to garden", "GREEN_LANDSCAPE"),
         area(206, 62, 306, 92, "parking", "Reduced car park", "PARKING")]
    a += [tree(30 + 26 * i, 198.5, f"Walkway tree A{i + 1}", 7.0, 4.0) for i in range(11)]
    a += [tree(30 + 26 * i, 234.0, f"Walkway tree B{i + 1}", 7.0, 4.0) for i in range(11)]
    a += [tree(x, y, f"Plaza tree {n + 1}", 7.0, 4.0) for n, (x, y) in enumerate([(142, 100), (164, 100), (186, 100), (142, 124), (164, 124), (186, 124)])]
    a += [item("poly", rect(138, 108, 162, 116), "Plaza shade canopy west", "SHADE_STRUCTURE", feature_type="shade_structure", height_m=4.0),
          item("poly", rect(166, 108, 190, 116), "Plaza shade canopy east", "SHADE_STRUCTURE", feature_type="shade_structure", height_m=4.0)]
    return a


REMOVED_IN_ALTERNATIVE = ["Surface car park (south-east)"]
HEIGHT_EDITS_IN_ALTERNATIVE = {"Block N2-3 (landmark, G+4)": {"floors": 4, "height_m": 15.0}}


def alternative_full(base: list[dict]) -> list[dict]:
    out = []
    for f in base:
        if f["name"] in REMOVED_IN_ALTERNATIVE:
            continue
        g = json.loads(json.dumps(f))
        if g["name"] in HEIGHT_EDITS_IN_ALTERNATIVE:
            g["props"].update(HEIGHT_EDITS_IN_ALTERNATIVE[g["name"]])
        out.append(g)
    return out + alternative_additions()


def geojson(feats: list[dict], title: str) -> dict:
    out = []
    for f in feats:
        if f["kind"] == "tree":
            x, y, _ = f["pts"]
            geom = {"type": "Point", "coordinates": ll(x, y)}
        else:
            ring = [ll(x, y) for x, y in f["pts"]]
            geom = {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
        out.append({"type": "Feature", "geometry": geom, "properties": {"name": f["name"], **f["props"]}})
    return {"type": "FeatureCollection", "name": title, "features": out}


LAYER_COLOURS = {"SITE_BOUNDARY": 1, "BUILDING_RESIDENTIAL": 5, "BUILDING_CLUBHOUSE": 6, "ROAD": 8, "PARKING": 9, "OPEN_SPACE_PAVED": 31,
                 "OPEN_SPACE_BARE": 42, "GREEN_LANDSCAPE": 3, "WATER_POOL": 4, "TREE_CANOPY": 94, "SHADE_STRUCTURE": 30, "ANNOTATION": 7}


def dxf(feats: list[dict], path: Path, title: str) -> None:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # metres
    for name, colour in LAYER_COLOURS.items():
        doc.layers.add(name, color=colour)
    msp = doc.modelspace()
    msp.add_lwpolyline([utm(x, y) for x, y in rect(0, 0, SITE_W, SITE_H)], close=True, dxfattribs={"layer": "SITE_BOUNDARY"})
    for f in feats:
        if f["kind"] == "tree":
            x, y, r = f["pts"]
            msp.add_circle(utm(x, y), r, dxfattribs={"layer": "TREE_CANOPY"})
            continue
        attrs = {"layer": f["layer"]}
        h = f["props"].get("height_m")
        if h:
            attrs["thickness"] = h  # shows the block extruded to its height in AutoCAD's 3D views
        msp.add_lwpolyline([utm(x, y) for x, y in f["pts"]], close=True, dxfattribs=attrs)
        if f["props"].get("feature_type") in ("building", "shade_structure"):
            cx = sum(p[0] for p in f["pts"]) / 4
            cy = sum(p[1] for p in f["pts"]) / 4
            label = f"{f['name']} | {h} m" + (f" | {f['props']['floors']} floors" if f["props"].get("floors") else "")
            msp.add_text(label, height=1.6, dxfattribs={"layer": "ANNOTATION"}).set_placement(utm(cx - 14, cy))
    msp.add_text(title, height=4, dxfattribs={"layer": "ANNOTATION"}).set_placement(utm(0, SITE_H + 12))
    msp.add_text("MOCK / FICTITIOUS - coordinates UTM zone 36N (EPSG:32636), units metres", height=2.5,
                 dxfattribs={"layer": "ANNOTATION"}).set_placement(utm(0, SITE_H + 6))
    doc.saveas(path)


def make_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    p = lambda t: Paragraph(t, ss["BodyText"])  # noqa: E731
    story = [
        Paragraph("MOCK CASE STUDY NC-01 — Building Permit Conditions (fictitious, not an official document)", ss["Title"]),
        p("Reference: MOCK/NC-01/2026. Issued: 2026-10-20. Issuing body: Example New City Development Authority (fictitious). "
          "Project: residential compound on a vacant parcel, New Cairo (5th Settlement). Applicant: Example Developer (fictitious)."),
        Spacer(1, 10),
        p("1. The plot area is 96,000 m2 as shown on the approved site layout."),
        p("2. Permitted land uses: residential apartment buildings with a clubhouse and community services."),
        p("3. Building coverage shall not exceed 25% of the plot area."),
        p("4. Maximum building height: 16 m above finished ground level."),
        p("5. Residential buildings shall not exceed G+3."),
        p("6. A minimum setback of 6 m shall be maintained from all plot boundaries."),
        p("7. Green space shall be at least 15% of the plot area, excluding paved plazas and walkways."),
        p("8. One parking space per residential unit shall be provided within the plot."),
        Spacer(1, 10),
        p("These conditions are invented for a software demonstration. They do not reflect any real regulation, authority or decision."),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, title="MOCK NC-01 permit conditions").build(story)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = baseline()
    alt = alternative_full(base)
    boundary = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "MOCK NC-01 site, New Cairo (9.6 ha)"},
                "geometry": {"type": "Polygon", "coordinates": [[ll(x, y) for x, y in rect(0, 0, SITE_W, SITE_H)] + [ll(0, 0)]]}}]}
    (OUT / "nc01_site_boundary.geojson").write_text(json.dumps(boundary, indent=1), encoding="utf-8")
    (OUT / "nc01_baseline_plan.geojson").write_text(json.dumps(geojson(base, "MOCK NC-01 baseline design"), indent=1), encoding="utf-8")
    (OUT / "nc01_alternative_additions.geojson").write_text(json.dumps(geojson(alternative_additions(), "MOCK NC-01 alternative additions"), indent=1), encoding="utf-8")
    (OUT / "nc01_alternative_full.geojson").write_text(json.dumps(geojson(alt, "MOCK NC-01 alternative design (complete)"), indent=1), encoding="utf-8")
    dxf(base, OUT / "nc01_baseline_plan.dxf", "MOCK NC-01 BASELINE SITE LAYOUT")
    dxf(alt, OUT / "nc01_alternative_full.dxf", "MOCK NC-01 ALTERNATIVE SITE LAYOUT")
    with open(OUT / "nc01_building_heights.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "layer", "floors", "height_m", "footprint_m2"])
        for f in base:
            if f["props"].get("feature_type") == "building":
                (x0, y0), (x1, y1) = f["pts"][0], f["pts"][2]
                w.writerow([f["name"], f["layer"], f["props"]["floors"], f["props"]["height_m"], round((x1 - x0) * (y1 - y0))])
    (OUT / "nc01_unit_rates_TEST_ONLY.csv").write_text(
        "quantity_type,description,unit,rate,currency,price_date,source,region\n"
        "green_space_added_m2,TEST soft landscaping with irrigation,m2,100,XTS,2026-10-01,TEST VALUES - not real prices,test\n"
        "trees_added_count,TEST semi-mature tree supply and plant,each,1000,XTS,2026-10-01,TEST VALUES - not real prices,test\n"
        "shade_structure_added_m2,TEST tensile shade canopy,m2,500,XTS,2026-10-01,TEST VALUES - not real prices,test\n"
        "hard_surface_removed_m2,TEST asphalt removal and disposal,m2,20,XTS,2026-10-01,TEST VALUES - not real prices,test\n", encoding="utf-8")
    make_pdf(OUT / "nc01_planning_conditions.pdf")
    summary(base, alt)
    print("Case study written to", OUT)


def summary(base: list[dict], alt: list[dict]) -> None:
    def tot(feats, ftype):
        s = 0.0
        for f in feats:
            if f["props"].get("feature_type") != ftype:
                continue
            if f["kind"] == "tree":
                s += math.pi * f["pts"][2] ** 2
            else:
                (x0, y0), (x1, y1) = f["pts"][0], f["pts"][2]
                s += (x1 - x0) * (y1 - y0)
        return s
    site = SITE_W * SITE_H
    for label, feats in (("baseline", base), ("alternative", alt)):
        allarea = sum(tot(feats, t) for t in ("building", "road", "parking", "open_space", "green_space", "water"))
        print(f"{label:12s} features={len(feats):3d} coverage={tot(feats, 'building') / site * 100:5.1f}% green={tot(feats, 'green_space') / site * 100:5.1f}% "
              f"(+tree canopy {tot(feats, 'tree'):.0f} m2) parking={tot(feats, 'parking'):.0f} m2 assigned={allarea / site * 100:.1f}%")


if __name__ == "__main__":
    sys.exit(main())
