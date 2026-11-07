"""D3 (satellite part): plausibility check of the NDVI and LST outputs on land covers with known behaviour.

Runs the engine (site-only requests) at three points whose relative behaviour is well established:
irrigated farmland in the Nile Delta, open water (Lake Qarun) and bare desert (the synthetic sample
point). Expected: NDVI water < desert < farmland (farmland well above 0.2); hot-season daytime LST
water < farmland < desert. This is a plausibility check, not a validation against ground truth.

Run (needs internet):  .venv/Scripts/python scripts/plausibility_satellite.py
Writes validation/satellite_plausibility.json.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from urbanova import recording  # noqa: E402
from urbanova.engine import analyse  # noqa: E402

SITES = [
    {"key": "farmland", "label": "Irrigated farmland, Nile Delta (near Tanta)", "latitude": 30.7650, "longitude": 31.0350},
    {"key": "water", "label": "Open water, Lake Qarun", "latitude": 29.4700, "longitude": 30.6500},
    {"key": "desert", "label": "Bare desert (synthetic sample point)", "latitude": 30.1200, "longitude": 31.8500},
]


def _metric(design: dict, name: str):
    m = next((x for x in design["metrics"] if x["metric_name"] == name), None)
    return m["value"] if m and m["state"] == "available" else None


def main() -> int:
    rows = []
    with tempfile.TemporaryDirectory(prefix="urbanova_plaus_") as tmp:
        for s in SITES:
            req = {"contract_version": "1.1", "request_id": f"plausibility-{s['key']}",
                   "site": {"latitude": s["latitude"], "longitude": s["longitude"]},
                   "settings": {"modules": ["climate_baseline", "vegetation", "surface_heat"]}}
            res = analyse(req, Path(tmp) / s["key"])
            d = res["designs"][0]
            rows.append({**s, "modules": {k: v["status"] for k, v in d["modules"].items() if v["status"] != "skipped"},
                         "reasons": {k: v.get("reason") for k, v in d["modules"].items() if v["status"] in ("unavailable", "failed", "blocked")},
                         "ndvi_site_mean": _metric(d, "ndvi_site_mean"), "ndvi_surroundings_mean": _metric(d, "ndvi_surroundings_mean"),
                         "lst_site_median_c": _metric(d, "lst_site_median_c"), "lst_surroundings_median_c": _metric(d, "lst_surroundings_median_c"),
                         "lst_scenes_used": _metric(d, "lst_scenes_used")})
    by = {r["key"]: r for r in rows}

    def order(metric: str, keys: list[str]) -> bool | None:
        vals = [by[k][metric] for k in keys]
        return None if any(v is None for v in vals) else all(a < b for a, b in zip(vals, vals[1:]))

    checks = {
        "ndvi_water_lt_desert_lt_farmland": order("ndvi_site_mean", ["water", "desert", "farmland"]),
        "ndvi_farmland_above_0.2": None if by["farmland"]["ndvi_site_mean"] is None else by["farmland"]["ndvi_site_mean"] > 0.2,
        "lst_water_lt_farmland_lt_desert": order("lst_site_median_c", ["water", "farmland", "desert"]),
    }
    out = {"generated_at": recording.now(), "method": __doc__.strip().splitlines()[0], "sites": rows, "checks": checks,
           "note": "None means a value was unavailable (for example no clear scene); it is not counted as a pass."}
    path = ROOT / "validation" / "satellite_plausibility.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['label']:45s} NDVI {r['ndvi_site_mean']} (ring {r['ndvi_surroundings_mean']})  LST {r['lst_site_median_c']} °C "
              f"(ring {r['lst_surroundings_median_c']}, scenes {r['lst_scenes_used']})  {r['modules']} {r['reasons'] or ''}")
    print("checks:", checks)
    print("wrote", path)
    return 0 if all(v is True for v in checks.values()) else 3


if __name__ == "__main__":
    sys.exit(main())
