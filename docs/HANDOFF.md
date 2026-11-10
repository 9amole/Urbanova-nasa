# URBANOVA analysis engine — handoff to the backend team

**The integration contract (what to call, request and result fields, AI service, UI rules) is in [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md).** This document covers ownership, operations, testing and open items.

This repository hands over the **analysis side** of URBANOVA Phase 1 as one component, `urbanova.engine`. It turns a site, designs, verified planning rules and unit rates into metrics, findings, recommendations, map layers and a scenario comparison, each with full provenance.

**What the engine is not:**

* It is not a web service, user system, database or UI. Those belong to the backend platform (decision C3, `docs/DECISIONS.md` §0).
* It is not production-validated. Read §8 before relying on any result.

| Owned by the backend team | Owned by data science (this repository) |
|---|---|
| Users, authentication, roles, organisations, SSO | Analysis methods, thresholds and their status (`urbanova/config/rules.json`) |
| Projects, file storage, database (e.g. PostGIS), result storage | Data sources, access routes and licence records (`urbanova/config/providers.json`) |
| Job queue, retries, timeouts at job level, scaling | Input validation of analysis requests; provider retries and caching inside a call |
| Product UI, maps, report presentation, notifications, billing, admin | Recommendation templates (`urbanova/config/recommendations.json`) and the result contract |
| Security hardening, rate limiting, secrets management, monitoring | Validation evidence (`docs/VALIDATION.md`) and method documentation (`docs/METHODOLOGY.md`) |

## 1. Install

You need Python 3.11. The engine needs no API keys.

```bash
pip install "urbanova @ git+https://github.com/Urbanoova/urbanova-1.1"   # engine only
pip install "urbanova[app,test] @ git+https://github.com/Urbanoova/urbanova-1.1"   # + prototype demo app and tests
```

The exact tested versions are in `requirements.txt`. On Linux, macOS and Windows, `rasterio` installs from wheels that include GDAL.

## 2. Use

```python
from urbanova.engine import analyse, extract_plan, extract_rules, EngineInputError

plan = extract_plan("plan.dxf", dxf_bytes, {"latitude": 30.12, "longitude": 31.85}, epsg="32636")
rules = extract_rules("conditions.pdf", pdf_bytes)            # English or Arabic text-layer PDFs
result = analyse(request_dict, output_dir="/var/urbanova/results/<request-id>")
```

The command-line runner is useful from other languages and for trying the engine out:

```bash
python -m urbanova.engine analyse examples/engine_request_sample.json --out out/ --result out/result.json
```

```bash
python -m urbanova.engine extract-plan plan.dxf --lat 30.12 --lon 31.85 --epsg 32636
```

```bash
python -m urbanova.engine extract-rules conditions.pdf
```

```bash
python -m urbanova.engine validate out/result.json
```

```bash
python -m urbanova.engine schema --out schemas/
```

Exit codes: 0 success, 2 invalid input (the errors are printed as JSON), 1 unexpected error.

## 3. Integration flow

1. **Plan upload.** Call `extract_plan()` with the uploaded GeoJSON or DXF. Show the returned features to a person to review: `review_status`, `classification_confidence`, `issues` and the quality report (overlaps, features outside the site, buildings without heights).
   * A DXF without declared units returns `units_unconfirmed`. `analyse()` refuses those features until a person confirms the drawing is in metres. On confirmation, drop the flag.
   * PDF and image plans are reference-only (decision C3).
2. **Planning documents.** Call `extract_rules()`. Every candidate keeps its exact wording and page. Low-confidence candidates carry English notes explaining why (decimal comma, reversed Arabic text, basement floors, advisory wording, conflicts).
   * A person verifies or rejects each one. Only verified rules go into `analyse()`.
   * Send the count of unverified candidates, and any conflicting plot areas that are still unresolved.
3. **Unit rates.** Use quoted rates only. The engine never invents a price; quantities without a rate come back as `price_required`.
4. **Designs.** Send one baseline (the original design) and up to 5 alternatives. All designs in one request share the same site data and settings (D4), so a comparison in the same request is like-for-like.
5. **Analyse.** Call `analyse()` in a background worker, not in a web request. With live data it takes about 1–2 minutes for two designs, mostly network time.
6. **Store and present** the result. Map layers are PNG overlays in Web Mercator with WGS 84 bounds, or GeoJSON, plus analysis-grade GeoTIFFs in local UTM. All are referenced by paths relative to `output_dir`.
   * Show every dataset's `attribution`, which the licences require.
   * Show `approval_label`, the `limitations`, and any finding whose threshold status is `provisional_not_approved` *as provisional*.

## 4. The contract

**Chat assistant (S8).** `POST /analysis-runs/{rid}/chat` `{question}` (editors) answers one turn about the run and stores the question and answer in `chat_messages` (`seq`, `role`, `text`, `claims`, `based_on`, `tool_calls`, `generated_by`, `label`, `provider`, `model`, `reason`); `GET /analysis-runs/{rid}/chat` returns the conversation (viewers included). For an integrating backend: `urbanova.ai.chat.turn(bundle, question, history, ai_allowed, local_only, comparison)` returns the same reply without storage. The tools only read the given bundle and comparison; `/ask` remains the single-turn endpoint.

