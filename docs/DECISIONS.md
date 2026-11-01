# Decisions, assumptions, document conflicts and open questions

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
| C-2 | Module 4 §3.3: satellite heat/vegetation products "must remain planned or unverified until tested and approved". FR P1 lists Sentinel-2 and Landsat integration in Phase 1. | Implemented and tested (mocked tests; one live run before the review). The access route was **approved by the project owner on 2026-11-01** (recorded in `config/providers.json`). Supervisor confirmation is still recommended under Module 4 §8. |
| C-3 | Module 5: Heat Analysis may output "a relative heat score or index". FR-27/FR-45 forbid one unexplained score. | No composite heat index. Heat Analysis reports separate indicators plus relative LST classes with stated breaks. |
| C-4 | FR names USGS (Landsat) and Copernicus Data Space (Sentinel). | The adapters read the identical USGS C2 L2 and ESA L2A products from the Microsoft Planetary Computer STAC mirror, which needs no credentials. That route is not named in the FR. The project owner approved it on 2026-11-01, so it is enabled; supervisor confirmation is recommended. CDSE and NASA Earthdata adapters are registered as *not implemented*. |
| C-5 | Modules 11–14 (billing, notifications, organisations, admin) are written as Phase 1 modules; FR §16 priorities do not include them. | Not built (see §4). Module 1 implemented minimally (local accounts, per-project roles). |
| C-6 | Module 6 leaves scenario limits to the team. | Max 5 active alternatives, 1 active baseline; archive instead of delete. |

## 3. Assumptions (smallest safe choice, easy to change)

| ID | Assumption | Where to change |
|---|---|---|
| A-1 | Climate period default = last 5 complete calendar years; user may set 1981+ | `analysis/climate.py`, project settings |
| A-2 | ERA5 (0.25°) via Open-Meteo is primary; NASA POWER is fallback, wind excluded on fallback | `config/providers.json`, `providers/weather.py` |
| A-3 | "Hot season" = 3 months with highest mean Tmax in the climate record (hemisphere default if unavailable) | `analysis/surface_heat.py` |
| A-4 | LST: last 3 complete years, scene cloud < 40 %, ≥ 60 % of site pixels clear, ≤ 6 scenes, 1 km surroundings ring | run settings / `orchestrator.DEFAULT_CONFIG` |
| A-5 | NDVI: last 365 days, ≤ 4 clear scenes; "vegetation indicator" = NDVI ≥ 0.2 | `analysis/vegetation.py` |
| A-6 | Without a boundary, satellite "site" statistics use a 100 m radius around the point (always labelled) | `geo.study_area` |
| A-7 | Shade: flat terrain, opaque casters, true solar time, analysis day = 21st of the middle hottest month | `analysis/solar.py` |
| A-8 | Floor-area ratio assumes every floor equals the footprint | `analysis/urban_form.py` |
| A-9 | Plot-area tolerance 5 % before the user must choose the area basis | `config/rules.json` |
| A-10 | Comparison preference directions are heat-focused (more green/shade, less hard surface) — disclosed on screen | `comparison.PREFERRED` |
| A-11 | 1 feddan = 4,200.83 m² when converting document areas (disclosed in the rule note) | `documents.AREA_UNITS` |
| A-12 | Upload limit 25 MB; SQLite storage; sessions 12 h | `.env` |
| A-13 | FR-recommended providers (Open-Meteo, NASA POWER, Overpass) are used for **non-commercial evaluation** before the team confirms terms; non-FR access routes are used only once approved (Planetary Computer: approved by the project owner, 2026-11-01) | `config/providers.json` (`approval_status`) |
| A-14 | Relative LST classes use mid-rank percentiles so tied values stay in one class | `analysis/surface_heat.py` |
| A-15 | Site smaller than 9 native 100 m thermal pixels → LST module marked "partial" with a mixed-pixel warning | `analysis/surface_heat.py` |

## 4. Not in this build (deliberately)

* Modules 10 (beyond recording recommendation reviews/feedback), 11 billing, 12 notifications, 13 organisations, 14 administration UI.
* OCR (no OCR engine installed): scanned pages are reported, not guessed.
* PDF/raster plan digitising and manual georeferencing (C-1).
* Population, air quality, terrain, flood, wind, thermal comfort (FR-21 optional; no approved provider/method).
* Generative-AI explanations: Module 9 is met with deterministic, record-linked explanations; an LLM layer needs a decision on provider, data policy and evaluation.
* Uploaded supplementary rasters are validated and catalogued but not consumed by analyses.
* PostgreSQL/PostGIS, object storage and a separate job queue (SQLite + thread pool used; persistence isolated in `db.py`).

## 5. Decisions needed from the supervisor / engineering team

1. **Approve the Phase 1 data sources and access routes** (C-2, C-4):
   * confirm the terms of the FR-recommended Open-Meteo/ERA5, NASA POWER and OSM Overpass;
   * confirm the Planetary Computer route for Landsat and Sentinel-2 (approved by the project owner on 2026-11-01), or replace it with CDSE/USGS adapters with credentials.

   Open-Meteo's free tier is **non-commercial**. Commercial use requires a subscription key, self-hosting, a direct CDS ERA5 adapter, or NASA POWER as the primary source (see §6).
2. **Confirm or replace the provisional thresholds** in `config/rules.json` (status `provisional_not_approved`, labelled as such in the UI and reports): TX35/TX40 day markers, LST site-vs-surroundings bands (2 K / 4 K), hottest-decile share bands (20 % / 40 %), NDVI vegetated-share bands, open-space exposure bands. ETCCDI SU/TR are standard.
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
