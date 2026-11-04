# Mock case study NC-01 — residential compound, New Cairo

> **Everything in this case study is fictitious except the location.** The plot is a real vacant desert parcel in New Cairo (5th Settlement). It was chosen because it is empty in OpenStreetMap and in current satellite imagery, with existing housing to the north and west. There is no real project, owner, permit or authority behind it. The design, the permit letter and the prices are invented, and the prices use the test currency **XTS**.

## 1. The scenario

A developer proposes a gated residential compound of 17 buildings on a **9.6 ha** plot (320 m × 300 m).

* **Site centre:** 29.99681 N, 31.48250 E.
* **Access:** from the main road on the west side.

The baseline design looks typical, but it has three problems for the permit:

1. One landmark block is **G+4, 18.5 m**. The permit allows G+3 and 16 m.
2. Green space is only **11%**. The permit requires at least 15%.
3. A large **surface car park** and wide paved areas leave the open spaces exposed to the sun.

The **alternative** design fixes them:

* lowers the landmark block to G+3 (15 m);
* turns part of the car park into a garden;
* adds 28 trees and 2 shade canopies over the central plaza.

## 2. Files

| File | What it is | Use it in |
|---|---|---|
| `nc01_site_boundary.geojson` | Site outline (WGS 84) | Phase 1, step 1 |
| `nc01_baseline_plan.geojson` | Baseline design with `feature_type`, `height_m`, `floors` (WGS 84) — **recommended plan input** | Phase 1, step 4 |
| `nc01_baseline_plan.dxf` | The same design as an AutoCAD 2018 DXF: UTM 36N (**EPSG 32636**), metres, closed polylines on named layers. Building heights are stored as polyline thickness, so blocks look 3D in AutoCAD. Tree canopies are circles, and labels are on layer `ANNOTATION` | The DXF route (see §5), CAD tools, Manus |
| `nc01_building_heights.csv` | Height and floors for each building, because Phase 1 doesn't read heights from DXF | The DXF route |
| `nc01_alternative_additions.geojson` | Features to add to the alternative: garden, reduced car park, 28 trees, 2 shade canopies | Phase 1, step 7 |
| `nc01_alternative_full.geojson` / `.dxf` | The complete alternative design, for 3D work | Manus / 3D |
| `nc01_planning_conditions.pdf` | Fictitious permit-conditions letter with 8 conditions | Phase 1, step 4 |
| `nc01_unit_rates_TEST_ONLY.csv` | Test rates in XTS. **Not real prices** | Phase 1, step 4 |
| `phase1_results/` | The reports (PDF and HTML) and data exports (ZIP) from the verification run below | Reading the results |

To regenerate the files, run `.venv\Scripts\python scripts\make_case_study_new_cairo.py`.

## 3. Run it in Phase 1 (about 15 minutes)

1. **Create the project.** Use name *MOCK NC-01*, latitude `29.99681`, longitude `31.48250`, and boundary `nc01_site_boundary.geojson`. Tick all the questions. Then **confirm the location**.
2. **Upload on the Inputs tab:**
   * `nc01_baseline_plan.geojson` as *Urban plan*;
   * `nc01_planning_conditions.pdf` as *Planning document*;
   * `nc01_unit_rates_TEST_ONLY.csv` as *Unit-rate table*.
3. **Create the baseline.** Click **Create baseline scenario from these features**.
4. **Planning rules:** **verify** coverage, height, floors (G+3), setback, green space and plot area.
5. **Scenarios:** create the alternative *Greener, shaded, height-compliant*. Then:
   1. **Remove** *Surface car park (south-east)*.
   2. In *Block N2-3 (landmark, G+4)*, set floors to `4` and height to `15`, then click **Apply**.
   3. Add `nc01_alternative_additions.geojson`.
6. **Run analysis:** click **Run all active scenarios** and wait about 1 minute.
7. **Look at the results, compare the two designs, and generate the report.**

## 4. What we got (verification run, 2 Nov 2026, live data)

All 7 modules completed for both scenarios. The report is labelled **Complete**: the site is larger than 9 Landsat pixels, so there's no small-site warning.

**Site conditions (the same for both designs)**

| Indicator | Value | Source |
|---|---|---|
| Hot-season mean daily maximum (2021–2025) | 34.2 °C | ERA5 via Open-Meteo |
| Days ≥ 35 °C per year | 40.8 | ERA5 (provisional threshold) |
| Tropical nights (min > 20 °C) per year | 117 | ERA5 |
| Observed land surface temperature, site median | 50.8 °C | Landsat C2 L2, 6 clear scenes |
| Site vs surroundings | **+2.9 K warmer** (bare desert vs. irrigated suburbs) | Landsat |
| Vegetation (NDVI), site / surroundings | 0.099 / 0.107: no vegetation today | Sentinel-2 |

**Design comparison**

| Indicator | Baseline | Alternative |
|---|---|---|
| Green space | 11.1 % | **17.6 %** |
| Hard surface (roads + parking) | 28.3 % | **23.3 %** |
| Open space in sun at 15:00 | 92.2 % | **89.7 %** |
| Average shade on open space | 1.09 h | **1.36 h** |
| Tallest building | 18.5 m, 5 floors | **15.0 m, 4 floors** |
| Building coverage | 15.5 % | 15.5 % |

**Planning checks**

| Rule | Baseline | Alternative |
|---|---|---|
| Coverage ≤ 25 % | passes | passes |
| Height ≤ 16 m | **fails** | passes |
| Floors ≤ G+3 | **fails** | passes |
| Green space ≥ 15 % | **fails** | passes |
| Setback ≥ 6 m | passes | passes |

**Indicative cost:** the green space added and the paving removed are priced from the **XTS test rates only**.

**Main findings:**

* **Baseline:** three planning failures, plus provisional findings on very hot days, a warm site surface, no vegetation and exposed open spaces.
* **Alternative:** the planning failures are resolved. The provisional heat and exposure findings remain, because the site is still bare desert today. Phase 1 doesn't predict future temperatures from a design.

## 5. The DXF route (what an engineer's CAD file gives you)

Upload `nc01_baseline_plan.dxf` as *Urban plan* with EPSG code **32636**. In our check, all 82 shapes were read, placed correctly and classified from their layer names. The result matches the GeoJSON: 17 buildings, 7 roads, 2 parking areas, 2 green areas, 1 pool, 10 trees and 42 paved or bare areas.

One thing still needs doing by hand on the Inputs tab: **building heights**. Phase 1 doesn't read heights from DXF, so enter them from `nc01_building_heights.csv` before you create the baseline.

*Fixed on 2 Nov 2026:* `PARKING` and `CAR_PARK` layers used to be classified as green space, because "park" was matched before "parking". They're now read as parking.

## 6. Limits of this case study

* The design, permit and prices are invented. The rules are plausible but don't come from any real regulation.
* The satellite and climate data are real, but they describe the plot as it is today (bare desert), not the finished compound.
* Provisional thresholds stay provisional until your supervisor approves them.
