# Verification record — pre-commit review, 1 Nov 2026

## 0. Update — satellite route approved (1 Nov 2026)

The project owner approved the Microsoft Planetary Computer access route for Landsat C2 L2 and Sentinel-2 L2A. The approval is recorded in `config/providers.json`; supervisor confirmation is still recommended.

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
