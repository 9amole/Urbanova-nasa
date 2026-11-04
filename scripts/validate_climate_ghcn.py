"""D3: compare the product's ERA5 climate baseline with NOAA GHCN-Daily weather stations.

The ERA5 series is fetched through the product's own adapter (Open-Meteo, models=era5) at each
station's coordinates, and both series go through the product's own `climate.summarise()` with the
index definitions from urbanova/config/rules.json, so the comparison tests exactly what users see.
GHCN-Daily is approved for this validation only (decision D3); no product result uses it.

Run (needs internet):  .venv/Scripts/python scripts/validate_climate_ghcn.py
Writes validation/climate_ghcn_results.json and prints a summary table.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from urbanova import recording  # noqa: E402
from urbanova.analysis.climate import summarise  # noqa: E402
from urbanova.providers.base import availability  # noqa: E402
from urbanova.providers.weather import fetch_open_meteo  # noqa: E402
from urbanova.settings import load_config, settings  # noqa: E402

BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily"
# Stations with complete TMAX/TMIN years (>= 300 days each) in GHCN-Daily, checked 2026-11-04.
# Recent years for Cairo-area stations are not in GHCN-Daily, so the most recent complete runs are used.
STATIONS = [
    {"id": "EGM00062387", "start_year": 2015, "end_year": 2019, "setting": "Nile valley, inland (Minya)"},
    {"id": "EGM00062337", "start_year": 2001, "end_year": 2004, "setting": "Mediterranean coast (El Arish)"},
]
MAX_MISSING_FRACTION = 0.10


def _get(url: str, cache: Path) -> bytes:
    if cache.exists():
        return cache.read_bytes()
    r = httpx.get(url, headers={"User-Agent": settings.user_agent}, timeout=120, follow_redirects=True)
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content


def station_meta(sid: str, cache_dir: Path) -> dict:
    text = _get(f"{BASE}/ghcnd-stations.txt", cache_dir / "ghcnd-stations.txt").decode("ascii", errors="replace")
    for line in text.splitlines():
        if line.startswith(sid):
            return {"id": sid, "latitude": float(line[12:20]), "longitude": float(line[21:30]), "elevation_m": float(line[31:37]), "name": line[41:71].strip()}
    raise SystemExit(f"Station {sid} not found in ghcnd-stations.txt")


def station_series(sid: str, start: date, end: date, cache_dir: Path) -> dict:
    """Daily TMAX/TMIN in °C; values with a quality flag (QFLAG) are excluded, never repaired."""
    raw = _get(f"{BASE}/by_station/{sid}.csv.gz", cache_dir / f"{sid}.csv.gz")
    vals: dict[str, dict[str, float]] = {"TMAX": {}, "TMIN": {}}
    flagged = 0
    for row in csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="ascii")):
        _, d, el, v, _mflag, qflag = row[:6]
        if el not in vals:
            continue
        day = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if not (start.isoformat() <= day <= end.isoformat()):
            continue
        if qflag.strip():
            flagged += 1
            continue
        vals[el][day] = int(v) / 10.0  # tenths of °C
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    return {"dates": dates, "series": {"tmax_c": [vals["TMAX"].get(d) for d in dates], "tmin_c": [vals["TMIN"].get(d) for d in dates]},
            "quality_flagged_values": flagged}


def _pairs(a: list, b: list) -> list[tuple[float, float]]:
    return [(x, y) for x, y in zip(a, b) if x is not None and y is not None]


def _stats(pairs: list[tuple[float, float]]) -> dict:
    """ERA5 minus station."""
    if len(pairs) < 30:
        return {"n_days": len(pairs)}
    d = [e - s for e, s in pairs]
    es, ss = [e for e, _ in pairs], [s for _, s in pairs]
    me, ms = mean(es), mean(ss)
    cov = sum((e - me) * (s - ms) for e, s in pairs)
    r = cov / math.sqrt(sum((e - me) ** 2 for e in es) * sum((s - ms) ** 2 for s in ss))
    return {"n_days": len(pairs), "bias_c": round(mean(d), 2), "mae_c": round(mean(abs(x) for x in d), 2),
            "rmse_c": round(math.sqrt(mean(x * x for x in d)), 2), "pearson_r": round(r, 3)}


def _season_mean(data: dict, months: list[int], var: str = "tmax_c") -> float | None:
    v = [x for d, x in zip(data["dates"], data["series"][var]) if x is not None and int(d[5:7]) in months]
    return round(mean(v), 2) if v else None


def validate(st: dict, cache_dir: Path) -> dict:
    meta = station_meta(st["id"], cache_dir)
    start, end = date(st["start_year"], 1, 1), date(st["end_year"], 12, 31)
    obs = station_series(st["id"], start, end, cache_dir)
    era = fetch_open_meteo(meta["latitude"], meta["longitude"], start, end)
    era_aligned = {"dates": obs["dates"], "series": {k: [dict(zip(era["dates"], era["series"][k])).get(d) for d in obs["dates"]] for k in ("tmax_c", "tmin_c")}}
    indices = load_config("rules.json")["climate_indices"]
    s_obs, s_era = summarise(obs, indices), summarise(era_aligned, indices)
    months = s_era["hottest_months"]
    completeness = {k: round(sum(v is not None for v in obs["series"][k]) / len(obs["dates"]), 3) for k in ("tmax_c", "tmin_c")}
    idx = {}
    for io_, ie in zip(s_obs["indices"], s_era["indices"]):
        both = sorted(set(io_["per_year"]) & set(ie["per_year"]))
        if both:
            o, e = mean(io_["per_year"][y] for y in both), mean(ie["per_year"][y] for y in both)
            idx[io_["id"]] = {"label": io_.get("label"), "years_compared": len(both), "station_per_year": round(o, 1), "era5_per_year": round(e, 1),
                              "difference": round(e - o, 1)}
        else:
            idx[io_["id"]] = {"label": io_.get("label"), "years_compared": 0, "note": "No year with >= 90 % complete data in both series."}
    hot_o, hot_e = _season_mean(obs, months), _season_mean(era_aligned, months)
    return {
        "station": meta, "setting": st["setting"], "period": [start.isoformat(), end.isoformat()],
        "era5_grid_point": era.get("grid_point"), "station_completeness": completeness, "quality_flagged_values_excluded": obs["quality_flagged_values"],
        "daily_tmax": _stats(_pairs(era_aligned["series"]["tmax_c"], obs["series"]["tmax_c"])),
        "daily_tmin": _stats(_pairs(era_aligned["series"]["tmin_c"], obs["series"]["tmin_c"])),
        "hot_season_months": months,
        "hot_season_mean_tmax_c": {"station": hot_o, "era5": hot_e, "difference": round(hot_e - hot_o, 2) if hot_o is not None and hot_e is not None else None},
        "tmax_p90_c": {"station": s_obs["tmax_p90_c"], "era5": s_era["tmax_p90_c"]},
        "tmax_p95_c": {"station": s_obs["tmax_p95_c"], "era5": s_era["tmax_p95_c"]},
        "indices": idx,
    }


def main() -> int:
    ok, kind, why = availability("noaa_ghcn_daily")
    if not ok:
        print(f"NOAA GHCN-Daily is not usable: {why}")
        return 1
    if settings.offline:
        print("URBANOVA_OFFLINE=1: this validation needs internet access.")
        return 1
    cache_dir = settings.cache_dir / "noaa_ghcn_daily"
    with recording.use(recording.MemoryRecorder()) as rec:
        results = [validate(st, cache_dir) for st in STATIONS]
    out = {"generated_at": recording.now(), "method": __doc__.strip().splitlines()[0], "era5_requests": rec.requests, "stations": results,
           "interpretation_notes": [
               "A station is a point; ERA5 is a ~25 km cell average, so a non-zero difference does not by itself mean either is wrong.",
               "Day boundaries differ: Open-Meteo aggregates ERA5 by local calendar day; GHCN TMAX/TMIN follow the station's observation day.",
               "Station values with a GHCN quality flag were excluded, not corrected.",
           ]}
    path = ROOT / "validation" / "climate_ghcn_results.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in results:
        st, dt, dn = r["station"], r["daily_tmax"], r["daily_tmin"]
        print(f"\n{st['name']} ({st['id']}, {st['latitude']}, {st['longitude']}) {r['period'][0][:4]}-{r['period'][1][:4]}, completeness {r['station_completeness']}")
        print(f"  daily Tmax ERA5-station: bias {dt.get('bias_c')} °C, MAE {dt.get('mae_c')}, RMSE {dt.get('rmse_c')}, r {dt.get('pearson_r')} (n={dt['n_days']})")
        print(f"  daily Tmin ERA5-station: bias {dn.get('bias_c')} °C, MAE {dn.get('mae_c')}, RMSE {dn.get('rmse_c')}, r {dn.get('pearson_r')} (n={dn['n_days']})")
        print(f"  hot season {r['hot_season_months']} mean Tmax: station {r['hot_season_mean_tmax_c']['station']} / ERA5 {r['hot_season_mean_tmax_c']['era5']}")
        print(f"  P90 Tmax: station {r['tmax_p90_c']['station']} / ERA5 {r['tmax_p90_c']['era5']}")
        for k, v in r["indices"].items():
            print(f"  {k}: station {v.get('station_per_year')} / ERA5 {v.get('era5_per_year')} per year ({v['years_compared']} years)")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
