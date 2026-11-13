"""Build the street-level "case files" of the Trend Detective (NASA Space Apps 2026).

For each demo region, URBANOVA's engine runs a SITE ANALYSIS (no design) of a ~1.2 km plot inside it with live data:
- Landsat land surface temperature (NASA/USGS);
- Sentinel-2 vegetation (NDVI);
- ESA WorldCover land cover;
- the ERA5 climate baseline;
- OpenStreetMap context;
- sun position and heat stress in sun and shade (UTCI).

The summary and the map overlays are written to web/casefiles/<id>/ and served read-only to everyone. The regional
trend says "the region is changing"; the case file shows what that place looks like on the ground today.

Usage (network needed, about 1–3 minutes per place):
    .venv/Scripts/python scripts/make_case_files.py [id ...]
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "web" / "casefiles"
HALF_M = 600  # half the side of the plot square (1.2 km × 1.2 km)
# id → the regional demo it belongs to, and a specific plot inside that region
CASES = {
    "new_cairo": {"region": "new_cairo", "name": "Fifth Settlement, New Cairo", "lat": 30.03, "lon": 31.47},
    "delhi": {"region": "delhi", "name": "Central Delhi (Connaught Place)", "lat": 28.632, "lon": 77.219},
    "perth": {"region": "opposite", "name": "Perth city centre", "lat": -31.952, "lon": 115.857},
    "sydney": {"region": "sydney", "name": "Sydney CBD", "lat": -33.87, "lon": 151.21},
    "niamey": {"region": "sahel", "name": "Niamey, Niger (Sahel)", "lat": 13.512, "lon": 2.112},
}
MODULES = ["climate_baseline", "site_context", "land_cover", "surface_heat", "vegetation", "solar_shade", "heat_stress"]
KEY_METRICS = [
    "lst_site_median_c", "lst_surroundings_median_c", "lst_site_minus_surroundings_k", "lst_site_hot_cluster_share_pct", "lst_scenes_used",
    "ndvi_site_mean", "ndvi_surroundings_mean", "ndvi_site_vegetated_share_pct",
    "utci_sun_peak_c", "utci_shade_peak_c", "utci_sun_very_strong_hours",
    "climate_hot_season_mean_tmax_c", "climate_mean_annual_TX35_days", "climate_mean_annual_TR_days", "climate_tmax_record_c",
    "context_mapped_buildings_count", "context_mapped_green_pct",
]


def square(lat: float, lon: float, half_m: float) -> dict:
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]
    return {"type": "Polygon", "coordinates": [ring]}


def build(case_id: str) -> dict:
    from urbanova.engine import analyse

    c = CASES[case_id]
    boundary = square(c["lat"], c["lon"], HALF_M)
    req = {"contract_version": "1.2", "request_id": f"casefile-{case_id}", "site": {"latitude": c["lat"], "longitude": c["lon"], "boundary": boundary},
           "objectives": ["main_concerns"], "settings": {"modules": MODULES}}
    tmp = Path(tempfile.mkdtemp(prefix=f"case_{case_id}_"))
    res = analyse(req, tmp)
    [d] = res["designs"]
    dest = OUT / case_id
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    layers = []
    for lay in d["layers"]:
        src = tmp / lay["file"]
        if lay["file"].endswith(".png") and src.exists() and lay.get("bounds"):
            name = f"{len(layers):02d}_{lay['module']}.png"
            shutil.copy(src, dest / name)
            layers.append({"name": lay["name"], "module": lay["module"], "output_kind": lay["output_kind"], "file": name, "bounds": lay["bounds"], "legend": lay.get("legend")})
    metrics = {m["metric_name"]: m for m in d["metrics"]}
    landcover = sorted([{"label": m["label"], "value": m["value"]} for m in d["metrics"] if m["metric_name"].startswith("landcover_site_")
                        and m["state"] == "available" and (m["value"] or 0) > 0], key=lambda x: -x["value"])
    summary = {
        "id": case_id, **c, "boundary": boundary, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis_mode": d.get("analysis_mode"), "status": d["status"],
        "metrics": {k: {"label": metrics[k]["label"], "value": metrics[k]["value"], "unit": metrics[k]["unit"], "state": metrics[k]["state"],
                        "reason": metrics[k].get("reason"), "output_kind": metrics[k]["output_kind"]} for k in KEY_METRICS if k in metrics},
        "land_cover": landcover,
        "site_context": d.get("site_context") or [],
        "modules": {k: {"label": v["label"], "status": v["status"], "reason": v.get("reason")} for k, v in d["modules"].items() if v["status"] != "skipped"},
        "layers": layers,
        "datasets": [{"name": x.get("source_id"), "attribution": x.get("attribution"), "license": x.get("license")} for x in res["datasets"]],
        "limitations": sorted({t for v in d["modules"].values() for t in (v.get("limitations") or [])})[:12],
    }
    (dest / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return summary


def main(ids: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads((OUT / "index.json").read_text(encoding="utf-8")) if (OUT / "index.json").exists() else {}
    for cid in ids or list(CASES):
        s = build(cid)
        m = s["metrics"]
        get = lambda k: (m.get(k) or {}).get("value")  # noqa: E731
        print(f"{cid:10} {s['status']:20} LST {get('lst_site_median_c')} °C (vs surroundings {get('lst_site_minus_surroundings_k')} K) "
              f"NDVI {get('ndvi_site_mean')} UTCI sun {get('utci_sun_peak_c')} shade {get('utci_shade_peak_c')} layers {len(s['layers'])}", flush=True)
        index[cid] = {"region": s["region"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "status": s["status"], "generated_at": s["generated_at"]}
        (OUT / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