**Land cover and downscaling (S7).** New module `land_cover` (A2b, existing land, site level) from ESA WorldCover 2021 via the Planetary Computer (`pc_esa_worldcover`, CC BY 4.0 — show its attribution). It adds `landcover_*` share metrics, a categorical layer and a second `site_context` record (`type: "land_cover"`) after H2. `Settings.lst_downscale` (bool, default false) turns on random-forest downscaling of the LST composite to 30 m; it adds `vegetation` and `land_cover` to the run, and, only when its checks pass, `lst_ds_site_median_c` / `lst_ds_site_p90_c` (output kind `modelled`) and a 30 m layer. The validation figures are in `modules.surface_heat.details.downscale.checks`. New dependency: scikit-learn 1.9.1.

**Report and app (S6).** The report opens with five sections around the supervisors' questions (executive summary with source ids, one six-part section per design hotspot, site context, comparison and trade-offs, recommended design and next steps); the former technical sections follow as Appendices A–N. Reports of projects with TEST prices (XTS) or the `synthetic_data` flag carry a synthetic-data label on every page. The data export adds `hotspots.geojson` and `hotspots.csv`. New app endpoints: `GET /projects/{pid}/dashboard`, `POST /analysis-runs/{rid}/options/{oid}/draft` (apply a measured option as a draft alternative); `PATCH /scenarios/{sid}` accepts `{"op": "update", "geometry": …}` for map edits (Leaflet-Geoman 2.20.3, pinned with SRI).

**Comparison (S5).** The engine's `compare` block accepts criteria from `comparison.CRITERIA` (including the derived `cost_typical` and `compliance_failures`) and an optional `budget_amount` / `budget_currency`. `AnalysisResult.comparison` adds `choice` (no_winner, or best_under_priorities with weights, exclusions and robustness), `tradeoffs` (chart points, Pareto front, give-up list) and `hotspots` (by severity per design).

**AI text (S4).** `urbanova.ai.generate_insights(bundle, ai_allowed)` and `urbanova.ai.answer_question(bundle, question, ai_allowed)` turn a run bundle (the keys of `urbanova.orchestrator.run_bundle`) into validated insight blocks, labelled `generated_by: "ai" | "fallback"`. `analyse()` itself makes no model calls. Configure providers with `URBANOVA_LLM_PROVIDERS` and `URBANOVA_LLM_<NAME>_BASE_URL / _MODEL / _API_KEY` (server-side environment only), `URBANOVA_LLM_ENABLED`, `URBANOVA_LLM_TIMEOUT_S`, `URBANOVA_LLM_MAX_CALLS_PER_DAY`.

The contract is defined in `urbanova/engine/contract.py` and exported as JSON Schema (`schemas/analysis_request.schema.json`, `schemas/analysis_result.schema.json`). A test fails if the exported schemas fall out of date. `contract_version` is `1.1` (1.1 added `condition` to every metric, and `hotspots`, `site_context`, `package` and `findings[].hotspot_ids` to each design result, and `intervention_id`, `hotspot_id`, `options`, `confidence`, `cost` and `literature` to recommendations; all additive, and requests marked 1.0 are still accepted): a breaking change raises the major number, and an addition raises the minor.

Rules the UI must respect:

* **Missing is never zero.** A metric has a value only when `state` is `available`. Otherwise `value` is null and `reason` says why: unknown, unavailable, blocked, invalid or not_applicable.
* **`output_kind`** says what a value is: observed, reanalysis, calculated, classified, modelled or user_provided. Show it.
* **Findings:**
  * `severity` comes from a stated rule. Provisional bands are capped at *moderate* (the band level is kept in `threshold.band_level`).
  * `affected_area` says where a finding applies. Regional climate findings have no geometry and must not be drawn on the site.
* **Module outcomes:** every module reports `completed`, `partial`, `blocked`, `unavailable`, `failed` or `skipped`, with a reason. A provider outage **never** raises; it is reported per module. `shared_across_designs` marks site results reused for a second design.
* **Reproducibility:**
  * `input_fingerprint` is identical for identical inputs, ignoring `request_id`.
  * `analysis_version` changes whenever a method changes. Treat results with a different version as out of date and never compare them.
* **Invalid input** raises `EngineInputError`, whose `.errors` is a list of `{"loc": [...], "msg": "..."}` entries. Map it to HTTP 422 and show the messages.

## 5. Configuration and operations

