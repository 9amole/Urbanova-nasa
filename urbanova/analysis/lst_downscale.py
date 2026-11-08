"""Random-forest downscaling of land surface temperature to 30 m (PHASE2_SPEC §3.4, stage S7). Existing condition only.

Landsat's thermal band is 100 m; the 30 m Level-2 product is resampled from it. The study area's own coarse pairs teach a
random forest how surface temperature relates to finer predictors (Sentinel-2 NDVI and NDBI, WorldCover class fractions):

1. Coarse scale: 90 m blocks (3 × 3 cells of 30 m, close to the native 100 m). Each complete block has its mean predictors
   and its mean surface temperature.
2. A random forest is trained on those block pairs (TsHARP-style sharpening [R-31] with random forest [R-30]).
3. It predicts at 30 m from the 30 m predictors.
4. Residual correction: each block's coarse residual (block LST − mean of its 30 m predictions) is added to its cells, so the
   30 m field averages exactly back to the 90 m LST.

Validation on every run: coarse consistency (must be < 0.1 °C to accept the field), leave-one-scene-out RMSE at the coarse
scale, and a scale check (train at 270 m, predict 90 m) compared with the published urban range of about 1–3.5 °C [R-32].
The result describes today's land: it never feeds design hotspots, causes or scenario differences.
"""

from __future__ import annotations

import numpy as np

from .. import basis
from .common import average_to_grid, block_mean as aggregate

BLOCK = 3  # 90 m blocks of 30 m cells (basis: surface_heat.block_size_m)
SCALE_BLOCK = 9  # 270 m blocks for the scale check (basis: lst_downscale.scale_check_m)


def setting(key: str):
    """A downscaling setting from its basis record (urbanova/config/basis.json)."""
    return basis.get(f"lst_downscale.{key}")["value"]


def _expand(coarse: np.ndarray, k: int, shape: tuple[int, int]) -> np.ndarray:
    out = np.full(shape, np.nan)
    big = np.kron(coarse, np.ones((k, k)))
    out[:big.shape[0], :big.shape[1]] = big
    return out


def _forest(oob: bool = False):
    from sklearn.ensemble import RandomForestRegressor

    return RandomForestRegressor(n_estimators=int(setting("rf_trees")), min_samples_leaf=int(setting("rf_min_samples_leaf")), random_state=0, n_jobs=1, oob_score=oob)


def _pairs(target: np.ndarray, X: np.ndarray, valid: np.ndarray, k: int):
    """Coarse training pairs: (block features [n, p], block target [n], block grid of the target)."""
    t = aggregate(target, valid, k)
    feats = np.stack([aggregate(X[..., j], valid, k) for j in range(X.shape[-1])], axis=-1)
    ok = ~np.isnan(t) & ~np.isnan(feats).any(axis=-1)
    return feats[ok], t[ok], t, ok


