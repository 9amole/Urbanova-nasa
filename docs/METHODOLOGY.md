# Methodology (analysis version `p1-methods-2026.10`)

All vector data is stored in WGS 84. Areas and distances are computed in the project's local UTM zone (`EPSG:326xx/327xx`, chosen from the coordinates). Every metric records a **state** (`available`, `unknown`, `unavailable`, `invalid`, `blocked`, `not_applicable`) and an **output kind** (observed, reanalysis, calculated, classified, modelled, user-provided). Non-available values never carry a number.

## Climate & heat baseline (FR-17)
* Source: Open-Meteo Historical API, `models=era5`, daily Tmax/Tmin/Tmean, RH mean, 10 m max wind, precipitation, shortwave sum, `timezone=auto`. Units are checked against expected units; values outside physical ranges become missing. Fallback: NASA POWER daily (T2M_MAX/MIN, T2M, RH2M, PRECTOTCORR, ALLSKY_SFC_SW_DWN ×3.6 → MJ/m²); fill −999 → missing; wind excluded.
* Monthly climatology = mean of daily values per calendar month; hottest 3 months = highest mean Tmax.
* Indices per complete year (≥ 90 % days present), averaged: SU (Tmax > 25 °C), TR (Tmin > 20 °C) — ETCCDI; TX35, TX40 — provisional.
* P90/P95 = empirical percentiles of daily Tmax over the whole period.

## Surrounding context (FR-15)
Overpass `way` features with `building`, `highway`, green `leisure/landuse/natural` tags and water, clipped to a 500 m buffer around the site. Shares are of the study-area area. Completeness warnings are always shown.

## Urban-form indicators (FR-16)
Features of each class are unioned (no double counting) and clipped to the site boundary. BCR = footprint ÷ area basis × 100 (area basis = boundary, or the verified document plot area if chosen). Green = green-space polygons + tree canopies with a stated radius. Hard surface = road + parking polygons. Open space = designated open space + green. Setback = minimum distance from a building edge to the boundary line. FAR = Σ(footprint × floors) ÷ area basis, only when floors are known for all buildings. Heights are never inferred.

## Land surface temperature (FR-18)
1. STAC search `landsat-c2-l2`, platforms landsat-8/9, scene cloud < 40 %, last 3 complete years; keep scenes in the hot months.
2. Read `qa_pixel` and `lwir11` windows; reproject to a 30 m local-UTM grid (nearest).
3. Mask QA bits 0–4 (fill, dilated cloud, cirrus, cloud, cloud shadow).
4. LST °C = DN × 0.00341802 + 149.0 − 273.15; drop values outside −30…80 °C.
5. Accept a scene if ≥ 60 % of site pixels are valid; composite = per-pixel median of ≤ 6 accepted scenes.
6. Metrics: site median & P90, ring median (site buffered by 60 m excluded, up to 1 km), site − ring difference (with per-scene min/max), share of site pixels ≥ study-area P90.
7. Classes from each pixel's mid-rank percentile within the study area (ties share one rank): < 25 cooler, 25–75 typical, 75–90 warmer, ≥ 90 hottest decile — relative to this study area only. A uniform field is therefore 'typical', never a hotspot.
8. If the site covers fewer than 9 native 100 m thermal pixels, the module is marked partial with a mixed-pixel warning.
9. Where NDVI was also computed, mean NDVI of hottest-decile pixels vs the rest is reported as co-location only (not causation).

**Access:** this module calls the Planetary Computer mirror only after that route is approved in `config/providers.json`; otherwise it reports *blocked*.

## Vegetation (FR-20)
Blocked until the Planetary Computer access route is approved (same as LST).
Sentinel-2 L2A (`sentinel-2-l2a`) B04, B08 (10 m) and SCL (20 m → 10 m nearest), last 365 days, one scene per date, ≤ 4 scenes. Reflectance = (DN − 1000)/10000 for processing baseline ≥ 04.00, else DN/10000. SCL classes 0, 1, 3, 8, 9, 10, 11 masked. NDVI = (NIR − Red)/(NIR + Red), median composite. Vegetated share = NDVI ≥ 0.2. Planned green polygons are compared with observed NDVI where present.

## Solar position and shade (FR-19)
NOAA/Spencer solar declination; elevation and azimuth at true solar time (no time-zone assumption). Building shadow = union of footprint, translated footprint (offset = h / tan(elevation) away from the sun) and the swept quadrilateral of every edge — exact for vertical prisms on flat ground. Shade structures and trees with height + canopy radius cast translated canopies. Hourly 07:00–17:00; shade duration = Σ hourly shaded share × 1 h.

## Planning checks (FR-16, FR-44/45)
Only user-verified rules. Ratio checks (coverage, green, open space, FAR) are *not assessed* while plot areas conflict in the document, or when the verified plot area differs from the boundary area by > 5 % and the user has not chosen the basis. Results are labelled "passes the configured geometric check", never "approved".

## Findings and recommendations (FR-22/23)
Configured in `config/rules.json` with metric, severity bands, status (`standard` / `provisional_not_approved`; provisional findings are labelled "PROVISIONAL THRESHOLD — NOT APPROVED" in the UI and reports), justification and limitation. A verified-rule failure is rated high. Modules that are blocked/unavailable/failed produce data-quality findings. Recommendation templates (`config/recommendations.json`) state only the expected direction of impact.

## Comparison (FR-26/27)
Requires the latest successful run of each scenario, identical comparable settings and analysis version, and no design change since the run. Only design-dependent metrics are compared; units and method text must match. Optional scoring: min–max normalisation across compared scenarios using the user's weights; missing criteria are excluded (never 0); no ranking without weights.

## Costs (FR-28)
Quantities = differences in site-clipped totals between alternative and baseline (green added, hard surface removed, trees added, shade area added, footprint removed). A line is priced only when a user rate with matching unit exists; currencies are never summed together; stale (> 2 years) rates are flagged.
