# Decisions, assumptions, document conflicts and open questions

## 0. Decision log — 4 Nov 2026

The project assigned the data science lead as decision maker for Phase 1. These decisions were taken in the development session on 2026-11-04. They settle remediation Phase C and set the scope of Phase D. A decision can be reopened by recording a new entry here.

| # | Decision | Owner from here on |
|---|---|---|
| C1 | **Data sources and licences.** Phase 1 runs in *non-commercial evaluation*. The terms of Open-Meteo (ERA5), NASA POWER, OSM Overpass and the Planetary Computer route (Landsat C2 L2, Sentinel-2 L2A) are confirmed for that scope and recorded in `urbanova/config/providers.json` (`confirmation`). **Not decided for commercial use:** Open-Meteo needs a subscription key (without one, the commercial context falls back to NASA POWER automatically); the Planetary Computer commercial terms must be reviewed (`commercial_use: unverified`, refused in the commercial context); Overpass needs an own instance or regional extract for production load. | Data science lead (sources); product/backend before any commercial launch |
| C2 | **Thresholds stay provisional.** No scientific review has happened, so every band marked `provisional_not_approved` keeps its label and the moderate severity cap. | Data science lead: review before any client-facing release |
| C3 | **Scope of the data-science deliverable.** The analysis side (Modules 3–9: inputs, data acquisition, analyses, findings, comparison, explanation) is handed over as the `urbanova.engine` component with a versioned JSON contract. Authentication, projects, users, storage, jobs, UI, reports in the product UI, notifications, billing, organisations and administration (Modules 1, 2, 10–14) belong to the backend platform. Module 3 allowlist: GeoJSON, and DXF with an EPSG code in metres; PDF and image plans are reference-only. Module 4: satellite data is in Phase 1. | Backend team (platform); data science lead (engine) |
| C4 | **Language.** All output is English. Planning documents in Arabic are parsed from their text layer, including Arabic-Indic digits; extracted values and notes are in English, and the original wording is kept verbatim as evidence. OCR of scanned pages is not in Phase 1; scanned pages are reported as unread. | Data science lead |
| C5 | ~~**Explanations.** Deterministic, record-linked templates only. No generative AI in Phase 1.~~ **Replaced on 2026-11-04** by the approved smart-upgrade spec ([PHASE2_SPEC.md](PHASE2_SPEC.md), decision S-1): AI is part of Phase 1. It runs as a server-side, free, provider-neutral service (Groq → OpenRouter → deterministic fallback; Ollama for development). It only explains evidence computed by the engine, and an automatic check verifies every claim. | Data science lead |
| C6 | **Production stack.** Chosen by the backend team. The prototype web app (FastAPI, SQLite, local accounts, Leaflet UI) is a reference demo, not the integration target. | Backend team |
| D1 | Arabic text-layer extraction (per C4). | Data science lead (done in this build) |
| D2 | Real-input validation. Calculations are checked against known-answer geometry, and the engine is exercised on real OpenStreetMap building footprints. **Still open:** a real project (real plan or DXF, planning document, quoted rates). | Data science lead, when real files exist |
| D3 | Scientific check of the climate baseline against a weather station. **NOAA GHCN-Daily is approved for validation only**; it is never used to produce product results. Satellite layers get a plausibility check against known land covers. | Data science lead |
| D4 | Resilience inside the engine: site data is fetched once per request and shared by every design, so comparisons use identical data; per-provider timeouts. Job retries, queues and scaling belong to the backend. | Data science lead (engine); backend (jobs) |
| D5 | Security, accounts, rate limiting and secrets management. | Backend team |
| D6 | Accessibility and browser support of the product UI. The engine returns legends with text labels so colour is never the only cue. | Backend/frontend team |
| D7 | Commercial licensing (see C1) and the production basemap provider. | Product/backend team |
| S | **Smart upgrade of Phase 1 approved** (2026-11-04): hotspot detection, design optimiser, AI engineering insights, chat assistant, reference cost library, new free data sources; stages S1–S7 as in [PHASE2_SPEC.md](PHASE2_SPEC.md) §11. **Revision 2 approved 2026-11-07** after the step 2 review: existing vs design kept apart, H2 as site context at 100 m, UTCI heat-stress severity, vector-overlay H1, no-winner comparison without weights, ML LST downscaling, map editing and dashboard; stages now S1–S8 (spec §11). Not started. | Data science lead |

