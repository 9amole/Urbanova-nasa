"""URBANOVA analysis engine — the component handed to the backend for integration.

    from urbanova.engine import analyse, extract_plan, extract_rules
    result = analyse(request_dict, output_dir="out/")

No database, web framework or user accounts are involved. The request/result contract is
defined in `urbanova.engine.contract` and exported as JSON Schema in `schemas/`.
See docs/HANDOFF.md.
"""

from typing import Any

__all__ = ["CONTRACT_VERSION", "AnalysisRequest", "AnalysisResult", "EngineInputError", "analyse", "extract_plan", "extract_rules"]


def __getattr__(name: str) -> Any:
    # Lazy exports: urbanova.engine.core is imported by the prototype app's orchestrator, and
    # urbanova.engine.api imports the orchestrator, so eager imports here would be circular.
    if name in ("EngineInputError", "analyse", "extract_plan", "extract_rules"):
        from . import api

        return getattr(api, name)
    if name in ("CONTRACT_VERSION", "AnalysisRequest", "AnalysisResult"):
        from . import contract

        return getattr(contract, name)
    raise AttributeError(name)
