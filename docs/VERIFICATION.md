# Verification record

## J. Full check after stages S1–S11 — 12 Nov 2026

The whole project (about 14,000 lines) was checked again: tests, a clean install, live runs, the validation scripts,
a security review, a logic review, a static scan and a browser walkthrough.

**Automated (actual runs, Windows 11, Python 3.11):**

| Check | Result |
|---|---|
| `pytest` (offline, mocked providers) | **337 passed** (338 with the new policy test) |
| Clean install: wheel into a fresh virtual environment | Pulls in no FastAPI or ReportLab. `urbanova-engine analyse examples/engine_request_sample.json` exits 0 and `validate` reports the result valid |
| `scripts/smoke.py` (live providers) | Exit 0 in 92 s. Two designs analysed, comparison, PDF report (420 KB), data export (238 KB) |
| `scripts/validate_climate_ghcn.py` | Unchanged from VALIDATION.md §1 (cached station data) |
| `scripts/plausibility_satellite.py` (live) | All 3 checks pass: NDVI water < desert < farmland, LST water < farmland < desert |
| `scripts/validate_real_geometry.py` | Pass: engine and independent equal-area areas agree to 0.00 points (129 real buildings) |
| Static scan (`ruff`: undefined names, likely bugs) | 3 unused names, removed. The rest are the FastAPI `Depends` pattern and intentional plain errors (`raise … from`) |

**Security review:**
- **Access:** every one of the 74 endpoints was listed with its checks. All of them need a signed-in user, except
  sign-in and registration, the public help content (FAQ, plans, page help), meta and health. Every project, run,
  file, scenario, report, chat and help-conversation endpoint goes through the membership check, and changes need the
  editor role. Adding members needs the owner.
- **Accounts:** passwords are hashed with scrypt and compared in constant time. There is a lockout after failed
  sign-ins, and tokens are random and stored only as hashes.
- **Uploads:** reads are capped before parsing, stored names are cleaned, and file types are allow-listed. Files are
  served only from paths stored by the server.
- **Secrets:** keys are read from the server environment only, URLs in the request log are redacted, and the secret
  scan of every pushed commit was clean.
- **Browser:** every value from the database or a model is escaped before it reaches the page (map layer names and
  popups included), help links are limited to app routes, and there are no inline scripts or handlers.
- **Fixed:** the pages had no **Content-Security-Policy**. They now send one: scripts only from this server and the
  pinned unpkg libraries, images also from the configured map-tile host, no plugins, no framing. The walkthrough
  below ran with it and nothing was blocked.
- **Left to the backend platform** (HANDOFF): rate limiting of sign-in and registration, HTTPS, and monitoring. The
  AI already has its own daily and per-user caps.

**Logic review:**
- **Costs:** never summed across currencies, "price required" without a rate, only increases costed.
- **Comparison:** refuses designs analysed with other settings; missing values are never 0.
- **Site analysis:** design-only modules are left out, not blocked; heat stress runs without shadows.
- **AI:** grounding validator, read-only tools, refusals before any model call, data rule.
- **Fixed:** the Dashboard cost tile now says it adds up each hotspot's option on its own (the site-wide package is
  evaluated together on 7 · Results).

**Browser walkthrough** (the demo design project and the site-analysis project, all 10 tabs each, with the new
policy):
- every view rendered with no error alerts, no script errors and no blocked content;
- map tiles 6/6 loaded, and Leaflet and Geoman loaded;
- a full comparison of 3 designs rendered every section.

## I. Integration readiness (Phase C/D, engine) — 4 Nov 2026 (uncommitted, awaiting review)

The Phase C decisions were taken ([DECISIONS.md](DECISIONS.md) §0). The data-science D items were carried out, and the analysis side is handed over as `urbanova.engine` ([HANDOFF.md](HANDOFF.md)).

**Tests (actual runs, Windows 11, Python 3.11.9, offline with mocked providers):**

* `pytest tests` → **153 passed** in about 30 s.
* The new tests cover:
  * `test_engine.py`, 25 cases: contract, 10 invalid-input cases, database-free path, fingerprint, D4 sharing, extraction, CLI, schema currency, GIS tags;
  * `test_arabic.py`, 17 cases.
* Three existing label assertions were changed to the confirmed state recorded by decision C1. Each still checks the "pending" wording when the confirmation is removed.

**Clean install:**