## 1. Rebuild instead of extending the earlier prototype

The earlier project (`../urbanova`, single commit `e1570e1`, unchanged) was inspected before any work began. It is a command-line pipeline for one Egyptian site. It was not extended because its foundation contradicts the Functional Requirements (FR):

| FR principle | Earlier prototype |
|---|---|
| Not tied to one city/provider/plan (§1) | Egypt hard-coded in 24 modules (UTM 35/36N only, EGP prices, "Egyptian residential" default height) |
| Never invent prices (FR-28, D) | Hard-coded "HBRC 2026" EGP unit prices |
| No invented thresholds, severity not arbitrary (FR-22, FR-44) | Fixed AHP weights and 0.45/0.70 risk thresholds as the core output |
| No silent substitution, unknown ≠ zero (A.5, D.10) | LST proxy from air temperature; default 3.5 m height; `.get(..., 0)` patterns |
| Coordinates-only projects, readiness (FR-01, FR-05) | GeoJSON boundary mandatory, no readiness engine |
| Uploads, document rules, scenarios with immutable baseline, web UI, auth (FR-02/03/25, Modules 1–2, 8) | Absent |

Carried forward as **knowledge** (re-implemented and re-tested, no code copied): Landsat ST scale/offset and QA handling, the fact that Sentinel-2 reflectance needs the processing-baseline offset, bounded retry/backoff, and atomic storage ideas. No data was carried over: the synthetic samples sit at an arbitrary desert point (30.1200 N, 31.8500 E), deliberately not the earlier prototype's pilot-site coordinates.

## 2. Conflicts between the FR and module documents (FR followed)

| # | Conflict | Resolution |
|---|---|---|
| C-1 | Module 3: GeoJSON is the only confirmed MVP format; PDF/DXF "not automatically included". FR-02: support PDF, raster, GIS and DXF "where a reliable parser is implemented". | GeoJSON fully supported; DXF supported with a mandatory EPSG code and metre units; PDF/PNG/JPEG plans are accepted and preserved as reference drawings only — no automatic geometry, no areas (FR-02 limitations). **Supervisor to confirm the allowlist.** |
| C-2 | Module 4 §3.3: satellite heat/vegetation products "must remain planned or unverified until tested and approved". FR P1 lists Sentinel-2 and Landsat integration in Phase 1. | Implemented and tested (mocked tests; one live run before the review). The access route was **approved by the project owner on 2026-11-01** (recorded in `urbanova/config/providers.json`). Supervisor confirmation is still recommended under Module 4 §8. |
| C-3 | Module 5: Heat Analysis may output "a relative heat score or index". FR-27/FR-45 forbid one unexplained score. | No composite heat index. Heat Analysis reports separate indicators plus relative LST classes with stated breaks. |
| C-4 | FR names USGS (Landsat) and Copernicus Data Space (Sentinel). | The adapters read the identical USGS C2 L2 and ESA L2A products from the Microsoft Planetary Computer STAC mirror, which needs no credentials. That route is not named in the FR. The project owner approved it on 2026-11-01, so it is enabled; supervisor confirmation is recommended. CDSE and NASA Earthdata adapters are registered as *not implemented*. |
| C-5 | Modules 11–14 (billing, notifications, organisations, admin) are written as Phase 1 modules; FR §16 priorities do not include them. | Not built (see §4). Module 1 implemented minimally (local accounts, per-project roles). |
| C-6 | Module 6 leaves scenario limits to the team. | Max 5 active alternatives, 1 active baseline; archive instead of delete. |

## 3. Assumptions (smallest safe choice, easy to change)

