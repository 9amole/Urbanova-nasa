# Brief for Manus — turn mock case study NC-01 into 3D

## Files to upload to Manus

* `nc01_site_boundary.geojson`
* `nc01_baseline_plan.geojson`
* `nc01_alternative_full.geojson`
* `nc01_baseline_plan.dxf` and `nc01_alternative_full.dxf` (optional: the same designs as CAD, with heights as polyline thickness)
* `phase1_results/nc01_baseline_report.pdf` (optional: Phase 1 results to compare against)

All of these files are fictitious demo data; only the location is real. Nothing confidential needs to be uploaded.

## Prompt to paste into Manus

```
You are helping with URBANOVA, an environmental decision-support tool for urban sites.
Attached is a MOCK case study (fictitious design on a real vacant plot in New Cairo, Egypt):
- nc01_site_boundary.geojson: site outline
- nc01_baseline_plan.geojson: baseline design
- nc01_alternative_full.geojson: alternative design (greener, shaded, landmark block lowered)

Data format: WGS 84 (EPSG:4326). Each feature has "name" and "feature_type", one of:
building, road, parking, green_space, open_space, water, tree, shade_structure.
Buildings have "height_m" and "floors". Trees are points with "height_m" and
"canopy_radius_m". Shade structures have "height_m" (a canopy at that height,
about 0.3 m thick). Everything else is flat ground. Do not invent any height or
property that is not in the files; if something is missing, list it.

Task 1 — 3D model
Build 3D models of both designs: extrude buildings to height_m, draw trees as a
trunk plus a sphere of canopy_radius_m at height_m, shade structures as thin
canopies, and colour ground surfaces by feature_type (green, grey road, dark
parking, light paving, blue water, sand for "bare ground"). Export each design
as glTF (.glb), and a CityJSON file if you can.

Task 2 — 3D viewer
Build a single HTML page (CesiumJS or deck.gl, libraries from a CDN with fixed
versions, no build step) that shows the site on a basemap, lets me switch
between baseline and alternative, click a building to see its name/height/floors,
and animate sun shadows for 21 July at latitude 29.9968, longitude 31.4825
(Cairo time, UTC+3). Show the data attribution.

Task 3 — 3D shade check
For both designs, calculate the share of open ground (open_space + green_space)
in shadow at 09:00, 12:00 and 15:00 on 21 July, using a documented sun-position
method. Output a CSV and a short note listing every assumption. For reference,
URBANOVA Phase 1 calculated open space exposed to sun at 15:00 as 92.2 %
(baseline) and 89.7 % (alternative); explain any difference.

Rules: no invented numbers, list all assumptions and libraries with versions,
and give me all files as a ZIP.
```

## When Manus is done

1. Open the viewer page. Check that the buildings stand on the right plot and that switching designs works.
2. Compare Manus's shade numbers with the Phase 1 numbers above. Small differences are normal, because the methods differ. Large differences need an explanation.
3. Save the ZIP outside the project folder and ask Claude to review it and integrate the viewer as a "3D view" tab.
