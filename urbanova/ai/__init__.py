"""Server-side AI service (PHASE2_SPEC §5, §9): evidence pack, provider chain, grounding validator, deterministic fallback.

For an integrating backend: `generate_insights(bundle, ai_allowed)` returns validated insight blocks for a run bundle
(see urbanova.orchestrator.run_bundle for the keys), labelled generated_by "ai" or "fallback"; `answer_question(bundle,
question, ai_allowed)` answers a free-text question the same way. Analysis results never depend on these; the engine's
analyse() makes no model calls.
"""

from .insights import answer as answer_question  # noqa: F401
from .insights import generate as generate_insights  # noqa: F401
