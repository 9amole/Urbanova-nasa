"""Heat-stress exposure in sun and shade on the composite hot day (A9, PHASE2_SPEC §3.2).

For each solar hour 07–17 of the hot day (climate baseline, ERA5 hourly):
* mean radiant temperature in sun = air temperature + ΔMRT from SolarCal (ASHRAE 55 Appendix C,
  `pythermalcomfort.solar_gain`), averaged over body orientations; in shade the same with no direct beam;
* UTCI (`pythermalcomfort.utci`, Bröde et al. 2012) from air temperature, mean radiant temperature,
  10 m wind and relative humidity, and its heat-stress category.
Design metrics weight the sunlit hard surface and open space by the hours of very strong heat stress.
"""

from __future__ import annotations

import math

import numpy as np

from .. import basis
from .. import values as V
from .common import BLOCKED, COMPLETED, PARTIAL, Context, result
from .solar import sunlit
from .urban_form import design_surfaces

_CATS = basis.get("heat_stress.utci_categories")["value"]
# UTCI assessment scale (Bröde et al. 2012, R-26): (lower bound °C, category). Heat-stress bounds from basis.json.
UTCI_SCALE = [(_CATS["extreme"], "extreme heat stress"), (_CATS["very_strong"], "very strong heat stress"), (_CATS["strong"], "strong heat stress"),
              (_CATS["moderate"], "moderate heat stress"), (9, "no thermal stress"), (0, "slight cold stress"), (-13, "moderate cold stress"),
              (-27, "strong cold stress"), (-40, "very strong cold stress"), (-math.inf, "extreme cold stress")]
VERY_STRONG, STRONG = float(_CATS["very_strong"]), float(_CATS["strong"])
SHARP_ANGLES = np.arange(0, 181, 15)  # body orientation to the sun, averaged: the direction people face is unknown
MIN_WIND_MS = basis.get("heat_stress.min_wind_ms")["value"]  # lower limit of the UTCI wind input


def category(utci_c: float | None) -> str | None:
    if utci_c is None or math.isnan(utci_c):
        return None
    return next(label for lo, label in UTCI_SCALE if utci_c >= lo)


def solarcal() -> dict:
    """SolarCal settings from basis.json (posture, short-wave absorptivity, sky-view fraction, ground reflectance)."""
    return {k: basis.get(f"heat_stress.{k}")["value"] for k in ("posture", "asw", "f_svv", "ground_reflectance")}


def delta_mrt(elevation: np.ndarray, dni: np.ndarray, sunlit: bool, cfg: dict) -> np.ndarray:
    """Orientation-averaged rise in mean radiant temperature from short-wave radiation (SolarCal), per hour; 0 with no sun."""
    from pythermalcomfort.models import solar_gain

    el, dn = np.asarray(elevation, float), np.asarray(dni, float)
    up = (el > 0) & (dn > 0)
    out = np.zeros(el.shape)
    if up.any():
        alt = np.repeat(np.minimum(el[up], 90.0), SHARP_ANGLES.size)
        beam = np.repeat(dn[up], SHARP_ANGLES.size)
        sharp = np.tile(SHARP_ANGLES, int(up.sum()))
        d = solar_gain(sol_altitude=alt, sharp=sharp, sol_radiation_dir=beam, sol_transmittance=1.0, f_svv=cfg["f_svv"], f_bes=1.0 if sunlit else 0.0,
                       asw=cfg["asw"], posture=cfg["posture"], floor_reflectance=cfg["ground_reflectance"], round_output=False).delta_mrt
        out[up] = np.asarray(d, float).reshape(-1, SHARP_ANGLES.size).mean(axis=1)
    return out


def utci_array(ta, tr, v10, rh) -> np.ndarray:
    """UTCI (°C) with the library's input limits on; out-of-range inputs give NaN (reported by the caller, never extrapolated)."""
    import warnings

    from pythermalcomfort.models import utci

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return np.atleast_1d(np.asarray(utci(tdb=ta, tr=tr, v=np.maximum(v10, MIN_WIND_MS), rh=rh, limit_inputs=True, round_output=False).utci, float))


def utci_c(ta: float, tr: float, v10: float, rh: float) -> float | None:
    out = float(utci_array(np.array([ta]), np.array([tr]), np.array([v10]), np.array([rh]))[0])
    return None if math.isnan(out) else out


def profile(hot_day: dict, positions: dict) -> list[dict]:
    """UTCI in sun and shade for each hour of the hot day (one vectorised call per quantity)."""
    cfg = solarcal()
    hours = sorted(hot_day["hours"])
    met = {k: np.array([hot_day["hours"][h][k] for h in hours]) for k in ("ta_c", "rh_pct", "v10_ms", "dni_wm2")}
    el = np.array([positions[h][0] for h in hours])
    tr_sun = met["ta_c"] + delta_mrt(el, met["dni_wm2"], True, cfg)
    tr_shade = met["ta_c"] + delta_mrt(el, met["dni_wm2"], False, cfg)
    u = utci_array(np.concatenate([met["ta_c"]] * 2), np.concatenate([tr_sun, tr_shade]), np.concatenate([met["v10_ms"]] * 2), np.concatenate([met["rh_pct"]] * 2))
    n = len(hours)
    rows = []
    for i, h in enumerate(hours):
        u_sun, u_shade = (None if math.isnan(x) else float(x) for x in (u[i], u[n + i]))
        rows.append({"hour": h, "elevation_deg": round(float(el[i]), 2), **hot_day["hours"][h], "tr_sun_c": round(float(tr_sun[i]), 2),
                     "tr_shade_c": round(float(tr_shade[i]), 2), "utci_sun_c": None if u_sun is None else round(u_sun, 2),
                     "utci_shade_c": None if u_shade is None else round(u_shade, 2), "category_sun": category(u_sun), "category_shade": category(u_shade)})
    return rows


