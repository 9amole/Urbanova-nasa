# App walkthrough on the synthetic site — 8 Nov 2026

The UI acceptance check of the smart upgrade (PHASE2_SPEC §10: "recorded walkthrough on the synthetic site; no console
errors"). This is a written record of a walkthrough done in a real browser against the running demo app
(`python -m urbanova.main`, project "Demo — synthetic sample site", real data providers, AI on Groq). It is a step
log, not a screen recording.

| # | Tab / action | What was checked | Result |
|---|---|---|---|
| 1 | Sign in | Local demo account | ok |
| 2 | 1 · Site (dashboard) | Latest baseline run, condition (8 design hotspots: 7 moderate, 1 low; 11,483 m² located), existing-land context (H2 hot cluster; WorldCover land cover), top 3 hotspots with suited option and cost, question answers, data completeness | ok |
| 3 | 2 · Inputs, 3 · Planning rules, 4 · Readiness | Pages render; readiness lists the new land-cover analysis | ok |
| 4 | 5 · Scenarios | Baseline, alternatives and a draft from a recommended option ("Approve draft") | ok |
| 5 | 7 · Results | Map with all layers including land cover (WorldCover, categorical legend) and the 30 m downscaled LST ("ML-downscaled, modelled; leave-one-scene-out RMSE 0.82 °C"); hotspot cards with provisional-threshold labels | ok |
| 6 | 8 · Compare, baseline vs an alternative analysed with other settings | Refused: "analysed with different settings (modules, solar_date, lst_downscale, analysis period) … Re-run all scenarios together" | ok (intended) |
| 7 | 6 · Run analysis → Advanced → "Experimental: downscale …" → "Run all active scenarios" | Three runs queued with per-scenario progress bars | ok |
| 8 | 8 · Compare after the joint run (both alternatives) | "No single best design" without weights; cost vs heat-stress trade-off chart; give-up list (parking, irrigation water "value required"); hotspots per design; indicator differences | ok |
| 8b | 9 · Report → "Generate PDF + HTML report" | New report listed with PDF, HTML and data-export downloads; labelled partial because some analyses were limited | ok |
| 9 | Rapid switching between map tabs (Site ↔ Results ↔ Inputs, < 0.3 s apart) | Browser console | **no errors** after fix 2 |

**Defects found and fixed during the walkthrough**

1. *Dashboard said "0 design hotspots" for a run that did not include hotspot detection.* It now says "Not assessed:
   … Run the analysis with 'Design hotspots' selected" (test: `test_dashboard_says_not_assessed_when_hotspots_did_not_run`).
2. *Console error `Cannot read properties of undefined (reading '_leaflet_pos')`* when leaving a map tab during a zoom
   animation. Leaflet 1.9.4 ends the animation with a timer that `map.remove()` cannot cancel; every map now ignores that
   callback once removed, and the results page stops its layer loading when the user has left the page.
3. *Compare with every alternative unticked* sent the request and relied on the server's 422 notice; the page now says
   "Select at least one alternative …" before sending.
4. *Results page script error on the land-cover layer* (a categorical legend without LST class breaks) — fixed in S7.
