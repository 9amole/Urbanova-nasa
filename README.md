# URBANOVA — Phase 1

Evidence-based environmental decision support for a development site: create a project from coordinates (optionally a site boundary, an urban plan and planning documents), see what can be analysed, retrieve regional climate, OpenStreetMap context and satellite observations, calculate site and shade indicators, check verified planning rules, compare the original design with alternatives, and produce a report that states every source, method and limitation.

This is a **clean rebuild** of Phase 1 against `URBANOVA_Phase_1_Functional_Requirements_Integrated.md`. The earlier prototype (`../urbanova`) is untouched and remains available for reference — see [docs/DECISIONS.md](docs/DECISIONS.md) §1 for why it was not extended and what was carried forward.

> Status: **Phase 1 analysis engine, ready for backend integration — not production-validated.** Phase C decisions were taken on 2026-11-04 ([docs/DECISIONS.md](docs/DECISIONS.md) §0). Several severity thresholds stay provisional and no real project has been validated yet. See [docs/VERIFICATION.md](docs/VERIFICATION.md) and [docs/VALIDATION.md](docs/VALIDATION.md).

## For the backend team

Start with **[docs/HANDOFF.md](docs/HANDOFF.md)**. The integration component is `urbanova.engine`: JSON in, JSON out, with no database, web framework or accounts. The contract is in `schemas/`, and an example request is `examples/engine_request_sample.json`.

```bash
pip install -e ".[app,test]"
```

```bash
python -m urbanova.engine analyse examples/engine_request_sample.json --out out/ --result out/result.json
```

The web app described below is a **reference demo** built on the same engine core. It is not the integration target.

## Prototype demo app: quick start (Windows, Python 3.11)

```powershell
.\run.ps1 setup      # creates .venv and installs pinned requirements
.\run.ps1 test       # automated acceptance tests (offline, ~40 s)
.\run.ps1 demo       # optional: seeds ./data with the synthetic sample project using live data (~1 min, needs internet)
.\run.ps1            # starts the app at http://127.0.0.1:8000
```

Manual equivalent: `python -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt`, then `.venv\Scripts\python -m urbanova.main`. On macOS/Linux use `.venv/bin/python`.

