# Analysis mapping

Generated from `urbanova/config/analyses.json` by `scripts/gen_analyses_doc.py`; edit the JSON, not this file.
**Condition** says whether an analysis describes the existing land (today's site, the same for every design) or the proposed design (PHASE2_SPEC decision S-8).

| Id | Analysis | Condition | What it measures | Required inputs | Method (summary) | Main outputs | Status |
|---|---|---|---|---|---|---|---|
| A1 | Climate baseline | existing | How hot the region is, and how often | Coordinates (optional: period) | ERA5 daily series via Open-Meteo (NASA POWER fallback); ETCCDI indices SU, TR and TX90p against 1961–1990; percentiles | climate_* metrics, hottest months | implemented |
| A2 | Surrounding context | existing | What is mapped around the site | Coordinates (optional: boundary) | OpenStreetMap ways within a buffer | context_* metrics, context layer | implemented |
| A3 | Urban form | design | How the design uses the site: coverage, green, open and hard surface, spacing, setback, floor-area ratio | Boundary, design (heights, floors) | Exact polygon areas in local UTM; unions so overlaps count once | building_coverage_ratio_pct, green_space_pct, ... | implemented |
| A4 | Vegetation | existing | Where vegetation exists now | Coordinates (optional: boundary) | Sentinel-2 L2A NDVI, SCL mask, median composite; NDVI ≥ 0.2 = vegetation | ndvi_* metrics, NDVI layer | implemented |
| A5 | Land surface temperature | existing | Which surfaces are hottest in the hot season today | Coordinates (optional: boundary) | Landsat C2 L2 surface temperature, QA mask, hot-season median; site position among surrounding 90 m blocks | lst_* metrics, LST layers | implemented |
| A6 | Solar and shade | design | Where the sun hits the ground, hour by hour, on a hot-season day | Design with heights (optional: tree canopy) | NOAA solar position; exact hourly shadow polygons 07–17 h | solar_* metrics, shadow layer | implemented |
| A7 | Planning compliance | design | Whether the design meets verified planning rules | Design, verified rules | Geometric checks | Pass/fail per rule | implemented |
| A8 | Hotspot detection | design | Where the problems are on the drawing, how big and how severe | Outputs of A3, A6, A7 (A5 for site context) | Vector overlay of hard surface with hourly shadows; distance to shade; rule geometry; Gi* at 100 m for context | Hotspot polygons with area, severity, metrics, causes | planned |
| A9 | Heat-stress exposure | design | Heat stress a person would feel in sun vs shade on a hot day | ERA5 hot-day conditions; sun/shade from A6 | UTCI via pythermalcomfort | UTCI category in sun and shade per hotspot | planned |
| A10 | Intervention evaluation | design | What each candidate intervention changes, measured | Design, hotspot, catalogue | Apply to a copy of the design; partial re-run; difference | Effect per option | planned |
| A11 | Cost estimation | design | What an option costs, including maintenance | Quantities, quotations or cost library | Quantity × unit-cost range | Cost range with sources, or 'price required' | extended |
| A12 | Scenario comparison | design | Which design performs better, at what cost | Two or more analysed designs | Same-data comparison; Pareto trade-off; ranking under user weights | Comparison, trade-off chart, choice under weights | extended |
