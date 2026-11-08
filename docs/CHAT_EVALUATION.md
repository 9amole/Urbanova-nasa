# Chat assistant evaluation (S8) — interim

**Status: incomplete.** The exit criterion (PHASE2_SPEC §11 S8: grounded answers on ≥ 30 questions; correct refusals)
is **not yet confirmed**. `scripts/eval_chat.py groq:openai/gpt-oss-120b` runs the 36 fixed questions and regenerates
this file when it finishes. On 8 Nov 2026 the Groq free tier's daily token allowance for `openai/gpt-oss-120b` ran out
after a few questions; the provider was then paused for about 30 minutes after each question, so a full run takes about
a day. The script caches every accepted answer in `data/eval_chat/` and resumes where it stopped.

## What is confirmed so far (prompt version chat-3)

| Group | Reached the model | Pass | Fail | Not reached (free-tier cap) |
|---|---|---|---|---|
| Answerable, sample site, questions 1–10 | 5 | 4 (questions 2, 4, 7, 9) | 1 (question 1: the answer failed validation twice; the automatic answer was shown) | 5 |
| Must refuse | — | the prediction and change requests (questions 25–28, 34) are refused by fixed rules before any model call; covered by `tests/test_stage_s8.py` | — | — |

Every AI answer that was shown passed the grounding validator; a failed validation never reached the user as AI text.

## Offline checks (no model)

`tests/test_stage_s8.py` (18 tests, faked provider) covers:
- the tool loop;
- rejection of a number that is not in the evidence, with one retry and then the fallback;
- unknown tools and ids from another run;
- the tool-call cap;
- refusals without a model call;
- follow-up history;
- storage per run, and viewers' read-only access;
- the local-only rule for real projects;
- the automatic answer to hotspot questions.

## Known issue

"Which hotspot should be addressed first, and why?" failed validation on both runs that reached the model (sample
site). The user then gets the automatic answer, which lists the hotspots in priority order with their likely causes.
To look at once the full evaluation has run.
