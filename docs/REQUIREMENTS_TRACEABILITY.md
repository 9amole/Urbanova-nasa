# Requirements traceability — Phase 1

Status: **Complete** = implemented and verified by automated tests and/or the live demo run · **Partial** = implemented with a stated gap · **Blocked** = needs a decision/input · **Not in Phase 1 build** = deliberately deferred (see DECISIONS.md §4).

Remediation of 4 Nov 2026 (Phase A/B): the rows marked *(remediated)* were re-checked against the code. FR-16 and FR-33 had been listed as Complete while containing confirmed errors (parking classified as green space, open/green overlaps counted twice, PDF missing sections); they are Complete again only because those errors are now fixed and covered by `tests/test_remediation.py`.

| Req | Title | Status | Implementation | Evidence / gap |
|---|---|---|---|---|
| FR-01 | Project creation & site identification | Complete | `projects.py`, `geo.py`, Site tab | Coordinate-only creation, range/Null-Island/boundary validity & plausibility checks, location confirmation gate, missing-info list, availability summary. Tests: `test_coordinates_only…`, `test_invalid_coordinates…`, `test_run_requires_location_confirmation`, `test_moving_site_requires_reconfirmation` |
| FR-02 | Urban plan upload & geometry extraction | Partial *(remediated)* | `files.py`, `plans.py`, Inputs tab | GeoJSON (WGS 84 / declared EPSG) and DXF (EPSG required) extraction; DXF LWPOLYLINE/POLYLINE/LINE/CIRCLE plus INSERT (block references, exploded) and HATCH boundaries; every other entity type is listed with counts as skipped. A DXF with undeclared units (`$INSUNITS` = 0) is marked for review and blocks the baseline and area-based indicators until the user confirms the units are metres (FR-02 "never present areas as reliable if scale unresolved"). Heights and canopy radii must be metres; other units are reported and ignored, never converted. Keyword classification puts parking and shade structures before green space and trees. A second plan upload requires an explicit replace/add choice; replaced features are deactivated and kept in history. **Gap:** PDF/PNG/JPEG plans stored as reference only (no digitising/georeferencing); DXF in units other than metres is refused, not scaled. Tests: `test_remediation.py` (A1, A4–A6) and earlier plan/DXF/image tests |
| FR-03 | Planning-document ingestion | Partial *(remediated, D1)* | `documents.py`, `engine.extract_rules`, Planning rules tab | Text-layer extraction in **English and Arabic** (Egyptian planning terms, Arabic-Indic digits, Arabic lines stored in reversed visual order detected and re-ordered, flagged low confidence); candidate rules with exact wording/page/document; decimal commas, split digits, basement floors, advisory wording and conflicts are low confidence with English notes; verification applies manual-entry range checks. **Gaps:** no OCR (scanned pages reported, decision C4); no real Arabic planning document tested yet (D2). Tests: `test_arabic.py`, `test_remediation.py` (A3) |
| FR-04 | Supplementary data | Partial | heights/floors/canopy via attributes or correction; rate CSV; GeoTIFF upload | Units/CRS validated and catalogued. **Gap:** uploaded rasters and local weather CSVs are not consumed by analyses |
| FR-05 | Readiness assessment | Complete | `readiness.py`, Readiness tab | Ready / limited / blocked per analysis, missing inputs, effect, next step |
| FR-06 | Provider registry | Complete | `urbanova/config/providers.json`, `/providers` | All FR-06 fields incl. licence, limits, fallback, implementation status |
| FR-07 | API request execution | Complete | `providers/*` | Variable selection, schema/unit checks, range checks, duplicate and out-of-period/out-of-order timestamps, raw response cached where permitted, provenance, standardised output |
| FR-08 | Orchestration & fallback | Complete | `providers/base.py`, `weather.py` | Timeouts, bounded backoff with Retry-After, caching, compatible fallback with disclosure. Tests: rate-limit, auth-failure, fallback |
| FR-09 | Data-acquisition policy | Complete for non-commercial evaluation *(decision C1)* | registry fields `approval_status`, `confirmation`, licence, `commercial_use` | Terms of every enabled source confirmed by the decision maker on 2026-11-04 for Phase 1 non-commercial evaluation and recorded in `urbanova/config/providers.json`; labels show it. **Not cleared for commercial use** (Open-Meteo subscription; Planetary Computer terms `unverified`, refused in the commercial context; Overpass own instance) |
| FR-10 | Unified data model | Complete | `values.py`, `db.py` (metrics, datasets) | Value, unit, state, kind, method, sources, quality, uncertainty |
| FR-11 | Cleaning & normalisation | Complete | weather/satellite adapters | Unit conversion recorded, missing ≠ 0, scale factors, no-data masks |
| FR-12 | CRS & alignment | Complete | `geo.py`, `analysis/common.py` | Local UTM for areas; reprojection/resampling recorded |
| FR-13 | Data quality & uncertainty | Complete | module warnings, metric quality, findings confidence | Explainable statuses ("only 2 clear scenes…") |
| FR-14 | Data fusion | Complete | design vs site-condition separation | Observed conditions never presented as caused by the design |
| FR-15 | Site & surrounding context | Complete | `analysis/context.py` | OSM layers, completeness warnings |
| FR-16 | Site & urban-form indicators | Complete *(remediated)* | `analysis/urban_form.py` | BCR, green, open, hard surface, spacing, setback, land-use distribution, FAR only with floors. Open space is now the **union** of open-space and green polygons (a tree canopy over a plaza counts once). Tests: `test_open_space_overlap_counted_once`, `test_keyword_classification`, `test_dxf_classifies_from_layer_names_only` |
| FR-17 | Weather & climate baseline | Complete *(validated, D3)* | `analysis/climate.py` | Live-verified (ERA5 2021–2025). Compared with two NOAA GHCN-Daily stations (`docs/VALIDATION.md` §1): daily Tmax bias ±0.7 °C, r 0.99; Tmin 1.5–2.2 °C warm, so TR can be overstated — stated as a limitation on every climate result |
| FR-18 | Heat & LST analysis | Complete | `analysis/surface_heat.py` | Masking, scaling, rank-based classes, small-site warning, NDVI co-location. Mocked tests (cloud, ties, co-location) and live demo (6 clear scenes). Access route approved by the project owner 2026-11-01; supervisor confirmation recommended |
| FR-19 | Solar exposure & shade | Partial | `analysis/solar.py` | Position, prism shadows, shade duration; **gap:** no terrain/off-site obstructions, no irradiance model |
| FR-20 | Vegetation | Complete | `analysis/vegetation.py` | Planned-vs-observed comparison. Mocked test and live demo (Sentinel-2 L2A). Access route approved by the project owner 2026-11-01 |
| FR-21 | Additional indicators | Not in Phase 1 build | architecture supports new modules | No approved provider/method |
| FR-22 | Evidence-based findings | Complete (thresholds provisional) *(remediated)* | `findings.py`, `urbanova/config/rules.json` | Rule-linked severity. A PROVISIONAL threshold never produces "high": its severity is capped at "moderate" (the band it falls in is kept and shown), it is labelled next to the severity, and it ranks below standard or verified-rule findings of the same severity. Each finding states its affected area; regional climate findings are labelled "Regional (~25 km ERA5 grid cell…)" and carry no site geometry. Tests: `test_provisional_threshold_never_produces_high_severity`, `test_regional_climate_finding_is_not_placed_on_the_site` |
| FR-23 | Recommendations | Complete | `urbanova/config/recommendations.json` | Direction-only impact, trade-offs, evaluation method, never alters design |
| FR-24 | AI-assisted explanation | Partial | `explain.py` | Deterministic, record-linked; refuses predictions/unsupported questions. **Gap:** no generative model (decision pending) |
| FR-25 | Design alternatives | Partial | `scenarios.py` | Immutable baseline, change log, validation, archive, cached datasets reused. **Gap:** a re-run recalculates all selected modules rather than only the affected indicators (step 5) |
| FR-26 | Compare alternatives | Complete | `comparison.py` | Same metric/unit/method, same settings and analysis period, refuses stale runs (any input change), shows value kind per row |
| FR-27 | Multi-criteria ranking | Complete *(remediated)* | `comparison._rank` | User weights only; missing excluded, caveats. Each score is shown with its raw value and difference from the baseline; with only two scenarios a warning explains that min–max scores are 0/1 however small the difference. Test: `test_two_scenario_scoring_shows_raw_values_and_warns` |
| FR-28 | Cost estimation | Complete | `cost.py` | Quantities always; prices only from user rates |
| FR-29 | Feasibility & constraints | Partial | planning checks + rule labels | Budget/preference/material checks not implemented |
| FR-30 | Interactive map | Partial | Results tab | Layers, opacity, legends, popups, value-kind labels. **Gap:** planning constraints (e.g. setback zones) are not drawn |
| FR-31 | Metrics dashboard | Complete | Results tab | Reasons shown instead of blanks |
| FR-32 | Progress & error handling | Complete | jobs + Run tab | Stage/progress per run; failed sources shown; re-run |
| FR-33 | Analysis report | Complete *(remediated)* | `report.py` | PDF and HTML share the same 14 numbered sections (`report.SECTIONS`), covering FR-33 items 1–15, including Assumptions and a Methodology section listing the recorded calculation for each metric. Test: `test_pdf_report_contains_every_section_heading` (headings checked in text extracted from the PDF) |
| FR-34 | Structured exports | Complete | `/export` | CSV, JSON, GeoJSON, GeoTIFF |
| FR-35 | Project & version management | Complete | history, originals, runs | Source changes never overwrite runs |
| FR-36 | Reproducible runs | Partial | `orchestrator.py` | Config, fingerprint, dataset IDs, versions, per-module stale detection. **Gap:** stale modules are flagged but not selectively re-run automatically |
| FR-37 | Corrections & feedback | Complete | feature corrections, rule decisions, recommendation reviews, feedback | Feedback never changes results |
| FR-38 | Provider adapters | Complete | `providers/` | Analysis consumes standardised data |
| FR-39 | Dataset catalog | Complete | `catalog.py` | All FR-39 + most A.7 fields |
| FR-40 | Cache & reuse | Complete *(D4)* | `providers/base.py`, `engine/core.py` | Cache keyed by request (secrets excluded), only where `caching_permitted`. **Within one request (engine) or one "Run all" (app), site data is fetched once and every design receives the same outcome, failures included** (`test_site_data_is_fetched_once_and_shared_by_every_design`; live run: one request per source for two designs). Overpass client timeout 75 s > its 60 s server timeout |
| FR-41 | Monitoring & logging | Partial | `provider_logs` | Status, duration, retries, cache hits, auth/rate-limit/timeouts; tokens and keys redacted. **Gap:** data-validation failures are recorded on the dataset, not in the operational log; no admin view |
| FR-42 | Secure credentials | Complete | optional `OPEN_METEO_API_KEY` env var only | Server-side only; redacted from logs; excluded from cache keys; no secrets in the repository |
| FR-43 | Provider configuration | Partial | config file + env | No admin UI (Module 14 deferred) |
| FR-44 | Configurable rules | Complete | `urbanova/config/rules.json` | Justification + status per rule |
| FR-45 | Risk vs non-compliance | Complete | finding classifications | Five classes |
| FR-46 | Explainable findings | Complete | evidence/threshold/limitations + Explain | |
| FR-47 | Partial analysis support | Complete | module statuses | Test: `test_api_failure_does_not_stop_unrelated_modules` |
| FR-48 | Confirm uncertain inputs | Complete | location, review flags, rule verification, area basis | |
| FR-49 | Objective selection | Complete | objectives → modules, answerability | A question counts as answered only when its essential result exists (e.g. LST for surface heat) |
| FR-50 | No automatic design modification | Complete | recommendations never edit scenarios | |
| A.1–A.7 | Data-acquisition strategy | Partial | API, STAC, manual upload, catalog | Bulk download not implemented; uploaded rasters not consumed |
| B.1–B.5 | Backend entities, endpoints, storage, lineage | Complete (SQLite) | `db.py`, `api.py` | PostGIS/object storage deferred |
| B.6 | Security & operations | Complete | content sniffing, size limits, roles, no execution | |
| B.7 | Backend acceptance tests | Complete | `tests/test_acceptance.py` | Earthdata/Copernicus auth tests n/a (not integrated) |
| Integration | Analysis engine for the backend team (decision C3) | Complete | `urbanova/engine/`, `schemas/`, `examples/`, `pyproject.toml`, `docs/HANDOFF.md` | `analyse` / `extract_plan` / `extract_rules`; versioned JSON contract 1.0 (Pydantic models, exported JSON Schema kept current by a test); located input errors; no database/web/accounts (tested: no database file is created); installable wheel tested in a clean environment without the app dependencies. Tests: `test_engine.py` |
| Module 1 | Authentication & permissions | Partial | `security.py` | Local accounts, scrypt, sessions, owner/editor/viewer. No orgs/SSO/MFA/reset |
| Module 2 | Project management | Complete | | |
| Modules 3–9 | | see FR rows above | | |
| Module 10 | Feedback & learning | Partial | feedback + recommendation reviews | No reviewer workflow |
| Modules 11–14 | Billing, notifications, organisations, admin | Not in Phase 1 build | | Decision pending (DECISIONS §5.4) |

