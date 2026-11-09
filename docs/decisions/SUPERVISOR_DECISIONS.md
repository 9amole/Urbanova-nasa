# Decisions needed from the team and supervisors — 8 Nov 2026

The smart upgrade (stages S1–S7, `docs/PHASE2_SPEC.md`) is built, tested and pushed. Everything below is a **decision for
people**, not code. Until a value is decided, the software labels it **PROVISIONAL** and it cannot produce the top severity,
so nothing is overstated in the meantime. Each item gives the current state and the developers' recommendation; the decision
is yours.

**How to record decisions (item 1):** fill in `pending_basis_values.csv` (one row per value: `decision` = `approve` or
`change`, `approved_by` = name and role, `approved_on`, optional `new_value` and `note`), then run

```
.venv/Scripts/python scripts/record_approvals.py apply docs/decisions/pending_basis_values.csv --dry-run
.venv/Scripts/python scripts/record_approvals.py apply docs/decisions/pending_basis_values.csv
```

A row without a named approver is rejected, and an invalid form changes nothing. Items 2–7: write the decision and the
decider's name under the item and commit the file.


> **Status, 8 Nov 2026: decided.** The project owner delegated these decisions to the AI assistant (Claude) acting as
> decision maker. The decisions below are recorded with that attribution (`approved_by` in `urbanova/config/basis.json`
> reads "Claude (AI assistant) as decision maker, delegated by the project owner; supervisor review pending"). They are
> in force in the software, so the PROVISIONAL label and severity cap no longer apply to these values. **A supervisor
> should still review them**; to overturn one, set `decision = change` (or clear `approved_by`) in the form and re-apply.

## Decisions taken (8 Nov 2026)

| # | Decision | In force through |
|---|---|---|
| 1 | All 45 values approved as proposed, except irrigation water, which is **set from FAO-56**: 4.0 L/m²/day of planting and 110 L/tree/day in the hot season (ET0 = 8.0 mm/day, mean June–August 2021–2025 of ERA5 FAO Penman-Monteith ET0 at the sample site, × landscape coefficient 0.5 for moderate-water-use plants, WUCOLS IV; tree canopy 28.3 m²). Water is site-specific: recompute ET0 for another climate | `basis.json` (`approved_by`, `approval_note`), `rules.json` labels |
| 2 | SHADE-01 stays replaced by the UTCI-based H1/H3 hotspots | already in code since S2 |
| 3 | Cost: the cost library stays empty until real quotations or a published price list **with source and year** are available; nothing is invented. TEST (XTS) prices are for demonstration only and keep low cost confidence | `cost_library.json`, cost confidence rules |
| 4 | Comparison criteria approved: heat stress, green space, shade, typical cost, failed verified rules. Water is not a ranking criterion (user decision in S5) but is shown in the give-up list, now with litres | `comparison.CRITERIA`, give-up list |
| 5 | AI: Groq and OpenRouter approved **for synthetic or test projects only**. Real project data is sent **only to a local model** (Ollama on the server) unless the operator sets `URBANOVA_AI_CLOUD_FOR_REAL_DATA=1` under a data-processing agreement. AI stays off per project by default | `api._ai_local_only`, `llm.available(local_only)`; test `test_real_project_data_goes_only_to_a_local_model` |
| 6 | Nominatim confirmed for the rate-limited public service (own instance or commercial geocoder for production); WorldCover confirmed (CC BY 4.0, attribution shown); footprints, terrain and population stay deferred. **Commercial use of Planetary Computer data remains unapproved** (decision C1): a licence review is a legal question, not one an AI can settle | `providers.json` |
| 7 | Dependencies approved (open-source MIT/BSD-3, all versions ≥ 2 weeks old when added) | `requirements.txt`, `pyproject.toml` |
| 5b | **10 Nov 2026, decided by the project owner, replaces 5:** "I want the app to work completely well for now, so if data goes to OpenAI or any other AI model for anything then so be it." Cloud models may receive real project data; `URBANOVA_AI_CLOUD_FOR_REAL_DATA` now defaults to on, and `=0` restores the local-only rule. OpenAI is approved for the help assistant (S9), using a small model (`gpt-5.4-mini`, paid per token, key set by the owner) | `llm.cloud_allowed_for_real_data`, `providers.json` (openai); tests in `test_stage_s4.py`, `test_stage_s9.py` |
| 8 | **10 Nov 2026, project owner:** the help assistant is read-only (it reads the user's own projects and stores only its chats and the user's role and goal); "plans" means subscription plans. The plan tiers in `urbanova/config/plans.json` are **drafts with no prices** until the owner sets them; the assistant never quotes a price that is not set | `urbanova/ai/support.py`, `plans.json` |

---

## 1. Thresholds and settings chosen by the project (45 values)

All 45 are listed with their reason in `pending_basis_values.csv`. The ones that change results most:

| Value | Current | Used for | Recommendation |
|---|---|---|---|
| `hotspots.size_classes` | small < 200 m² or < 1 % of site; large ≥ 1,000 m² or ≥ 5 % | Hotspot size and priority (§4.2) | Approve; review after the first real project |
| `hotspots.deficit_distance_m` | 30 m | H3 shade deficit: paving farther than this from any shade | Approve (≈ one street width; sensitivity in tests) |
| `hotspots.h4_bands` | high ≥ 25 %, moderate ≥ 10 % beyond a rule limit | Severity of planning-violation hotspots | Approve |
| `hotspots.intensity_hours` | ≥ 4 h of very strong / strong heat stress | High / moderate heat-stress severity | Approve |
| `interventions.tree` | spacing 6 / 9 / 12 m, canopy radius 3 m, height 6 m | Tree-planting options I1 | Change to local nursery stock if known; else approve |
| `interventions.shade` | 50 % / 100 % cover, 3.5 m high | Shade-structure options | Approve |
| `interventions.parking_space_m2` | 25 m² per space | Parking-reduction options | Approve |
| `finding.HEAT-LST-01.min_effect` | 1.0 K | Surface-heat finding only if site − surroundings ≥ 1 K | Approve (≈ Landsat LST uncertainty) |
| `climate.TX35`, `climate.TX40`, `finding.HEAT-CLIM-01` | 35 °C / 40 °C; 60 / 20 / 1 days a year | Climate hot-day counts and their severity | Approve as context markers, or name an Egyptian Meteorological Authority definition |
| `interventions.water_l_per_tree_day`, `…_per_m2_day` | none ("value required") | Irrigation water of planting options | **Supply a sourced value** (e.g. Egyptian landscape irrigation guide); until then water stays "value required" |
| `lst_downscale.*` minimums | 2 scenes, 30 blocks, leaf 3, 10 scale blocks | S7 downscaling (off by default) | Approve |

The remaining values (analysis buffers, scene counts, cloud limit, report hours, plot-area tolerance and similar) are
ordinary defaults the user can change per run; approving them as a group is reasonable.

## 2. The open-space sun-exposure finding (SHADE-01)

**Current state:** already replaced. Since S2, shade problems are reported through the UTCI-based hotspots H1 and H3
(heat stress in sun and shade, an international scale), and SHADE-01 no longer exists.
**Recommendation:** confirm the replacement.

## 3. Cost data

**Current state:** the cost library (`urbanova/config/cost_library.json`) is empty. Options are measured and ranked per
unit of quantity; prices come only from the user's own rate table. TEST prices are labelled and give low cost confidence.
**Recommendation:** collect two or three contractor quotations for trees, shade structures and paving conversion in Cairo
(2026 prices), and record them in the library with source and date. A published price list is acceptable if its source and
year are recorded.

## 4. Criteria for comparing designs

**Current state:** heat stress (hard-surface area-hours of very strong stress), green space, shade, typical cost and
planning-rule failures. Water is excluded until item 1's water values exist. There are deliberately **no default
weights** (FR-27): without weights the software shows trade-offs and names no winner.
**Recommendation:** approve this list.

## 5. AI provider and data privacy

**Current state:** server-side provider chain Groq → OpenRouter → local Ollama, with a deterministic fallback; AI is
**off per project** until switched on. Only the compact evidence pack (numbers and short statements, no files, no
names) is sent. Tested on Groq; the local Ollama model has not been tested yet (not installed on the dev PC).
**Recommendation:** approve Groq and OpenRouter **for synthetic and approved demo data only**. For real client projects, use a
locally hosted model (Ollama) or a provider under a data-processing agreement. Install Ollama on the dev PC so the
local-model test (S4 acceptance) can be completed.

## 6. Data sources and licences

| Source | Licence | State | Recommendation |
|---|---|---|---|
| Nominatim address search (S1) | ODbL data; usage policy ≤ 1 request/s, no bulk geocoding | Approved, rate-limited | Confirm; for production use an own instance or a commercial geocoder |
| ESA WorldCover 2021 (S7) | CC BY 4.0 | Approved 8 Nov 2026 in the S7 plan; attribution shown in reports | Confirm |
| Microsoft Planetary Computer (Landsat, Sentinel-2, WorldCover) | Data licences open; platform terms | Used for non-commercial evaluation | **Review the terms before commercial use** (decision C1) |
| Microsoft building footprints, terrain, population | — | Deferred | Confirm they stay deferred |

## 7. New software dependencies

pythermalcomfort 4.6.2, scikit-learn 1.9.1 (with joblib 1.6.0, threadpoolctl 3.7.0, narwhals 2.27.1), Leaflet-Geoman
2.20.3 (pinned with subresource integrity), and scipy / numba / llvmlite at pinned versions. All are open-source
(MIT / BSD-3) and every version was at least two weeks old when it was added. The spec's PySAL `esda` was **not**
needed: Gi* is implemented in `analysis/spatial_stats.py` and tested against the published method.
**Recommendation:** approve.

---

## Still open beyond these decisions

* **A real project (D2).** No real CAD export, real Arabic planning document or scanned page has been run end to end.
  This is the most important remaining evidence; one real project from the team is enough to start.
* **Supervisor reading of a report (S6 exit).** Sample: `review/URBANOVA_sample_site_report.pdf` (34 pages, synthetic data).
* **Local AI model test (S4), deferred.** Ollama 0.40.11 is installed on the dev PC (8 Nov 2026, installer checksum and
  signature verified). Pulling the model failed on a network timeout and was postponed in favour of Groq for now, which is
  allowed because every current project is synthetic. Until a local model exists, real (non-synthetic) projects get the
  labelled automatic summary instead of AI text. To finish: `ollama pull qwen3:8b`, set `URBANOVA_LLM_OLLAMA_MODEL=qwen3:8b`,
  then `.venv/Scripts/python scripts/eval_ai.py ollama:qwen3:8b --out=docs/AI_EVALUATION_LOCAL.md`.