* A wheel built with `pip wheel .` and installed into a fresh virtual environment pulls in **no** FastAPI, Starlette, uvicorn or ReportLab.
* Run from outside the repository, `urbanova-engine analyse examples/engine_request_sample.json` exits 0 and `urbanova-engine validate` reports the result valid.
* The configuration loads from inside the installed package.
* Not tested: Linux or macOS installs, and Python 3.12.

**Live runs** (internet, fresh data directories):

| Run | Result |
|---|---|
| Engine, `examples/engine_request_sample.json` (2 designs) | Exit 0 in 59 s; result valid. Provider log: Open-Meteo 1, Overpass 1, Planetary Computer Landsat 2 and Sentinel-2 2 successful requests for **both** designs together. All 34 site-level metrics identical across the designs. The comparison matches the app run of section R (green 9.06 → 13.57 %, open space 14.31 → 18.06 %, the same planning checks). 10 layer files present. |
| Prototype app, `scripts/smoke.py` | Exit 0 in 95 s. Same metrics, findings and comparison as the engine. "Run all scenarios" now makes the same single set of provider requests (before: separate Planetary Computer downloads per scenario). |
| `scripts/validate_climate_ghcn.py` | 2 stations; see [VALIDATION.md](VALIDATION.md) §1 |
| `scripts/plausibility_satellite.py` | All 3 checks pass; see [VALIDATION.md](VALIDATION.md) §2 |
| `scripts/validate_real_geometry.py` | 134 real OSM polygons; engine vs independent equal-area areas agree (< 0.005 points); see [VALIDATION.md](VALIDATION.md) §3 |

**Fixed because of these checks:**

* parks and parking in GIS exports (`leisure`, `amenity`, `natural` attributes) were left unclassified;
* the feddan conversion note quoted the Arabic unit instead of naming it in English;
* engine findings were not sorted by severity;
* browsers kept the old stylesheet after an update. Asset links are now versioned (`?v=1.1.0`).

**Version changes:**

* Methods version bumped to `p1-methods-2026.11`, so runs made with earlier methods show as out of date.
* Package version is now `1.1.0`.

**UI** (review server on port 8001, live demo data):

* the Results, Inputs, Planning rules, Readiness, Scenarios, Compare and Report tabs render;
* source labels show the recorded confirmation;
* the climate caveat is present;
* the page fits its width once the current stylesheet is loaded;
* the only console error is the expected 401 from before sign-in.

**Still not verified:**

* a real project (D2);
* OCR and scanned pages (decision C4: not in Phase 1);
* commercial use (decision C1);
* the provisional thresholds (decision C2);
* production deployment, load, security hardening and accessibility of the product UI (backend team).

---

## R. Remediation Phase A/B — 4 Nov 2026 (uncommitted, awaiting review)

Code and documentation changes for remediation items A1–A6 and B1–B6. Nothing was committed, deployed or published.

**Tests (actual runs, Windows 11, Python 3.11.9, offline with mocked providers):**

* `pytest tests/test_remediation.py` → **46 passed**. These are the new regression tests for A1–A6 and B1–B5.
* `pytest tests` → **111 passed**. That is 58 existing tests, 7 cases of the earlier parking-layer test, and the 46 new tests.
* No existing test was weakened. One existing assertion (`test_review_fixes.py`, approved-route label) was extended to require the new "supervisor confirmation pending" wording.

**Live sample workflow** (`scripts/smoke.py`, synthetic samples, live Open-Meteo, Overpass and Planetary Computer):

* Run on the committed code and on the working tree, each into a fresh data directory. Both exited 0 (93 s and 111 s).
* Differences in the transcripts:

| Item | Before | After | Cause |
|---|---|---|---|
| Alternative open-space proportion | 18.82 % | 18.06 % | A2. The alternative places 6 tree canopies of 50.1 m² each (300.6 m², 0.75 % of the site) inside the paved plaza. They were counted as both plaza and green space; they are now counted once. Baseline unchanged at 14.31 % (no overlap). |
| "Frequent very hot days" finding (TX35 = 67 days/yr) | high | moderate (PROVISIONAL, capped from high) | B2. The TX35 bands are provisional and not approved. |
| Baseline site context (OSM) | unavailable (Overpass http_error ×3) | partial | Not a code change. Overpass was flaky during both runs (see FR-40 in the traceability table). |
| PDF size | 203,957 B | 209,826 B | B3. Added sections. |

