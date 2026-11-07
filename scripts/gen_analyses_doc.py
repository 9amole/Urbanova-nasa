"""Generate docs/ANALYSES.md from urbanova/config/analyses.json (PHASE2_SPEC §2).

Usage: python scripts/gen_analyses_doc.py   (tests fail if the committed file is out of date)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from urbanova.settings import load_config  # noqa: E402

OUT = ROOT / "docs" / "ANALYSES.md"
COLUMNS = [("spec_id", "Id"), ("name", "Analysis"), ("condition", "Condition"), ("measures", "What it measures"), ("inputs", "Required inputs"),
           ("method", "Method (summary)"), ("outputs", "Main outputs"), ("status", "Status")]


def _cell(value) -> str:
    return str(value).replace("|", r"\|")


def render() -> str:
    rows = load_config("analyses.json")["analyses"]
    lines = [
        "# Analysis mapping",
        "",
        "Generated from `urbanova/config/analyses.json` by `scripts/gen_analyses_doc.py`; edit the JSON, not this file.",
        "**Condition** says whether an analysis describes the existing land (today's site, the same for every design) or the proposed design (PHASE2_SPEC decision S-8).",
        "",
        "| " + " | ".join(h for _, h in COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    lines += ["| " + " | ".join(_cell(r[k]) for k, _ in COLUMNS) + " |" for r in rows]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