def sharpen(target: np.ndarray, X: np.ndarray, valid: np.ndarray, k: int, correct: bool = True, pairs=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Train at k × k blocks, predict per cell, add back the block residual. Returns (fine field, coarse target grid, block mask).
    `pairs` reuses training pairs already built by _pairs(target, X, valid, k)."""
    fx, ft, tgrid, ok = pairs or _pairs(target, X, valid, k)
    rf = _forest().fit(fx, ft)
    pred = np.full(target.shape, np.nan)
    pred[valid] = rf.predict(X[valid])
    covered = _expand(ok.astype(float), k, target.shape) == 1
    field = np.where(covered, pred, np.nan)
    if correct:
        resid = tgrid - aggregate(np.nan_to_num(field), covered, k)
        field = field + _expand(resid, k, target.shape)
    return field, tgrid, ok


def _rmse(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(np.mean((a - b) ** 2)))


def downscale(comp: np.ndarray, scenes: list[np.ndarray], predictors: dict[str, np.ndarray], study_mask: np.ndarray, *, correct: bool = True) -> dict:
    """Downscale the composite `comp` (30 m grid, °C) with 30 m `predictors` on the same grid.

    Returns {"accepted": bool, "reason", "field", "checks": {...}, "predictors", "blocks"}. `correct=False` disables the
    residual correction (used only to test the acceptance gate)."""
    names = list(predictors)
    X = np.stack([predictors[n] for n in names], axis=-1).astype("float64")
    valid = study_mask & ~np.isnan(comp) & ~np.isnan(X).any(axis=-1)
    out = {"accepted": False, "reason": None, "field": None, "predictors": names, "scenes": len(scenes), "checks": {}}
    min_scenes, min_blocks = int(setting("min_scenes")), int(setting("min_blocks"))
    pairs = _pairs(comp, X, valid, BLOCK)
    ft = pairs[1]
    out["blocks"] = int(ft.size)
    if len(scenes) < min_scenes:
        out["reason"] = f"Only {len(scenes)} clear-sky scene(s); at least {min_scenes} are needed for the leave-one-scene-out check."
        return out
    if ft.size < min_blocks:
        out["reason"] = f"Only {ft.size} complete 90 m blocks with all predictors; at least {min_blocks} are needed to train the model."
        return out
    field, tgrid, ok = sharpen(comp, X, valid, BLOCK, correct=correct, pairs=pairs)
    covered = ~np.isnan(field)
    back = aggregate(np.nan_to_num(field), covered, BLOCK)
    consistency = float(np.nanmax(np.abs(back - tgrid)[ok]))
    # Leave one scene out: train on the median of the other scenes, predict the held-out scene's 90 m blocks. Each block's
    # prediction is out-of-bag (only from trees that did not see that block), so the error is not an in-sample fit. Each
    # scene's mean is removed first: the model predicts the pattern; the scene-wide level changes with the weather of the day.
    loso = []
    for i, held in enumerate(scenes):
        rest = np.nanmedian(np.stack([s for j, s in enumerate(scenes) if j != i]), axis=0)
        v = valid & ~np.isnan(rest) & ~np.isnan(held)
        fx, ft_rest, _, okb = _pairs(rest, X, v, BLOCK)
        th = aggregate(held, v, BLOCK)[okb]
        if ft_rest.size < min_blocks:
            continue
        pred = _forest(oob=True).fit(fx, ft_rest).oob_prediction_
        loso.append(round(_rmse(pred - pred.mean(), th - th.mean()), 3))
    # Scale check: the same sharpening one level up — train on 270 m blocks of the 90 m grid, predict the 90 m blocks
    # (with residual correction at 270 m) and compare with the 90 m LST.
    scale_rmse = None
    k2 = SCALE_BLOCK // BLOCK
    if (~np.isnan(aggregate(tgrid, ok, k2))).sum() >= int(setting("min_scale_blocks")):
        feats90 = np.stack([aggregate(X[..., j], valid, BLOCK) for j in range(X.shape[-1])], axis=-1)
        pred90, _, _ = sharpen(np.nan_to_num(tgrid), np.nan_to_num(feats90), ok, k2)
        cmp_ = ~np.isnan(pred90)
        if cmp_.any():
            scale_rmse = round(_rmse(pred90[cmp_], tgrid[cmp_]), 3)
    lo, hi = setting("published_rmse_range_c")
    out["checks"] = {"coarse_consistency_max_c": consistency, "gate_c": float(setting("consistency_gate_c")),
                     "loso_rmse_c": float(np.mean(loso)) if loso else None, "loso_rmse_per_scene_c": loso,
                     "scale_check_rmse_c": scale_rmse, "published_rmse_range_c": [lo, hi],
                     "scale_check_within_published_range": None if scale_rmse is None else bool(scale_rmse <= hi)}
    if not consistency < out["checks"]["gate_c"]:
        out["reason"] = f"Not accepted: the 30 m field does not average back to the 90 m surface temperature (max difference {consistency:.3f} °C, limit {out['checks']['gate_c']} °C)."
        return out
    out.update(accepted=True, field=field)
    return out


def predictors_from(shared: dict, transform, crs: str, shape: tuple[int, int], min_class_pct: float = 1.0) -> dict[str, np.ndarray]:
    """30 m predictors from the shared NDVI/NDBI composites and WorldCover classes (class fractions per cell)."""
    out = {}
    nd = shared.get("ndvi")
    if nd:
        out["ndvi"] = average_to_grid(nd["composite"], nd["transform"], nd["crs"], transform, crs, shape)
        if nd.get("ndbi") is not None:
            out["ndbi"] = average_to_grid(nd["ndbi"], nd["transform"], nd["crs"], transform, crs, shape)
    lc = shared.get("landcover")
    if lc is not None:
        cls = lc["classes"]
        valid = ~np.isnan(cls)
        for code in np.unique(cls[valid]).astype(int):
            if 100 * np.mean(cls[valid] == code) >= min_class_pct:
                frac = np.where(valid, (cls == code).astype("float32"), np.nan)
                out[f"wc_{code}"] = average_to_grid(frac, lc["transform"], lc["crs"], transform, crs, shape)
    return out