## §17 acceptance tests

| Test | Result | Covered by |
|---|---|---|
| Coordinates only | Pass | `test_coordinates_only_project_runs_available_analyses`, live smoke |
| Coordinates + urban plan | Pass | `test_plan_extraction_and_metrics`, DXF tests |
| Coordinates + planning document | Pass | `test_planning_document_candidates_sources_and_ambiguity` |
| All three input types | Pass | live demo run (`scripts/smoke.py`), all seven modules ran |
| Missing building heights | Pass | `test_missing_heights_…`, `test_no_heights_at_all_…` |
| API unavailable | Pass | `test_api_failure_…`, `test_primary_weather_failure_…` |
| Cloud-covered imagery | Pass (mocked) | `test_cloud_covered_scenes_are_excluded_not_invented` |
| Conflicting plot areas | Pass | `test_conflicting_plot_areas_block_ratio_checks` |
| Invalid plan scale | Pass | `test_image_plan_is_not_used_for_areas`, `test_dxf_requires_epsg` |
| Two design alternatives | Pass | `test_two_alternatives_compared_consistently` |
| Missing planning rules | Pass | `test_missing_rules_means_not_assessed` |
| Incomplete analysis | Pass | `test_partial_run_report_is_labelled_partial` |
| Changed source file | Pass | `test_baseline_is_immutable_and_changes_mark_results_stale`, `test_feature_correction_preserves_original` |
| Unsupported AI question | Pass | `test_unsupported_question_is_not_answered` |