| ID | Assumption | Where to change |
|---|---|---|
| A-1 | Climate period default = last 5 complete calendar years; user may set 1981+ | `analysis/climate.py`, project settings |
| A-2 | ERA5 (0.25°) via Open-Meteo is primary; NASA POWER is fallback, wind excluded on fallback | `urbanova/config/providers.json`, `providers/weather.py` |
| A-3 | "Hot season" = 3 months with highest mean Tmax in the climate record (hemisphere default if unavailable) | `analysis/surface_heat.py` |
| A-4 | LST: last 3 complete years, scene cloud < 40 %, ≥ 60 % of site pixels clear, ≤ 6 scenes, 1 km surroundings ring | run settings / `orchestrator.DEFAULT_CONFIG` |
| A-5 | NDVI: last 365 days, ≤ 4 clear scenes; "vegetation indicator" = NDVI ≥ 0.2 | `analysis/vegetation.py` |
| A-6 | Without a boundary, satellite "site" statistics use a 100 m radius around the point (always labelled) | `geo.study_area` |
| A-7 | Shade: flat terrain, opaque casters, true solar time, analysis day = 21st of the middle hottest month | `analysis/solar.py` |
| A-8 | Floor-area ratio assumes every floor equals the footprint | `analysis/urban_form.py` |
| A-9 | Plot-area tolerance 5 % before the user must choose the area basis | `urbanova/config/rules.json` |
| A-10 | Comparison preference directions are heat-focused (more green/shade, less hard surface) — disclosed on screen | `comparison.PREFERRED` |
| A-11 | 1 feddan = 4,200.83 m² when converting document areas (disclosed in the rule note) | `documents.AREA_UNITS` |
| A-12 | Upload limit 25 MB; SQLite storage; sessions 12 h | `.env` |
| A-13 | FR-recommended providers (Open-Meteo, NASA POWER, Overpass) are used for **non-commercial evaluation** before the team confirms terms; non-FR access routes are used only once approved (Planetary Computer: approved by the project owner, 2026-11-01) | `urbanova/config/providers.json` (`approval_status`) |
| A-14 | Relative LST classes use mid-rank percentiles so tied values stay in one class | `analysis/surface_heat.py` |
| A-15 | Site smaller than 9 native 100 m thermal pixels → LST module marked "partial" with a mixed-pixel warning | `analysis/surface_heat.py` |
| A-16 | A finding from a provisional (unapproved) threshold is shown at most as "moderate"; the band level is kept and displayed. This is a display safeguard, not an approval or replacement of any band | `findings.PROVISIONAL_SEVERITY_CAP` |
| A-17 | Climate-baseline findings are regional (~25 km ERA5 cell) and carry no site geometry | `findings.REGIONAL_MODULES` |
| A-18 | A provider whose `commercial_use` is `unverified` is refused in the commercial licence context (conservative default until the team reviews the terms) | `providers/base.availability` |
| A-19 | DXF `$INSUNITS` = 0 (undeclared) is treated as unresolved scale: no baseline or areas until the user confirms metres. Declared non-metre units are refused, not converted | `plans.parse_dxf`, `scenarios.create_baseline` |
| A-20 | Plan heights and canopy radii are accepted only as metres (bare number or "m"); other units are reported and ignored | `plans._number` |

## 4. Not in this build (deliberately)

* Modules 10 (beyond recording recommendation reviews/feedback), 11 billing, 12 notifications, 13 organisations, 14 administration UI.
* OCR (no OCR engine installed): scanned pages are reported, not guessed.
* PDF/raster plan digitising and manual georeferencing (C-1).
* Population, air quality, terrain, flood, wind, thermal comfort (FR-21 optional; no approved provider/method).
* Generative-AI explanations: Module 9 is met with deterministic, record-linked explanations; an LLM layer needs a decision on provider, data policy and evaluation.
* Uploaded supplementary rasters are validated and catalogued but not consumed by analyses.
* PostgreSQL/PostGIS, object storage and a separate job queue (SQLite + thread pool used; persistence isolated in `db.py`).