* No API keys are needed — every enabled provider is credential-free. Configuration is optional (`.env.example`).
* **Data sources and licences:** see [Data sources, approval and licences](#data-sources-approval-and-licences) below. The prototype runs in *non-commercial evaluation* mode by default.
* Set `URBANOVA_OFFLINE=1` to run without internet: external analyses then report *data unavailable* (never zero).
* API documentation: http://127.0.0.1:8000/api/docs
* After `.\run.ps1 demo`, sign in with the local demo account whose randomly generated password is written to `data/demo_credentials.txt` (git-ignored, never committed), or register a new account in the UI.

## The Phase 1 journey (UI tabs)

1. **Site** — create a project with a name and coordinates (map click or typing). A boundary is optional. Confirm the location before any analysis runs.
2. **Inputs** — upload an urban plan (GeoJSON; DXF with its EPSG code), planning documents (PDF), unit-rate tables (CSV) or supplementary rasters (GeoTIFF). Review/correct extracted features and add building heights. Create the **baseline** (immutable snapshot of the original design).
3. **Planning rules** — candidate conditions extracted from PDFs with exact wording and page. Verify or reject each; only verified rules are applied. Choose the area basis when plot areas conflict.
4. **Readiness** — per analysis: *Ready*, *Ready with limitations* or *Blocked*, the missing inputs and the next step.
5. **Scenarios** — create alternatives from the baseline; change feature types/heights, remove features, add features from GeoJSON. Every change is logged.
6. **Run analysis** — select modules (defaults follow the project objectives). "Run all active scenarios" guarantees identical settings for comparison. Progress is shown per module.
7. **Results** — status banner (complete/partial/out of date), answers to the selected questions, map layers (design, OSM context, LST, relative heat classes, NDVI, shade), findings with evidence and explanation, recommendations, metrics by module with value kind, provenance.
8. **Compare** — baseline vs alternatives on the same metrics and methods; improvements/regressions, planning checks, findings, indicative costs (only with user rates), optional transparent weighted scoring.
9. **Report** — PDF + HTML report (labelled *partial* whenever anything was limited, blocked, unavailable or out of date) and a ZIP data export (CSV, JSON, GeoJSON, GeoTIFF).

Sample inputs for the whole journey are in `samples/` (all **synthetic**; regenerate with `scripts/make_samples.py`):
`sample_site_boundary.geojson` (lat 30.1200, lon 31.8500 — an arbitrary desert point, not a project site), `sample_plan_baseline.geojson`, `sample_alternative_additions.geojson` (remove the baseline's "Surface car park" in the alternative first), `sample_planning_conditions.pdf` (contains deliberately conflicting plot areas), `sample_rates_TEST_ONLY.csv` (ISO test currency **XTS** — not real prices).

## What "Heat Analysis" means in this build

Heat Analysis (the primary analysis, Module 5) is built from three parts:

| Part | Source | Default status |
|---|---|---|
| Regional climate & heat baseline — hot-season mean Tmax, P90/P95, ETCCDI SU/TR, provisional TX35/TX40 | ERA5 via Open-Meteo (FR-recommended); NASA POWER fallback, disclosed | **Enabled** (non-commercial evaluation) |
| Observed land surface temperature — Landsat C2 L2, per-pixel cloud mask, site vs surroundings, relative classes | USGS Landsat via the Microsoft Planetary Computer mirror | **Enabled** — route approved by the project owner on 2026-11-01 |
| Design heat-relevant indicators — green-space and hard-surface proportions, geometric shade on open space | Calculated from the user's design | **Enabled** |

Vegetation (Sentinel-2 NDVI) uses the same mirror and is enabled under the same approval. If a site covers fewer than 9 native 100 m thermal pixels (under about 9 ha, like the 4 ha sample), the LST module completes *with limitations* and warns that site values are dominated by mixed pixels.

The build does **not** predict air-temperature or comfort changes from a design: no validated model is available in Phase 1 (FR §1 boundaries, FR-18, FR-23). Severity bands marked **provisional — not approved** in `urbanova/config/rules.json` are shown as such wherever a finding appears.

## Data sources, approval and licences

`urbanova/config/providers.json` records every provider with an `approval_status`. A provider is called only when it is implemented, enabled, and `fr_recommended`, `fr_required` or `approved`:

* `fr_recommended` — named in the FR as a recommended integration (Open-Meteo, NASA POWER, OSM Overpass). Used for non-commercial evaluation; the team must still confirm the terms (FR-09).
* `approved` — recorded with `approved_by` and `approved_on`. The Planetary Computer access route for Landsat and Sentinel-2 is not named in the FR; the project owner approved it on 2026-11-01. Supervisor confirmation is pending (`supervisor_confirmed: false`) and its commercial-use terms are unverified, so results label it "supervisor confirmation pending; commercial-use terms unverified" and the commercial licence context does not use it.
* `pending_supervisor_approval` — never called. Any future source outside the FR starts here.

**Open-Meteo free tier is non-commercial only.** It affects the prototype as follows:

* **Allowed now:** this prototype's use — internal, non-commercial evaluation — is within the free-tier terms, with CC BY 4.0 attribution. The attribution appears in results and reports.
* **Not allowed:** any commercial use of URBANOVA (paid clients, B2B delivery) on the free tier.
* **Built-in safeguard:** set `URBANOVA_USAGE_CONTEXT=commercial` and the system refuses to call the free tier. It falls back to NASA POWER (disclosed, wind excluded), or uses Open-Meteo's paid customer endpoint if `OPEN_METEO_API_KEY` is set server-side. The key is never logged, cached or sent to the browser.

**Before commercial use**, one of these must be in place:
* an Open-Meteo API subscription;
* self-hosted Open-Meteo;
* ERA5 directly from the Copernicus Climate Data Store (free for commercial use with attribution; needs an account and an adapter, which is not built);
* NASA POWER as the primary source.

**Basemap tiles.** The map uses OpenStreetMap's volunteer tile servers by default. Their [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) allows light use with visible attribution and a valid referrer; the app sends its origin as referrer and shows the attribution. They are **not for production or heavy use**: set `URBANOVA_TILE_URL` and `URBANOVA_TILE_ATTRIBUTION` to a commercial tile provider or your own tile server before deployment.

The team must also confirm the licence of every other source (Overpass fair use, Planetary Computer terms) and record the decision in the registry.

## Repository layout

```
urbanova/                 Python package
  engine/                 INTEGRATION COMPONENT: api.py (analyse, extract_plan, extract_rules), contract.py (JSON contract),
                          core.py (runs the modules; shared by the demo app), __main__.py (command line)
  analysis/               climate, context, urban_form, surface_heat, vegetation, solar, compliance
  providers/              adapters: weather (Open-Meteo, NASA POWER), osm (Overpass), satellite (STAC/COG), base (retry, cache, logging)
  config/                 providers.json (registry + licence decisions), rules.json (thresholds + status), recommendations.json
  plans.py documents.py findings.py comparison.py cost.py geo.py values.py recording.py catalog.py explain.py
  api.py orchestrator.py db.py security.py projects.py scenarios.py report.py ...   prototype demo app (reference only)
schemas/                  analysis_request.schema.json, analysis_result.schema.json (generated from engine/contract.py)
examples/                 engine_request_sample.json (synthetic)
validation/               results of the D2/D3 checks (see docs/VALIDATION.md)
web/                      demo UI: index.html, app.js, styles.css (no build step; Leaflet 1.9.4 from unpkg with SRI)
samples/                  synthetic demo inputs
scripts/                  make_samples.py, make_engine_example.py, smoke.py (live demo), validate_climate_ghcn.py,
                          plausibility_satellite.py, validate_real_geometry.py
tests/                    FR §17 acceptance, remediation regressions, engine contract, Arabic extraction
docs/                     HANDOFF.md, DECISIONS.md, VALIDATION.md, VERIFICATION.md, METHODOLOGY.md, REQUIREMENTS_TRACEABILITY.md
```

Runtime data (SQLite database, uploads, provider cache, outputs) lives in `./data` (git-ignored).
