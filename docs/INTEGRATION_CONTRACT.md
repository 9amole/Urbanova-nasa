# URBANOVA integration contract (for the backend team)

**Contract version 1.3, as of 13 Nov 2026.** This document lists what the backend can call, what goes in and what
comes back. For the reasons behind it, ownership and operations, see [HANDOFF.md](HANDOFF.md). For the methods, see
[METHODOLOGY.md](METHODOLOGY.md).

The machine-readable source of truth is:
- `urbanova/engine/contract.py` (Pydantic models);
- the JSON Schemas generated from it, `schemas/analysis_request.schema.json` and `schemas/analysis_result.schema.json`.

A test fails if these schemas fall out of date. Where this document and the schemas differ, the schemas win.

---

## 1. What you integrate

| Surface | Call | Needs network | Needs a database |
|---|---|---|---|
| Plan import | `urbanova.engine.extract_plan(filename, bytes, site, epsg=None)` | no | no |
| Planning-document import | `urbanova.engine.extract_rules(filename, bytes)` | no | no |
| **Analysis** | `urbanova.engine.analyse(request, output_dir)` | yes (open data providers) | no |
| AI insights | `urbanova.ai.generate_insights(bundle, ai_allowed, local_only)` | AI provider | no |
| AI single question | `urbanova.ai.answer_question(bundle, question, ai_allowed, local_only)` | AI provider | no |
| AI chat turn | `urbanova.ai.chat_turn(bundle, question, history, ai_allowed, local_only, comparison)` | AI provider | no |
| Help assistant | `urbanova.ai.support.turn(user, text, history, page, project_id)` | AI provider (free Groq pool by default) | yes: reads users and projects (§5b) |
| Result → AI input | `urbanova.ai.bundle_from_result(result, design_id)`, `comparison_from_result(result)` | no | no |

For callers outside Python, the same engine calls run from the command line. They read and write JSON, and the exit
codes are 0 for success, 2 for invalid input (the errors are printed as JSON) and 1 for an unexpected error:

```bash
python -m urbanova.engine analyse request.json --out out/ --result out/result.json
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

You own users, authentication, roles, projects, file storage, the result database, the job queue and the UI. The engine
owns analysis, input validation of analysis requests, and provider retries and caching inside a call.

## 2. End-to-end flow

```
upload plan ──► extract_plan ──► person reviews features ──┐
upload PDF  ──► extract_rules ─► person verifies rules ─────┤
enter unit rates (quoted only) ─────────────────────────────┤
                                                            ▼
                         build AnalysisRequest (1 baseline + ≤ 5 alternatives)
                                                            │   background worker, 1–2 min with live data
                                                            ▼
                                   analyse(request, output_dir) ──► AnalysisResult + files in output_dir
                                                            │
                     store result; serve layers from output_dir; show the provenance and limitations
                                                            │
             bundle_from_result(result, design_id) ──► generate_insights / chat_turn (optional, AI)