## 5. Decisions needed from the supervisor / engineering team

Remediation Phase C. None of these was decided in the Phase A/B remediation. Who must resolve each:

| # | Decision | Who |
|---|---|---|
| C1 | Data sources and licences: terms of Open-Meteo, NASA POWER, Overpass and Planetary Computer (including Planetary Computer commercial use, now recorded as `unverified`), recorded in `urbanova/config/providers.json` | Supervisor, with the data-source owners |
| C2 | Approve or replace each provisional threshold in `urbanova/config/rules.json` | AI/Analysis team; supervisor sign-off |
| C3 | Module scope: Modules 1 and 10–14 in Phase 1; Module 3 vs FR-02 format allowlist; Module 4 vs FR P1 satellite timing | Supervisor |
| C4 | Whether Arabic text and OCR of scanned planning documents are required in Phase 1 | Supervisor, with the planning-domain users |
| C5 | Explanations: fixed templates, or a generative model (provider, data policy, evaluation) | Supervisor and engineering team |
| C6 | Production stack: PostGIS, object storage, job queue, hosting, SSO | Engineering team |

The numbered items below give the background.


1. **Approve the Phase 1 data sources and access routes** (C-2, C-4):
   * confirm the terms of the FR-recommended Open-Meteo/ERA5, NASA POWER and OSM Overpass;
   * confirm the Planetary Computer route for Landsat and Sentinel-2 (approved by the project owner on 2026-11-01), or replace it with CDSE/USGS adapters with credentials.

   Open-Meteo's free tier is **non-commercial**. Commercial use requires a subscription key, self-hosting, a direct CDS ERA5 adapter, or NASA POWER as the primary source (see §6).
2. **Confirm or replace the provisional thresholds** in `urbanova/config/rules.json` (status `provisional_not_approved`, labelled as such in the UI and reports): TX35/TX40 day markers, LST site-vs-surroundings bands (2 K / 4 K), hottest-decile share bands (20 % / 40 %), NDVI vegetated-share bands, open-space exposure bands. ETCCDI SU/TR are standard.
3. **Confirm the supported upload allowlist** (C-1), and whether DXF with EPSG is acceptable for the MVP.
4. **Decide whether Modules 11–14 belong to this Phase 1 delivery** or a later increment.
5. **Decide on generative AI** for Module 9 (provider, hosting, evaluation) or confirm deterministic explanations for Phase 1.
6. **Production stack**: PostgreSQL/PostGIS, object storage, job queue, hosting and SSO — required before multi-user deployment.
7. **Real inputs**: an actual project boundary, plan (GeoJSON/DXF with EPSG), planning document and quoted unit rates to validate beyond the synthetic samples.

## 6. Open-Meteo licence and commercial use

* **What the free tier allows:** non-commercial use with CC BY 4.0 attribution. This prototype is an internal, non-commercial evaluation, so it runs within those terms. Every climate result carries the attribution, and the climate module adds a limitation stating the free-tier restriction.
* **What it forbids:** URBANOVA is described as a B2B product (Module 6). Using the free endpoint for paying clients, or any commercial delivery, would breach the terms.
* **Safeguard in code:** `URBANOVA_USAGE_CONTEXT=commercial` makes `providers.base.availability()` refuse the free tier, giving failure kind `license_restricted`. The climate module then falls back to NASA POWER, disclosed, with wind excluded. If `OPEN_METEO_API_KEY` is set server-side, the adapter uses `customer-archive-api.open-meteo.com` with the key, which is redacted from logs and kept out of the cache key.
* **Not verified:** the customer endpoint has not been tested against the real service, because no subscription key was available. It was tested with a mocked transport only.
* **Required before commercial use (decision for the supervisor):** buy an Open-Meteo subscription, self-host Open-Meteo, implement a Copernicus CDS ERA5 adapter, or make NASA POWER primary. Also review Overpass usage (use an own instance or a regional extract for production load) and the terms of any satellite route that gets approved.
