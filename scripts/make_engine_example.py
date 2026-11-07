"""Build examples/engine_request_sample.json from the synthetic samples (all data is SYNTHETIC).

The request mirrors the prototype demo: a baseline design, an alternative without the surface car
park and with trees, a shade canopy and a garden, the rules a reviewer verified from the sample
planning PDF, and the TEST-ONLY unit rates (currency XTS).

Run: .venv/Scripts/python scripts/make_engine_example.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "samples"


def main() -> Path:
    boundary = json.loads((S / "sample_site_boundary.geojson").read_text(encoding="utf-8"))
    base = json.loads((S / "sample_plan_baseline.geojson").read_text(encoding="utf-8"))
    adds = json.loads((S / "sample_alternative_additions.geojson").read_text(encoding="utf-8"))
    alt_features = [f for f in base["features"] if f["properties"].get("name") != "Surface car park"] + adds["features"]
    with open(S / "sample_rates_TEST_ONLY.csv", encoding="utf-8") as fh:
        rates = [{"quantity_type": r["quantity_type"], "unit": r["unit"], "rate": float(r["rate"]), "currency": r["currency"],
                  "price_date": r["price_date"], "source": r["source"], "region": r.get("region") or None} for r in csv.DictReader(fh)]
    doc = "sample_planning_conditions.pdf (SYNTHETIC)"
    request = {
        "contract_version": "1.1",
        "request_id": "example-synthetic-site",
        "site": {"latitude": 30.12, "longitude": 31.85, "boundary": boundary},
        "objectives": ["main_concerns", "surface_heat", "vegetation", "shade", "planning_compliance", "compare_alternatives"],
        "designs": [
            {"id": "baseline", "name": "Baseline (original design)", "kind": "baseline", "features": {"type": "FeatureCollection", "features": base["features"]}},
            {"id": "alt-greener", "name": "Greener with shade", "kind": "alternative", "features": {"type": "FeatureCollection", "features": alt_features}},
        ],
        "rules": {
            "verified": [
                {"rule_type": "max_building_coverage_pct", "value": 35, "source": {"document": doc, "page": 1, "text": "Building coverage shall not exceed 35% of the net plot area."}},
                {"rule_type": "max_height_m", "value": 18, "source": {"document": doc, "page": 1, "text": "Maximum building height: 18 m above finished ground level."}},
                {"rule_type": "min_setback_m", "value": 6, "source": {"document": doc, "page": 1, "text": "A minimum setback of 6 m shall be maintained from all plot boundaries."}},
                {"rule_type": "min_green_space_pct", "value": 20, "source": {"document": doc, "page": 2, "text": "Green space shall be at least 20% of the plot area."}},
                {"rule_type": "plot_area_m2", "value": 40000, "source": {"document": doc, "page": 1, "text": "The plot area is 40,000 m2 as recorded in the allocation schedule."}},
            ],
            "unverified_count": 4,
        },
        "rates": rates,
        "settings": {},
        "compare": {"baseline_id": "baseline", "alternative_ids": ["alt-greener"],
                    "criteria": [{"metric": "green_space_pct", "weight": 2}, {"metric": "solar_open_space_shade_hours", "weight": 1}]},
    }
    out = ROOT / "examples" / "engine_request_sample.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    print("wrote", main())