```

1. **Plans.**
   - `extract_plan` returns `{"detected_type", "warnings", "features": FeatureCollection, "summary"}`.
   - Show every feature's `review_status`, `classification_confidence` and `issues` to a person.
   - A DXF without declared units is flagged `units_unconfirmed`. `analyse()` refuses those features until a person
     confirms metres and you drop the flag.
   - PDF and image plans are reference-only.
2. **Rules.**
   - `extract_rules` returns `{"pages", "pages_with_text", "candidates", "conflicts", "issues"}`. Each candidate keeps
     its exact wording and page.
   - Only rules a person has verified go into the request (`rules.verified`).
   - Send the number of still-unverified candidates (`rules.unverified_count`) and any unresolved conflicting plot
     areas.
3. **Rates.** Use quoted rates only. The engine never invents a price; a quantity without a rate comes back as
   `price_required`.
4. **Analyse.**
   - Run it in a worker, not in a web request.
   - Give each call its own `output_dir`. Calls are safe in parallel threads or processes.
5. **Store and present** (§4 lists the rules the UI must follow).
6. **AI text** (optional, §5) runs on the stored result. Analysis never depends on it.

## 3. Request — `AnalysisRequest`

```jsonc
{
  "contract_version": "1.1",            // "1.0" is still accepted
  "request_id": "your-id",              // optional; echoed back
  "site": {
    "latitude": 30.12, "longitude": 31.85,               // WGS 84
    "boundary": { /* GeoJSON Polygon/MultiPolygon or Feature/FeatureCollection holding one */ },
    "verified_site_area_m2": null,                       // plot area from a verified document, overrides the boundary area
    "climate_period": { "start_year": 2020, "end_year": 2024 }  // default: last 5 complete years
  },
  "objectives": ["main_concerns"],      // main_concerns | surface_heat | vegetation | shade | compare_alternatives | planning_compliance | data_gaps
  "designs": [                          // 0 = site-only; at most 1 baseline + 5 alternatives
    { "id": "baseline", "name": "Original design", "kind": "baseline",
      "features": { "type": "FeatureCollection", "features": [ /* properties.feature_type required */ ] } }
  ],
  "rules": {
    "verified": [{ "rule_type": "min_green_space_pct", "value": 20,
                   "source": { "document": "Planning conditions", "page": 3, "text": "exact wording" } }],
    "unverified_count": 0,
    "conflicting_plot_areas_m2": []
  },
  "rates": [{ "quantity_type": "trees_added_count", "unit": "tree", "rate": 1500, "currency": "EGP",
              "price_date": "2026-10-01", "source": "Supplier quote 123" }],
  "settings": { "modules": null, "lst_downscale": false, "budget_amount": null, "budget_currency": null },
  "compare": { "baseline_id": "baseline", "alternative_ids": ["alt-1"], "criteria": null }
}
```

**Value sets:**
- `feature_type`: site_boundary, building, road, green_space, tree, parking, open_space, water, shade_structure,
  land_use_zone, unclassified.
- Optional feature properties: `height_m`, `floors`, `canopy_radius_m` (point trees), `name`, `land_use`.
- `rule_type`: max_building_coverage_pct, max_height_m, max_floors, min_setback_m, min_green_space_pct,
  min_open_space_pct, max_far, plot_area_m2, permitted_land_use, parking_requirement.
- `quantity_type`: green_space_added_m2, hard_surface_removed_m2, trees_added_count, shade_structure_added_m2,
  building_footprint_removed_m2, cool_paving_m2.
- Currency: ISO 4217. `XTS` marks test prices, and every report page is then labelled synthetic.

**Limits:** 6 designs, 5,000 features per design, and a bounded vertex count. Unknown fields are refused: the models
forbid extras. A complete example is in `examples/engine_request_sample.json`.

**Invalid input** raises `EngineInputError`. Its `.errors` is a list of `{"loc": [...], "msg": "..."}`. Map it to
HTTP 422 and show the messages. Nothing is analysed when the request is invalid.

## 4. Result — `AnalysisResult`

```jsonc
{
  "contract_version": "1.1", "engine_version": "…", "analysis_version": "…",
  "request_id": "your-id", "input_fingerprint": "16 hex", "generated_at": "ISO 8601",
  "usage_context": "non_commercial_evaluation",
  "status": "succeeded | partially_succeeded | failed",
  "designs": [ DesignResult, … ],       // same order as the request; one entry with design_id null for a site-only run
  "datasets": [ { "id", "source_id", "license", "attribution", "approval_label", … } ],
  "provider_requests": [ … ],           // one per outbound request or cache hit; secrets redacted
  "comparison": { "comparable", "rows", "choice", "tradeoffs", "hotspots", "costs", … } | null,
  "warnings": [ … ],
  "limitations": [ … ]                  // apply to every result of this build
}
```

**`DesignResult`** contains:
- `modules`: map of module → `{status, label, reason, warnings, limitations, details, provider_failures, shared_across_designs}`;
- `metrics`, `findings`, `recommendations`, `layers`, `objectives`, `warnings`;
- `hotspots`, `site_context` and `package` (added in 1.1).

| Object | Key fields |
|---|---|
| Metric | `id`, `metric_name`, `label`, `state`, `value`, `unit`, `reason`, `output_kind`, `condition` (existing / design), `method`, `source_dataset_ids`, `quality` |
| Finding | `id`, `classification`, `title`, `summary`, `severity` (high / moderate / low / info), `severity_rule`, `threshold`, `affected_area`, `geometry`, `evidence`, `sources`, `confidence`, `limitations`, `hotspot_ids` |
| Hotspot | `id`, `number` (priority: severity, then area), `types` (H1 sun-exposed hard surface, H3 shade deficit, H4 planning violation), `geometry` (null when `site_wide`), `area_m2`, `severity`, `severity_capped`, `likely_causes[]` (`code`, `text`, `evidence_ids`), `supporting_metrics[]` |
| Recommendation | `id`, `finding_id`, `hotspot_id`, `intervention_id` (I1–I7), `expected_direction` (direction only, never a predicted number), `options[]` (effect before → after, cost range or "price required", effect and cost confidence, roles: best_value / largest_benefit / cheapest_effective), `literature` |
| Layer | `id`, `module`, `name`, `output_kind`, `file` (relative to `output_dir`: PNG overlay in Web Mercator with WGS 84 `bounds`, or GeoJSON), `legend`, `meta.geotiff` (analysis-grade GeoTIFF, local UTM) |

### Site analysis (contract 1.2)

A request with **no designs** is a **site analysis** of the land as it is, for a plot before any design exists.
- The result has one entry in `designs`, with `design_id: null` and **`analysis_mode: "site"`**; every other entry
  has `analysis_mode: "design"`.
- The design-only modules (`urban_form`, `planning_compliance`, `hotspots`, `interventions`) have status `skipped`
  with **`site_mode: true`** and a reason saying a design plan is needed. They are not failures, so a complete site
  analysis has `status: "succeeded"`.
- Heat stress gives the UTCI in full sun and in shade (existing-land values) without any design.
- To show a site analysis, present the existing-land results, and invite the user to add a design for hotspots,
  options and costs.

### Rules the UI must follow

1. **Missing is never zero.** A metric has a value only when `state` is `available`. Otherwise `value` is null and
   `reason` says why. Never show a missing value as 0.
2. **Show `output_kind`** (observed, reanalysis, calculated, classified, modelled, user_provided), and the condition:
   *existing* land, or *proposed design*. Only design values differ between designs.
3. **Provisional thresholds.** When `threshold.status` is `provisional_not_approved` or `severity_capped` is true, label
   the finding as provisional. Its severity is capped at moderate.
4. **Regional climate findings have no geometry.** Never draw them on the site.
5. **Module outcomes.** A provider outage never raises; it shows up as a module `status` (`unavailable`, `blocked`,
   `failed`, …) with a `reason`. Show it, and show `partially_succeeded` runs as such.
6. **Licences.** Show every dataset's `attribution` (required by the licences) and its `approval_label`.
7. **Reproducibility.**
   - `input_fingerprint` is identical for identical inputs, ignoring `request_id`.
   - Results with a different `analysis_version` are out of date and must not be compared.
8. **No predictions.** The engine gives directions and measured geometric effects, never a predicted temperature or
   comfort change. Don't add one in the UI.

## 5. AI service (optional)

**Input.** The AI functions read a *run bundle*. Build it from a stored result with
`bundle_from_result(result, design_id=None)` (`None` means the baseline). The chat's compare tool reads
`comparison_from_result(result)`.

```python
from urbanova.ai import bundle_from_result, comparison_from_result, generate_insights, chat_turn