* Unchanged: every other metric, the planning checks (the same single failure, green space 9.06 % < 20 %), the comparison outcome directions, the costs, the report label (*partial*) and the objective answer.
* No planning check flipped.
* A rasterio `NotGeoreferencedWarning` was printed once in the after run. It comes from `providers/satellite.py`, which is unchanged, and the LST values were identical in both runs. Its cause was not investigated.

**PDF report:** the report generated in the live run contains all 14 headings of `report.SECTIONS` in its extracted text, plus "Affected area: Regional…", "Capped at moderate", "supervisor confirmation pending", "Analysis methods version" and "never as zero".

**UI**, checked in the browser pane on a review server (port 8001, scratch data directory):

* **DXF units:** a unitless DXF shows the units warning and the per-file "is in metres — confirm units" button, plus the note "Entities not used as plan geometry: 1 TEXT". Confirming removes the warning. Layer-only classification gives building, parking and green space.
* **Re-upload:** the re-upload selector appears once plan features exist. Submitting without a choice is refused with an explanation.
* **Results:**
  * the climate card reads "MODERATE (PROVISIONAL) · capped from high" with "Affected area: Regional (~25 km ERA5 grid cell…)";
  * site findings show "Affected area: Site boundary";
  * the source status reads "Approved by Project owner (development session) on 2026-11-01 — supervisor confirmation pending; commercial-use terms unverified", with an amber badge.
* **Compare, with two scenarios:** the scoring shows the small-set warning and "value 13.57 %; +4.51 vs baseline" under the score.
* **Layout fix found during the check:** long source-status badges overflowed the provenance table (1,408 px on a 1,009 px page, also before this change). Badges in table cells now wrap. A cached stylesheet needs Ctrl+F5.
* **Not checked in the UI:** clicking "Create baseline" while units are unconfirmed. This is covered by the API test only, because the button opens a native confirm dialog.

**Still not verified:** anything listed in §5 below. In particular, no real planning documents, CAD exports or real projects were used. Every result above comes from synthetic samples and is not validation.

---

# Pre-commit review, 1 Nov 2026 (historical)

Test counts in the sections below are those of their date: 56, then 57, then 58 after the basemap fix. The current count is in section R.

## 0. Update — satellite route approved (1 Nov 2026)

The project owner approved the Microsoft Planetary Computer access route for Landsat C2 L2 and Sentinel-2 L2A. The approval is recorded in `urbanova/config/providers.json`; supervisor confirmation is still recommended.

* **Tests:** `pytest` → **57 passed**. The gating tests now mark a provider unapproved explicitly, and a new test checks that the recorded approval enables the route and shows in provenance.
* **Clean live demo:** all seven modules ran for both scenarios in 70 s.
  * Readiness: LST and NDVI **ready**.
  * Landsat: 6 clear scenes; site median LST 53.1 °C vs surroundings 52.8 °C (+0.37 K, no finding); hottest-decile share 2.8 %. The module completed *with limitations* because the 4 ha site is about 4 native 100 m thermal pixels.
  * Sentinel-2: site NDVI 0.083, 0 % vegetated → "Limited observed vegetation" finding (provisional threshold).
  * Co-location: NDVI 0.078 in the hottest decile vs 0.078 elsewhere — no difference at this bare desert point.
  * Provider log: Landsat 4 and Sentinel-2 4 successful requests. One Overpass HTTP error was retried successfully.
  * Report still labelled *partial* because of the LST resolution limitation and the OSM completeness warning.
* **UI:** both module cards show results; the NDVI, LST and heat-class map layers are available; provenance shows "Approved by Project owner … on 2026-11-01"; no console errors.

Sections 2 and 5 below describe the earlier run, made while the route was still blocked.

Environment: Windows 11, Python 3.11.9, pinned `requirements.txt` in `.venv`, internet available. Licence context: `non_commercial_evaluation` (default). Repository: uncommitted.

## 1. Automated tests

`.\run.ps1 test` → **56 passed** (≈ 37 s), offline, with providers mocked or disabled.

