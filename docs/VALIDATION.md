# Validation and plausibility checks — 4 Nov 2026

Remediation items D2 and D3. Every number below comes from a run on 2026-11-04 and from the result files in `validation/`. Re-run with the scripts named in each section; they need internet access.

**What this does and does not show.**

* **Shown:**
  * the climate baseline was compared with real weather stations;
  * the satellite layers behave as expected on land covers with well-known contrasts;
  * area calculations on real, messy building footprints match an independent calculation.
* **Not shown:**
  * accuracy at any specific project site;
  * accuracy of the provisional thresholds;
  * behaviour on a real project's CAD export, planning documents and prices. That D2 item stays open (see the last section).

## 1. ERA5 climate baseline vs weather stations (D3)

`scripts/validate_climate_ghcn.py` → `validation/climate_ghcn_results.json`.

**Method:**

* **ERA5:** fetched through the product's own adapter (Open-Meteo, `models=era5`) at each station's coordinates.
* **Station:** NOAA GHCN-Daily TMAX/TMIN. Values with a quality flag are excluded, never corrected.
* **Statistics:** both series go through the product's own `climate.summarise()`, with the index definitions in `urbanova/config/rules.json`.
* **Approval:** GHCN-Daily is approved for this validation only (decision D3). No product result uses it.

**Station choice:** GHCN-Daily has no recent temperature data for Cairo-area stations, so the two Egyptian stations with the most recent complete years were used:

* **Minya:** Nile valley, inland, about 245 km from the sample site;
* **El Arish:** Mediterranean coast.

| | Minya (EGM00062387) | El Arish Intl (EGM00062337) |
|---|---|---|
| Period | 2015–2019 | 2001–2004 |
| Station data completeness (TMAX / TMIN) | 89 % / 91 % | 94 % / 88 % |
| **Daily Tmax, ERA5 − station** | bias **+0.70 °C**, MAE 0.85, RMSE 1.06, r 0.994 (1,630 days) | bias **−0.69 °C**, MAE 0.92, RMSE 1.19, r 0.987 (1,371 days) |
| **Daily Tmin, ERA5 − station** | bias **+2.17 °C**, MAE 2.18, RMSE 2.40, r 0.989 (1,661 days) | bias **+1.50 °C**, MAE 1.63, RMSE 1.95, r 0.974 (1,282 days) |
| Hot-season mean Tmax (station / ERA5) | 37.22 / 38.09 °C (Jun–Aug) | 32.60 / 32.36 °C (Jul–Sep) |
| P90 daily Tmax (station / ERA5) | 38.51 / 39.30 °C | 33.60 / 33.30 °C |
| SU, summer days (Tmax > 25 °C), per year | 228.7 / 264.0 | 205.2 / 212.2 |
| TR, tropical nights (Tmin > 20 °C), per year | 132.0 / 170.7 | not compared (no year ≥ 90 % complete in both) |
| TX35, very hot days (provisional), per year | 108.7 / 130.0 | 16.5 / 11.5 |
| TX40 (provisional), per year | 17.7 / 26.3 | 2.0 / 1.0 |

Index rows for Minya compare only the years that are at least 90 % complete in both series: 3 of 5 years.

**Conclusions:**

1. **Daily maximum temperature, and the statistics built on it, are close to the stations:** mean bias within ±0.7 °C, correlation 0.99. The hot-season mean Tmax and P90 differ by less than 1 °C.
2. **Daily minimum temperature is warmer in ERA5, by 1.5–2.2 °C at both stations.** As a result, tropical-night counts (TR) can be overstated by tens of nights per year: +39 at Minya.
3. **Threshold counts are sensitive to small biases.** At Minya, a +0.7 °C Tmax bias moves TX35 by +21 days a year and SU by +35. This adds to the reasons the TX35/TX40 bands stay provisional (decision C2).
4. **Action taken:** the climate module now states this result as a limitation on every climate output. No correction factor is applied: two stations are not enough to calibrate one, and doing so would be inventing accuracy.