bundle = bundle_from_result(result, "baseline")
insights = generate_insights(bundle, ai_allowed=project.ai_enabled, local_only=not project.synthetic)
reply = chat_turn(bundle, question, history, ai_allowed=project.ai_enabled, local_only=not project.synthetic,
                  comparison=comparison_from_result(result))
```

**Outputs.** Every output carries `generated_by`, a `label` to show next to the text, and `based_on` (evidence ids:
metric, finding, hotspot, option or comparison ids from the result). Use those ids to link the text to the item on
screen.

| Function | Returns |
|---|---|
| `generate_insights` | `{blocks: {site_status, top_issues, attention_first, limitations, answers}, generated_by: "ai" \| "fallback", label, provider, model, reason, attempts, pack_hash}`. Each block is a list of claims `{text, claim_type, evidence_ids, code?}`. |
| `answer_question` | `{answer, claims, based_on, generated_by: "ai", …}`, or **`None`**: then show the automatic answer from `urbanova.explain.answer(question, bundle)`. |
| `chat_turn` | `{answer, generated_by: "ai" \| "fallback" \| "rule", label, claims, based_on, tool_calls, reason?, refusal?}`. `refusal` is `"prediction"` or `"read_only"`. |

**Chat history** is `[{"role": "user" | "assistant", "text": str}, …]` in order; only the last 6 turns are sent. You
store the conversation. The prototype's `chat_messages` table is a reference: `run_id`, `seq`, `role`, `text`,
`claims`, `based_on`, `tool_calls`, `generated_by`, `label`, `provider`, `model`, `reason`, `created_by`,
`created_at`.

**Guarantees:**
- Every AI answer has passed the grounding validator: each number appears in the cited evidence, and every cause comes
  from the hotspot's cause list. If validation fails twice, the deterministic automatic text is returned instead,
  labelled as such.
- The chat is read-only. Its tools are `list_hotspots`, `get_hotspot`, `get_metric` and `compare`, and they read only
  the given bundle. Change requests and temperature predictions are refused before any model call.
- **Data rule:**
  - since 10 Nov 2026 cloud models may receive real project data (`URBANOVA_AI_CLOUD_FOR_REAL_DATA` defaults to on);
  - with `URBANOVA_AI_CLOUD_FOR_REAL_DATA=0`, pass `local_only=True` for real (non-synthetic) projects: only a local model
    (Ollama) is used, and with none the automatic text is returned.
- `ai_allowed=False` never calls a model.
- Results are cached by (evidence hash, question, history, model, prompt version), so the same input gives the same
  text.

### 5b. Help assistant (S9)

A support chat available on every page. It knows the signed-in user (their stored role and goal), the page they are on,
the FAQ, the subscription plans and the user's own projects. It is read-only.

| Call | Purpose |
|---|---|
| `urbanova.ai.support.turn(user, text, history, page, project_id=None, ai_allowed=True)` | One reply: `{answer, generated_by: "ai" / "fallback" / "rule", label, links: [{label, href}], suggest_plan, tool_calls, reason?, refusal?, faq_ids?}` |
| `urbanova.ai.support.search_faq(query)`, `pages()`, `plans()` | The FAQ, page help and plans (`urbanova/config/support_faq.json`, `support_pages.json`, `plans.json`) |

- **User:** `user` is `{"id", "name", "email"}`. The tools read the user's projects through `project_members` only, so
  the assistant never sees another user's project. The prototype keeps `support_role` / `support_goal` on `users`.
- **Tools:** `get_me`, `list_my_projects`, `get_project`, `get_page_help`, `search_faq`, `list_plans`.
- **Page keys:** `login`, `projects`, `overview`, `inputs`, `rules`, `readiness`, `scenarios`, `analysis`, `results`,
  `compare`, `report`.
- **Links** in replies are in-app hrefs (`#/…`) only; anything else is dropped.
- **Projects with AI switched off** are shown to the model by name and run status only; their details stay out of the prompt.
- **Plans:** a plan with a null price is never quoted, and a reply containing a price is rejected while prices are
  unset. `suggest_plan` is a plan id or null.