def sunlit_area_hours(geom, shade_by_hour: dict, hours: list[int]) -> float:
    """Σ over the given hours of the area of `geom` in sun (m²·h)."""
    return sum(sunlit(geom, shade_by_hour.get(h)).area for h in hours)


def run(ctx: Context) -> dict:
    hot, sol = ctx.shared.get("hot_day"), ctx.shared.get("solar")
    if not hot:
        return result(BLOCKED, reason="No hot-day weather profile (needs the climate baseline with ERA5 hourly data), so heat stress cannot be assessed.")
    if not sol:
        return result(BLOCKED, reason="No hourly shadows (the shade analysis needs buildings with heights), so heat stress in sun and shade cannot be located.")
    rows = profile(hot, sol["positions"])
    method = ("UTCI (pythermalcomfort) from hot-day ERA5 air temperature, humidity and 10 m wind; mean radiant temperature = air temperature + "
              "SolarCal ΔMRT (ASHRAE 55), orientation-averaged, in sun and in shade")
    q = {"basis": "heat_stress.utci_categories", "hot_day_days": hot["days"]}  # the hourly profile is in the module details
    m = []
    sun = [r["utci_sun_c"] for r in rows if r["utci_sun_c"] is not None]
    shade = [r["utci_shade_c"] for r in rows if r["utci_shade_c"] is not None]
    vs_hours = [r["hour"] for r in rows if (r["utci_sun_c"] or -99) >= VERY_STRONG]
    s_hours = [r["hour"] for r in rows if (r["utci_sun_c"] or -99) >= STRONG]
    ex = V.EXISTING  # the same for every design: climate and sun position only
    if sun:
        m.append(V.metric("utci_sun_peak_c", f"Highest UTCI in full sun on the hot day ({category(max(sun))})", round(max(sun), 1), "°C", method=method, quality=q, condition=ex))
        m.append(V.metric("utci_sun_very_strong_hours", f"Hours (07–17 solar) with very strong or extreme heat stress in full sun (UTCI ≥ {VERY_STRONG:g} °C)", len(vs_hours), "h", method=method, quality=q, condition=ex))
        m.append(V.metric("utci_sun_strong_hours", f"Hours (07–17 solar) with at least strong heat stress in full sun (UTCI ≥ {STRONG:g} °C)", len(s_hours), "h", method=method, quality=q, condition=ex))
    if shade:
        m.append(V.metric("utci_shade_peak_c", f"Highest UTCI in shade on the hot day ({category(max(shade))})", round(max(shade), 1), "°C", method=method, quality=q, condition=ex))
    warnings = []
    nan_hours = [r["hour"] for r in rows if r["utci_sun_c"] is None]
    if nan_hours:
        warnings.append(f"UTCI is outside its valid input range at {len(nan_hours)} hour(s) ({', '.join(f'{h:02d}:00' for h in nan_hours)}); those hours are not counted.")
    if sol.get("buildings_without_height"):
        warnings.append(f"{sol['buildings_without_height']} building(s) have no height and cast no shadow, so sun exposure is overstated.")
    surf = design_surfaces(ctx)
    if surf:
        for name, geom, label in (("heat_stress_hard_surface_very_strong_m2h", surf["hard"], "hard surface (roads, parking, paved open space)"),
                                  ("heat_stress_open_space_very_strong_m2h", surf["open"], "open and green space")):
            if geom.area <= 0:
                m.append(V.missing(name, f"Sunlit {label} during very strong heat stress", "m²·h", V.NOT_APPLICABLE, f"No {label} in the design."))
                continue
            m.append(V.metric(name, f"Sunlit {label} during very strong heat stress (area × hours)", round(sunlit_area_hours(geom, sol["shade_by_hour"], vs_hours), 1), "m²·h",
                              method=f"Σ over hours with UTCI in sun ≥ {VERY_STRONG:g} °C of the {label} area outside that hour's shadows (exact polygons)",
                              extra={"very_strong_hours": vs_hours}))
    ctx.shared["heat_stress"] = {"profile": {r["hour"]: r for r in rows}, "very_strong_hours": vs_hours, "strong_hours": s_hours}
    limitations = [
        "Surfaces are assumed to be at air temperature: long-wave radiation from hot paving and walls is not modelled, so heat stress over paving is underestimated.",
        "Wind is the regional ERA5 10 m wind; wind inside the development is usually lower, which raises heat stress.",
        "SolarCal (ASHRAE 55) estimates diffuse and reflected short-wave radiation from the direct beam; the sky-view fraction is 1 (open sky) everywhere.",
        "The hot day is a composite of hot-season days; it is a design condition, not a forecast.",
    ]
    return result(PARTIAL if warnings else COMPLETED, metrics=m, warnings=warnings, limitations=limitations,
                  details={"profile": rows, "very_strong_hours": vs_hours, "strong_hours": s_hours, "solarcal": solarcal(), "hot_day_days": hot["days"],
                           "hot_day_months": hot["months"], "hot_day_threshold_c": hot["threshold_c"]})