| Environment variable | Purpose | Default |
|---|---|---|
| `URBANOVA_DATA_DIR` | Provider response cache (`cache/`) | `./data` in a source checkout, `~/.urbanova` when installed |
| `URBANOVA_CONFIG_DIR` | Use a reviewed copy of `providers.json`, `rules.json`, `recommendations.json` | the copy inside the package |
| `URBANOVA_USAGE_CONTEXT` | `non_commercial_evaluation` or `commercial`. Commercial mode refuses Open-Meteo's free tier and sources whose commercial terms are unverified | `non_commercial_evaluation` |
| `OPEN_METEO_API_KEY` | Open-Meteo customer endpoint for commercial use (server-side only; never logged or cached) | unset |
| `URBANOVA_OFFLINE` | `1` = never call providers; analyses report *unavailable* | `0` |
| `URBANOVA_AI_CLOUD_FOR_REAL_DATA` | `0` = send real project data only to a local model (the 8 Nov 2026 rule). On by default since the owner's decision of 10 Nov 2026 | `1` |
| `URBANOVA_LLM_<NAME>_MODELS` | A provider's model pool, comma-separated: a rate-limited model hands over to the next at once (`_MODEL` sets a single model) | Groq: `openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b` |
| `URBANOVA_SUPPORT_PROVIDERS`, `URBANOVA_SUPPORT_GROQ_MODELS` | Provider order and Groq pool of the help assistant (S9) | `groq`; `openai/gpt-oss-20b,qwen/qwen3.8-27b,openai/gpt-oss-120b` |
| `URBANOVA_LLM_OPENAI_API_KEY`, `URBANOVA_LLM_OPENAI_MODEL` | Paid opt-in for the help assistant (needs prepaid credit and `URBANOVA_SUPPORT_PROVIDERS=openai`) | unset, `gpt-5.4-mini` |
| `URBANOVA_SUPPORT_MAX_PER_USER_DAY` | AI answers per user per UTC day before the help centre answers automatically | `100` |
| `URBANOVA_SUPPORT_MAX_CALLS_PER_DAY` | Model calls per UTC day for the help assistant, counted apart from the analysis AI's budget | `1000` |
| `URBANOVA_HTTP_TIMEOUT_S`, `URBANOVA_HTTP_MAX_RETRIES` | HTTP client defaults (Overpass has its own 75 s timeout in the registry) | 45, 3 |

**Concurrency:** `analyse()` can run in parallel threads or processes. Provenance is collected per call (context variable), and the file cache is protected per key. Give each call its own `output_dir`.

**Outbound network access** needed with default settings:

* `archive-api.open-meteo.com`, and `customer-archive-api.open-meteo.com` with a key;
* `power.larc.nasa.gov`;
* `overpass-api.de`;
* `planetarycomputer.microsoft.com`;
* `*.blob.core.windows.net` (satellite image files).

**Production load:**

* Overpass must be an own instance or a regional extract (registry note).
* The Planetary Computer terms must be reviewed before commercial use (decision C1).

**Safety:** uploaded files are parsed, never executed. Requests are limited to 6 designs, 5,000 features per design and a bounded vertex count. Secrets come from the environment only.

## 6. Testing

```bash
python -m pytest
```

This runs offline in about 30 s with mocked providers. It covers the FR acceptance tests, the remediation regressions, the engine contract, input validation, the database-free path, D4 sharing, and Arabic extraction.

Live checks (need internet):

* `scripts/smoke.py`: the prototype app end to end;
* `scripts/validate_climate_ghcn.py`;
* `scripts/plausibility_satellite.py`;
* `scripts/validate_real_geometry.py`.

The results of the last run are in `docs/VERIFICATION.md` and `docs/VALIDATION.md`.

## 7. The prototype app

`urbanova/api.py`, `web/` and `run.ps1` are a reference demo: FastAPI, SQLite, local accounts and a Leaflet UI. It uses the same engine core (`urbanova.engine.core.run_design`), so it shows exactly what the engine computes. It is **not** the integration target: its accounts, storage and UI are prototype-level. `report.py` contains a reference HTML/PDF report layout (FR-33, 14 sections) that the product report can follow.

## 8. Known limitations and open items

The decisions still needed from the team and supervisors, with recommendations and a form to record them, are in `docs/decisions/SUPERVISOR_DECISIONS.md` (record basis approvals with `scripts/record_approvals.py`).

* **Evidence:** results come from synthetic samples, real OSM geometry, and station and land-cover checks (`docs/VALIDATION.md`). **No real project has been validated** (D2 open). In particular there has been no real CAD export, no real Arabic planning document and no scanned page.
* **Thresholds:** severity thresholds marked provisional are not approved (decision C2).
* **Climate data:** ERA5 minimum temperatures were 1.5–2.2 °C warmer than two Egyptian stations, so tropical-night counts can be overstated. This is stated on every climate result.
* **Licensing:** commercial use is not cleared (decision C1).
* **Not in Phase 1:**
  * OCR of scanned documents;
  * PDF or image plan digitising;
  * temperature or comfort prediction from a design;
  * population and air quality analyses.
* **AI text** (insights, chat) is server-side, grounded and validated, with automatic text as the fallback (S4, S8; see INTEGRATION_CONTRACT §5).