- **No provider** (no key, over the budget or the per-user cap): the best FAQ match or the page help is returned,
  labelled `fallback`.
- **Prototype endpoints:**
  - `GET /support/faq?q=`, `GET /support/plans`, `GET /support/pages/{page}` (public);
  - `GET/PUT /support/profile`;
  - `GET /support/conversations`, `GET /support/conversations/{id}` (the user's own only);
  - `POST /support/messages` `{text, conversation_id?, page, project_id?, run_id?}` → 201 (the open project and run are used only when the user is a member).
- **Tables:** `support_conversations` (`id`, `user_id`, `title`, `created_at`, `updated_at`) and `support_messages`
  (`conversation_id`, `user_id`, `seq`, `role`, `text`, `page`, `links`, `tool_calls`, `suggest_plan`,
  `generated_by`, `provider`, `model`, `reason`, `created_at`).

**AI suggestions while importing** (planning rules from a PDF, feature types for CAD layer names) exist only in the
prototype app (`urbanova/ai/suggest.py`), because they read its database. They are not part of this contract yet.

## 6. Configuration (server environment only)

| Variable | Purpose | Default |
|---|---|---|
| `URBANOVA_DATA_DIR` | Provider cache (and the AI cache and budget state) | `./data` (source) or `~/.urbanova` (installed) |
| `URBANOVA_CONFIG_DIR` | Use a reviewed copy of the method configs | the copy inside the package |
| `URBANOVA_USAGE_CONTEXT` | `non_commercial_evaluation` or `commercial` (commercial refuses sources with unverified terms) | `non_commercial_evaluation` |
| `URBANOVA_OFFLINE` | `1` = no provider or AI calls | `0` |
| `OPEN_METEO_API_KEY` | Open-Meteo customer endpoint for commercial use | unset |
| `URBANOVA_HTTP_TIMEOUT_S`, `URBANOVA_HTTP_MAX_RETRIES` | HTTP client | 45, 3 |
| `URBANOVA_LLM_PROVIDERS` | AI provider order | `groq,openrouter,ollama` |
| `URBANOVA_LLM_<NAME>_BASE_URL`, `_MODEL` or `_MODELS` (a pool), `_API_KEY` | OpenAI-compatible endpoint per provider (no key for a local Ollama). In a pool, a rate-limited model hands over to the next at once | see `urbanova/ai/llm.py` |
| `URBANOVA_LLM_ENABLED`, `URBANOVA_LLM_TIMEOUT_S`, `URBANOVA_LLM_MAX_CALLS_PER_DAY` | AI switch, timeout, daily call budget | `1`, 30, 500 |
| `URBANOVA_AI_CLOUD_FOR_REAL_DATA` | Allow cloud AI for real projects (`0` = local model only) | `1` |
| `URBANOVA_SUPPORT_PROVIDERS`, `URBANOVA_SUPPORT_GROQ_MODELS`, `URBANOVA_SUPPORT_MAX_PER_USER_DAY`, `URBANOVA_SUPPORT_MAX_CALLS_PER_DAY` | Help assistant provider order and Groq pool; AI answers per user per day; its own daily call budget | `groq`, see HANDOFF, 100, 1000 |

**Outbound hosts:**
- `archive-api.open-meteo.com` (and the customer host when a key is set);
- `power.larc.nasa.gov`;
- `overpass-api.de` (use your own instance in production);
- `planetarycomputer.microsoft.com` and `*.blob.core.windows.net`;
- the AI provider hosts, if AI is used.

### Long-term climate trends (contract 1.3)

Module **`climate_trends`** (A1b) is new. It is an existing-land, regional module that runs once per request and is
shared by every design.
- **Metrics:** `trend_<var>_per_decade` for `t2m_max`, `t2m`, `t2m_min`, `precip`, `rh` and `sun`.
  - Unit: the variable's unit per decade.
  - `uncertainty.ci95_low` / `ci95_high`.
  - `extra.verdict`: `significant`, `borderline` or `not_significant`.
  - `extra.direction` and `extra.total_change`.
- **`details.rows[]`:** the yearly series, the trend line, the corrected and plain p-values, the effective years, the
  change point and the notes.
- **Finding:** a significant warming adds an informational finding (`rule_id: "TREND-WARM-01"`, category `trend`).
- **Version change:** additive, so requests marked 1.0–1.2 are still accepted.

## 7. Versioning

- `contract_version` follows major.minor: a breaking change raises the major number, an addition raises the minor.
  Requests marked 1.0 are still accepted, and 1.1 only added result fields.
- Ignore result fields you don't know; new ones may appear in minor versions.
- `analysis_version` changes whenever a method changes. Store it with every result.
- The AI outputs carry `prompt_version` / `pack_hash` for traceability.

## 8. Reference implementation

The prototype app (`urbanova/api.py`, `web/`) is a working example of every flow above. It uses FastAPI with SQLite,
and its endpoints are under `/api/v1`:

| Area | Endpoints |
|---|---|
| Projects | `POST/GET /projects`, `GET/PATCH /projects/{pid}`, `GET /projects/{pid}/project-dashboard` (S10: every important output on one page), `POST /projects/{pid}/confirm-location`, `/site-area-basis`, `/members`, `/readiness-check`, `GET /projects/{pid}/dashboard` |
| Inputs | `POST /projects/{pid}/uploads`, `GET /projects/{pid}/files`, `POST /files/{fid}/confirm-units`, `GET /projects/{pid}/features`, `PATCH /features/{fid}`, `GET/POST /projects/{pid}/rules`, `POST /rules/{rid}/decision`, `GET/POST /projects/{pid}/rates` |
| Scenarios | `GET/POST /projects/{pid}/scenarios`, `GET/PATCH /scenarios/{sid}`, `POST /scenarios/{sid}/import`, `/approve`, `/archive`, `POST /analysis-runs/{rid}/options/{oid}/draft`, `/package/draft` |
| Runs | `POST /projects/{pid}/analysis-runs`, `/analysis-runs/all-scenarios`, `GET /jobs/{jid}`, `GET /analysis-runs/{rid}` (+ `/metrics`, `/findings`, `/recommendations`, `/export`), `GET /layers/{lid}/data` |
| Compare and report | `POST/GET /projects/{pid}/scenario-comparisons`, `GET /scenario-comparisons/{cid}`, `POST /analysis-runs/{rid}/reports`, `GET /reports/{rpid}/download` |
| AI | `POST/GET /analysis-runs/{rid}/insights`, `POST /analysis-runs/{rid}/ask`, `GET/POST /analysis-runs/{rid}/chat`, `POST /files/{fid}/suggest-rules`, `/suggest-layers` |

It is a reference, not the integration target: its accounts, storage and UI are prototype-level.

## 9. Known limits that affect integration

- No real project has been validated yet (`docs/VALIDATION.md`), and commercial licensing is not cleared (decision C1).
- The chat evaluation of at least 30 questions is not yet complete on the free Groq tier
  ([CHAT_EVALUATION.md](CHAT_EVALUATION.md)). Every AI answer is still validated or replaced by the automatic text.
- The subscription plans are drafts without prices (`urbanova/config/plans.json`), and the FAQ needs a content review.
