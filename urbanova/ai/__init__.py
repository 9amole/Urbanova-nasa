"""Server-side AI service (PHASE2_SPEC §5, §9): evidence pack, provider chain, grounding validator, deterministic fallback.

For an integrating backend (docs/INTEGRATION_CONTRACT.md §5):

    bundle = bundle_from_result(result, "baseline")          # one design of an engine analyse() result
    generate_insights(bundle, ai_allowed, local_only)        # validated insight blocks
    answer_question(bundle, question, ai_allowed, local_only)  # single question, or None → use the automatic answer
    chat_turn(bundle, question, history, ai_allowed, local_only, comparison_from_result(result))  # one chat turn

Every reply is labelled generated_by "ai" | "fallback" | "rule". Analysis results never depend on these; the engine's
analyse() makes no model calls.
"""

from __future__ import annotations

from .chat import turn as chat_turn  # noqa: F401
from .insights import answer as answer_question  # noqa: F401
from .insights import generate as generate_insights  # noqa: F401


def bundle_from_result(result: dict, design_id: str | None = None) -> dict:
    """The run bundle the AI functions read, built from an engine AnalysisResult (dict) for one design.

    design_id None takes the baseline (or the only design / the site-only result)."""
    designs = result["designs"]
    if design_id is None:
        d = next((x for x in designs if x.get("kind") == "baseline"), designs[0])
    else:
        d = next((x for x in designs if x.get("design_id") == design_id), None)
        if d is None:
            raise KeyError(f"No design {design_id!r} in this result")
    run = {"id": f"{result.get('request_id') or result['input_fingerprint']}:{d.get('design_id') or 'site'}", "status": d["status"],
           "module_status": d["modules"], "design_id": d.get("design_id"), "analysis_version": result["analysis_version"]}
    return {"run": run, "metrics": d["metrics"], "findings": d["findings"], "recommendations": d["recommendations"], "hotspots": d["hotspots"],
            "site_context": d["site_context"], "objectives": d["objectives"], "layers": d["layers"], "datasets": result["datasets"]}


def comparison_from_result(result: dict) -> dict | None:
    """The comparison the chat's compare tool reads, from an engine AnalysisResult; None when the request had no compare block."""
    if not result.get("comparison"):
        return None
    names = {d["design_id"]: d["design_name"] for d in result["designs"] if d.get("design_id")}
    return {"id": f"cmp_{result.get('request_id') or result['input_fingerprint']}", "scenario_names": names, "result": result["comparison"]}
