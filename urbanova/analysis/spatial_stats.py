"""Local hot-cluster statistic for the H2 site context (PHASE2_SPEC §3.2, §4.1).

Getis–Ord Gi* (Getis & Ord 1992; Ord & Getis 1995) with binary distance-band weights that include the
cell itself, conditional permutation inference as in PySAL esda `G_Local(star=True)` (x_i held fixed,
the other values permuted among its neighbours), and Benjamini–Hochberg control of the false discovery
rate across the local tests (as evaluated for local statistics by Caldas de Castro & Singer 2006).
"""

from __future__ import annotations

import numpy as np


def band_neighbours(valid: np.ndarray, band_cells: float) -> list[np.ndarray]:
    """For each valid cell (row-major order of valid cells), the indices of the other valid cells whose
    centres are within `band_cells` cell widths."""
    rr, cc = np.nonzero(valid)
    out = []
    r = int(np.floor(band_cells))
    index = -np.ones(valid.shape, dtype=int)
    index[rr, cc] = np.arange(rr.size)
    for i, (y, x) in enumerate(zip(rr, cc)):
        y0, y1, x0, x1 = max(0, y - r), min(valid.shape[0], y + r + 1), max(0, x - r), min(valid.shape[1], x + r + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        near = ((yy - y) ** 2 + (xx - x) ** 2 <= band_cells ** 2) & (index[y0:y1, x0:x1] >= 0)
        j = index[y0:y1, x0:x1][near]
        out.append(j[j != i])
    return out


def gi_star_z(x: np.ndarray, neigh: list[np.ndarray]) -> np.ndarray:
    """Closed-form standardised Gi* (Ord & Getis 1995) with binary weights, the cell included."""
    n = x.size
    xbar, s = x.mean(), np.sqrt((x ** 2).mean() - x.mean() ** 2)
    z = np.full(n, np.nan)
    for i, j in enumerate(neigh):
        w = j.size + 1  # the cell itself has weight 1
        num = x[i] + x[j].sum() - xbar * w
        den = s * np.sqrt((n * w - w ** 2) / (n - 1))
        z[i] = num / den if den > 0 else np.nan
    return z


def gi_star(grid: np.ndarray, band_cells: float, perms: int = 999, seed: int = 0) -> dict:
    """Gi* hot-cluster test on a 2-D grid (NaN = no data).

    Returns {"z": grid of closed-form z, "p": grid of one-sided (hot) conditional-permutation p-values,
    "valid": mask}. p = (k + 1) / (perms + 1), k = permutations whose neighbourhood sum ≥ the observed one."""
    valid = ~np.isnan(grid)
    x = grid[valid].astype(float)
    n = x.size
    neigh = band_neighbours(valid, band_cells)
    z = gi_star_z(x, neigh)
    p = np.full(n, np.nan)
    kmax = max((j.size for j in neigh), default=0)
    if n > 1 and kmax:
        rng = np.random.default_rng(seed)  # fixed seed: the same data always gives the same answer
        # One set of random draws of kmax distinct indices from 0…n-2, reused for every cell (as esda does);
        # index ≥ i is shifted by one so cell i itself is never drawn.
        draws = np.argsort(rng.random((perms, n - 1)), axis=1)[:, :kmax]
        for i, j in enumerate(neigh):
            k = j.size
            idx = draws[:, :k]
            idx = idx + (idx >= i)
            sim = x[idx].sum(axis=1)
            p[i] = (np.sum(sim >= x[j].sum()) + 1) / (perms + 1)
    zg, pg = np.full(grid.shape, np.nan), np.full(grid.shape, np.nan)
    zg[valid], pg[valid] = z, p
    return {"z": zg, "p": pg, "valid": valid}


def bh_reject(p: np.ndarray, q: float) -> np.ndarray:
    """Benjamini–Hochberg: boolean array of rejected hypotheses at FDR level q (NaN never rejected)."""
    flat = np.asarray(p, dtype=float).ravel()
    ok = ~np.isnan(flat)
    out = np.zeros(flat.shape, dtype=bool)
    m = int(ok.sum())
    if m:
        order = np.argsort(flat[ok])
        ranked = flat[ok][order]
        below = ranked <= q * np.arange(1, m + 1) / m
        if below.any():
            cut = ranked[np.nonzero(below)[0].max()]
            sel = np.zeros(m, dtype=bool)
            sel[flat[ok] <= cut] = True
            out[np.nonzero(ok)[0]] = sel
    return out.reshape(np.shape(p))


def hot_clusters(grid: np.ndarray, band_cells: float, q: float, perms: int = 999, seed: int = 0) -> dict:
    """Cells in a statistically significant hot cluster: Gi* z > 0 and BH-rejected at FDR q."""
    g = gi_star(grid, band_cells, perms, seed)
    sig = bh_reject(g["p"], q) & (np.nan_to_num(g["z"]) > 0)
    return {**g, "significant": sig}
