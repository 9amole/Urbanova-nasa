"""Basis records: where every threshold, band and default comes from (PHASE2_SPEC §3.1).

The records live in urbanova/config/basis.json. A value is *provisional* when its basis is
`provisional`, or when it is a `project_choice` nobody has approved yet; provisional values are
labelled and cannot produce the top severity (findings.PROVISIONAL_SEVERITY_CAP).
"""

from __future__ import annotations

from functools import lru_cache

from .settings import load_config

BASIS_TYPES = {"standard", "regulatory", "literature", "statistical", "project_choice", "provisional"}
BASIS_LABELS = {"standard": "International standard", "regulatory": "Verified planning document", "literature": "Peer-reviewed literature",
                "statistical": "Statistical test", "project_choice": "Project choice", "provisional": "Provisional"}


@lru_cache(maxsize=1)
def _records() -> dict[str, dict]:
    return {r["id"]: r for r in load_config("basis.json")["values"]}


def all_records() -> list[dict]:
    return list(_records().values())


def get(value_id: str) -> dict | None:
    return _records().get(value_id)


def is_provisional(value_id: str) -> bool:
    """True when the value has no basis, a provisional one, or an unapproved project choice."""
    return record_is_provisional(get(value_id))


def record_is_provisional(r: dict | None) -> bool:
    return r is None or r["basis_type"] == "provisional" or (r["basis_type"] == "project_choice" and not r.get("approved_by"))


def used_by(modules: set[str]) -> list[dict]:
    """Records for the given modules (plus findings), for the report's 'values used' appendix."""
    wanted = set(modules) | {"findings"}
    return [r for r in all_records() if wanted & {m.strip() for m in r["used_in"].split(",")}]
