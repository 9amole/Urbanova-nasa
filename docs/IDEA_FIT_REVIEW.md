# Idea-fit review: does URBANOVA do its job for a planner?

**Date:** 2026-11-07. **Build:** commit 52be22e (Phase 1, before the smart upgrade).

**Method:**

* the sample project was seeded with live data and every screen walked as a planner would use it, on a local server;
* the generated report was read;
* the result was compared with the Phase 1 Functional Requirements (FR) and the supervisors' nine points;
* the satellite constants were spot-checked against official sources.

**The idea, in one sentence:** help a planner or developer see a site's heat and environmental problems early, and choose a better design based on evidence.

## 1. Verdict

**The foundations are strong. The decision support is weak.**

URBANOVA measures the right things and measures them honestly. But it leaves the planner to work out the conclusions:

* where the problems are on the drawing;
* what to do about them;
* what each option costs;
* which design to choose.

That is the part the supervisors asked for. It is also what the approved smart upgrade (PHASE2_SPEC.md) is designed to add. A few important gaps are not in that spec yet (§4).

## 2. Strengths (keep these)

| Strength | Evidence |
|---|---|
| Honest evidence: missing data is never shown as zero; every value has a source, date and kind | Value states and provenance on every metric; results page and report |
| Measurements checked against reality | ERA5 vs two weather stations; satellite plausibility 3/3; areas match an independent calculation on 134 real buildings (VALIDATION.md) |
| Correct satellite processing | Landsat C2 L2 surface temperature: DN × 0.00341802 + 149.0 K, confirmed by USGS. Sentinel-2 offset −1000 applied from the scene's processing baseline (≥ 04.00), as ESA prescribes |
| Like-for-like comparison | Every design in a run uses identical site data (one fetch per source, checked live) |
| Planning rules are traceable | Exact wording and page, verified by a person before use, English and Arabic documents |
| Safe and integration-ready | Engine with a versioned JSON contract, 162 tests, installable package |

## 3. Cons found in the walkthrough

Ordered by how much they hurt the idea.

| # | Con | What a planner experiences | Evidence |
|---|---|---|---|
| C1 | **Problems are not located on the drawing** | Findings apply to "the site boundary" or a region, not to "the car park on the east side" | FR-22 requires "geographic location or affected area"; 3 of 4 sample findings use the whole site or a regional cell |
| C2 | **Recommendations are generic templates** | "Add shade structures… along main pedestrian routes", with no location, no effect, no cost, no confidence | Report section 8; FR-23 requires "the affected area or design feature" and an evaluation method; supervisors' "Recommendations Logic" |
| C3 | **One recommendation is linked to the wrong kind of problem** | A *regional* climate finding produces a *site* shade recommendation | Report section 8: "Problem: Frequent very hot days in the regional climate record → Introduce shade…" |
| C4 | **No conclusion or summary** | Results page: about 2,050 words and 69 table rows with no "site condition" summary. Report: about 5,000 words, mostly tables | Supervisors' "Engineering Insights" and "Reports" points; FR-31 dashboard |
| C5 | **The questions show a badge, not an answer** | "What are the site's main environmental concerns? → answered with available evidence", but the answer itself is never shown | Results page "Your questions" |
| C6 | **Designing an alternative is hard** | No drawing tools on the map; alternatives are made by editing table rows or importing a GeoJSON file | Scenarios tab; FR-25 "manually edited design parameters" |
| C7 | **No cost for most options** | Without the user's own rates every item says "price required"; there is no budget input | FR-28, FR-29 ("user-specified budget") |
| C8 | **Surface temperature is coarse for small sites** | A 4 ha site covers about 4 native 100 m Landsat thermal pixels, so site-level LST mixes in the surroundings | LST module status "partial" with a mixed-pixel warning on the sample site |
| C9 | **The comparison doesn't say which design to pick** | Improvements and regressions are listed, but there is no trade-off between cost and performance, and no recommended choice | Supervisors' "Scenario Comparison" point |
| C10 | **The whole run is repeated for every change** | Editing one alternative re-runs every module, including unchanged site data | FR-25 step 5: "recalculate only the affected indicators" |
| C11 | Small usability issues | The project page is blank for about 2 s with no loading indicator; Results opens on the latest run (the alternative), not the baseline | Walkthrough |

## 4. Does the approved smart upgrade fix these?

| Con | Covered by PHASE2_SPEC.md? | Gap to add (input for step 2) |
|---|---|---|
| C1 location | **Yes**: hotspot detection H1–H4 (§4) | — |
| C2 generic recommendations | **Yes**: intervention catalogue, measured effects, optimiser (§6) | Add "building orientation or spacing" (named in FR-23) to the catalogue, or record why not |
| C3 wrong linking | **Partly**: recommendations require a matching hotspot (§6.2) | State explicitly that regional findings never produce site interventions |
| C4 no summary | **Yes** for the report (executive summary, §8) | Add a one-screen **site dashboard** in the app (FR-31), not only in the report |
| C5 badge without answer | **Partly**: AI insights (§5) | Show the answer to each selected question, with evidence links |
| C6 hard to design alternatives | **No** | Add simple **map editing**: draw, move or delete features, plus "apply recommendation" from the optimiser |
| C7 costs | **Yes**: cost library and budget (§6.4, §6.6) | Quotations still need collecting (team) |
| C8 coarse LST | **No** | Consider **ML downscaling of surface temperature** to 10–30 m using Sentinel-2 bands. This is an established remote-sensing method, and a meaningful place for ML. To evaluate in step 2 |
| C9 which design | **Yes**: trade-offs, priorities, robustness (§7) | — |
| C10 re-running everything | **No** | Re-run only the modules a change affects (the dependency map already exists) |
| C11 usability | **No** | Loading indicator; Results opens on the baseline |

## 5. Things to add (summary)

1. **Already in the approved spec:** hotspots on the drawing; tested and priced recommendations; AI insights; a decision-focused report; trade-offs and a recommended design.
2. **To add to the spec in step 2:**
   * map editing of alternatives (C6);
   * an in-app site dashboard (C4);
   * answers to the selected questions (C5);
   * partial re-runs (C10);
   * an orientation/spacing intervention (C2);
   * the small usability fixes (C11).
3. **To evaluate in step 2 as the main ML candidate:** surface-temperature downscaling (C8). It addresses a real weakness of the current analysis, rather than adding AI for appearance.

Sources for the satellite checks:

* USGS, Landsat Collection 2 Surface Temperature: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature
* USGS FAQ, using scale factors: https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products
* ESA, Sentinel-2 Level-2A algorithms and products: https://sentinel.esa.int/web/sentinel/technical-guides/sentinel-2-msi/level-2a-algorithms-products
* Microsoft Planetary Computer, Sentinel-2 baseline change: https://nbviewer.org/github/microsoft/PlanetaryComputerExamples/blob/main/datasets/sentinel-2-l2a/baseline-change.ipynb