**Interpretation limits:**

* A station is a point, while ERA5 is a ~25 km cell average. The ERA5 grid point used for Minya (28.00 N, 30.75 E) is about 9 km from the station (28.083 N, 30.733 E), and a station's surroundings are not the cell average.
* Day boundaries differ: Open-Meteo aggregates by local calendar day, while GHCN follows the station's observation day.
* Two stations, from different years, are a check, not a calibration.

## 2. Satellite layers on land covers with known behaviour (D3)

`scripts/plausibility_satellite.py` → `validation/satellite_plausibility.json`. These are site-only engine requests with a 100 m radius around each point, using the default settings and the same code path as the product.

| Site | NDVI site mean (ring) | Hot-season LST median, site (ring) | Landsat scenes |
|---|---|---|---|
| Open water, Lake Qarun (29.47 N, 30.65 E) | −0.329 (−0.316) | 31.5 °C (31.4) | 6 |
| Irrigated farmland, Nile Delta near Tanta (30.765 N, 31.035 E) | 0.448 (0.396) | 37.8 °C (37.6) | 6 |
| Bare desert, synthetic sample point (30.12 N, 31.85 E) | 0.084 (0.078) | 53.1 °C (52.8) | 6 |

| Expected relation | Result |
|---|---|
| NDVI: water < desert < farmland | **pass** |
| NDVI of irrigated farmland above the 0.2 vegetation-indicator threshold | **pass** (0.448) |
| Daytime hot-season LST: water < farmland < desert | **pass** (31.5 < 37.8 < 53.1 °C) |

The LST module reports *partial* at all three points, which is expected. A 100 m radius covers fewer than 9 native 100 m thermal pixels, so the mixed-pixel warning applies (assumption A-15).

**What this check does not cover:** absolute accuracy. Landsat C2 L2 surface temperature has its own published uncertainty, and no ground measurements were compared.

## 3. Real-world geometry through the full engine path (D2, partial)

`scripts/validate_real_geometry.py` → `validation/real_geometry_check.json`.

**Input:** 134 real OpenStreetMap polygons (buildings, parks, gardens) of a 400 m × 400 m area of Garden City, Cairo. They were written to a GeoJSON file **without feature types**, the way a GIS export arrives.

**Path:** the file went through `extract_plan()` (classification, repair, quality checks), then `analyse()` (urban form).

| Step | Result |
|---|---|
| extract_plan | 129 buildings and 4 green spaces classified from their tags; 1 feature (a sports pitch) left *unclassified* and flagged for review rather than guessed; 1 overlapping building pair and 6 buildings crossing the site edge reported; 0.11 s |
| analyse | Completed with limitations, warning that 12,735 m² of footprint outside the site is excluded from the ratios |
| Building coverage: engine (UTM) vs independent (equal-area EPSG:6933) | 35.31 % vs 35.31 % (difference < 0.005 points) |
| Green-space share: engine vs independent | 4.56 % vs 4.56 % |

**Found and fixed during this check:** GIS exports carry OSM-style `leisure`, `natural` and `amenity` attributes, which the classifier did not read. Parks therefore came out *unclassified* (flagged, not guessed). The classifier now reads these attributes too; `test_gis_export_tags_are_classified` covers it.

## 4. Still open (owner: data science lead)

* **D2, a real project:** a real boundary, a real plan or DXF, a real planning document (ideally Arabic, including a scan), and quoted unit rates. Check coverage, green share and setbacks against a hand calculation, and rule extraction against a manual reading. Re-run `scripts/smoke.py` and the engine on that project and record the differences here.
* **D3, more stations:** check more stations and recent years when data is available (for example from the Egyptian Meteorological Authority), before any correction of the Tmin bias is considered.
* **Thresholds:** review the provisional thresholds against the station evidence above (decision C2).
