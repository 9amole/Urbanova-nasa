"""Value states and output kinds.

The single most important invariant in URBANOVA (principle D.10): an unknown,
unavailable, invalid, blocked or not-applicable value is never stored or shown as
zero. A genuinely measured or calculated zero has state ``available`` and value 0.
"""

from __future__ import annotations

from typing import Any

# Value states
AVAILABLE = "available"
UNKNOWN = "unknown"            # input exists but the value could not be determined
UNAVAILABLE = "unavailable"    # data source returned nothing usable / does not cover the site
INVALID = "invalid"            # input failed validation
BLOCKED = "blocked"            # prerequisite input missing
NOT_APPLICABLE = "not_applicable"
VALUE_STATES = {AVAILABLE, UNKNOWN, UNAVAILABLE, INVALID, BLOCKED, NOT_APPLICABLE}

# Output kinds (FR-33, B.1 MetricResult.output_kind)
OBSERVED = "observed"          # directly observed / retrieved (satellite, station)
RETRIEVED_MODEL = "reanalysis"  # gridded reanalysis or modelled product retrieved from a provider
CALCULATED = "calculated"      # calculated from source data by a documented method
CLASSIFIED = "classified"      # derived through a documented classification rule
MODELLED = "modelled"          # estimated by a model
USER_PROVIDED = "user_provided"
OUTPUT_KINDS = {OBSERVED, RETRIEVED_MODEL, CALCULATED, CLASSIFIED, MODELLED, USER_PROVIDED}

OUTPUT_KIND_LABELS = {
    OBSERVED: "Observed / retrieved",
    RETRIEVED_MODEL: "Retrieved reanalysis (gridded model data)",
    CALCULATED: "Calculated from source data",
    CLASSIFIED: "Classified by documented rule",
    MODELLED: "Model estimate",
    USER_PROVIDED: "Supplied by the user",
}


def metric(
    name: str,
    label: str,
    value: float | None,
    unit: str | None,
    *,
    state: str | None = None,
    reason: str | None = None,
    method: str = "",
    output_kind: str = CALCULATED,
    sources: list[str] | None = None,
    quality: dict | None = None,
    uncertainty: dict | None = None,
    design_dependent: bool = False,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Build a metric record, enforcing the value-state invariant."""
    if state is None:
        state = AVAILABLE if value is not None else UNKNOWN
    if state not in VALUE_STATES:
        raise ValueError(f"unknown value state {state!r}")
    if output_kind not in OUTPUT_KINDS:
        raise ValueError(f"unknown output kind {output_kind!r}")
    if state != AVAILABLE:
        if value is not None:
            raise ValueError(f"metric {name}: a non-available value must not carry a number")
        if not reason:
            raise ValueError(f"metric {name}: state {state} requires a reason")
    elif value is None:
        raise ValueError(f"metric {name}: available state requires a value")
    return {
        "metric_name": name,
        "label": label,
        "state": state,
        "value": None if value is None else float(value),
        "unit": unit,
        "reason": reason,
        "method": method,
        "output_kind": output_kind,
        "source_dataset_ids": sources or [],
        "quality": quality or {},
        "uncertainty": uncertainty or {},
        "design_dependent": design_dependent,
        "extra": extra or {},
    }


def missing(name: str, label: str, unit: str | None, state: str, reason: str, **kw: Any) -> dict[str, Any]:
    return metric(name, label, None, unit, state=state, reason=reason, **kw)