* `tests/test_acceptance.py`: every FR §17 acceptance test, the applicable B.7 backend tests, value-state invariants, access control, rates, and solar/shadow geometry against reference values.
* `tests/test_review_fixes.py`, 18 tests added in this review, covering:
  * source approval and gating;
  * Open-Meteo licence context and commercial key handling;
  * provisional-threshold labelling;
  * tied LST values;
  * LST–NDVI co-location;
  * out-of-period weather data;
  * the upload size limit and a non-UTF-8 CSV;
  * invalid run settings and a bad scenario import;
  * mismatched report comparisons and stale comparisons after a boundary change;
  * owner role protection;
  * land-use distribution;
  * objective answerability;
  * report labels.

Test fixtures use obviously fake values: password `correct-horse-battery`, key `TEST-KEY-123`.

## 2. Live end-to-end demo (default settings, approved-only sources)

`scripts/smoke.py ./data` runs the full journey against live Open-Meteo and Overpass, using the synthetic samples (arbitrary desert point 30.1200 N, 31.8500 E, 4.0 ha). It took 39 s.

| Check | Observed |
|---|---|
| Run before location confirmation | Rejected (HTTP 422) with explanation |
| Plan | 12 features extracted |
| Planning PDF | 10 candidates. Conflicting plot areas flagged. "6 floors (G+5)" no longer produces a false conflict. |
| Readiness | Climate, context, urban form, shade, planning, comparison and cost **ready**. LST and NDVI **blocked: awaiting supervisor approval**. Population and air quality: not in build. |
| Module outcomes (both scenarios) | • Climate: completed<br>• Context: partial (no mapped buildings at the desert point; warning shown)<br>• Urban form, shade, planning: completed<br>• LST, NDVI: blocked |
| Provider calls (from `provider_logs`) | Open-Meteo 1 request + 1 cache hit; Overpass 1 + 1 cache hit; **no calls to Planetary Computer**; no unredacted tokens |
| Climate (ERA5 2021–2025) | Hot-season mean Tmax 35.4 °C, TX35 67 days/yr (HIGH, **provisional threshold**), TR 138 nights/yr |
| Findings | • Provisional-threshold findings labelled as such (climate, open-space exposure)<br>• Verified-rule failure: green space 9.06 % < 20 % (high)<br>• Data-quality findings for the blocked modules |
| Comparison | Comparable. Green +4.5 pts, hard surface −3.75 pts, 15:00 open-space sun 99.7 → 93.3 %, shade 0.08 → 0.75 h. Value kind shown per row. |
| Cost | Priced only from **XTS test rates**. Removed paving: "price required". |
| Report | Labelled **partial**. HTML/PDF include the licence context, source approval status, PROVISIONAL THRESHOLD labels and the blocked modules. |
| Objectives | "Main concerns" shown as *partly answered — some evidence unavailable* |
| Unsupported question | Refused |

## 3. Web UI (browser pane, http://127.0.0.1:8000)

Checked on the demo project:

* **Results:** partial banner listing blocked modules; LST card shows the approval reason; two provisional-threshold badges; provenance "Source status" column.
* **Readiness:** LST blocked with the approval reason and next step.
* **Planning rules:** 10 "Open source document" buttons.
* **Compare:** kind column present.
* **Report:** comparison picker lists only comparable comparisons; a report generated with the comparison downloads as PDF/HTML.
* **Console:** no errors.

The full form-driven journey (create → upload → correct → baseline → alternative → run → compare → report) was exercised before this review, and the code paths it uses are unchanged apart from the fixes listed in the review summary.

## 4. Repository hygiene

* 61 files would be committed.
* The scan for keys, tokens, passwords, private emails and user paths found **no secrets**. The only hit is the random demo-password generator in `scripts/smoke.py`.
* `data/` (database, uploads, cache, demo credentials), `.venv/` and `.env` are git-ignored.
* Samples are synthetic and moved away from the earlier prototype's pilot coordinates.
* The planning PDF is fictitious; rates use the ISO test currency XTS.
* The earlier `urbanova` project is unchanged.

## 5. What remains unverified

* **Satellite access route:** approved by the project owner, not yet confirmed by the supervisor. The Planetary Computer terms of use have not been reviewed by the team.
* **Open-Meteo commercial endpoint:** no subscription key; tested with a mocked transport only.
* **Scientific validation:** no comparison with ground stations; provisional thresholds are not approved.
* **Real inputs:** no real plans, planning documents (including scans), CAD exports or quoted prices.
* **Licence terms:** provider facts recorded from public documentation, not confirmed by the team.
* **Scale, security hardening, accessibility audit, cross-browser testing:** not done (single-user local prototype; SQLite and a thread pool).
