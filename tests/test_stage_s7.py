"""Stage S7 of the smart upgrade (PHASE2_SPEC rev. 2 §2.1, §3.4): ESA WorldCover land cover as site context and
random-forest downscaling of land surface temperature to 30 m (existing land only, off by default). Offline, synthetic rasters."""

import numpy as np
import pytest

from conftest import make_project, run
from test_acceptance import approve, metric

# ------------------------------------------------------------------ synthetic field
N = 72  # 72 × 72 cells of 30 m (2.16 km); 24 × 24 blocks of 90 m


def _fine_field(seed=0):
    """NDVI and NDBI that vary at the 30 m scale, and the true 30 m LST that depends on them."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:N, 0:N]
    ndvi = np.clip(0.35 + 0.25 * np.sin(xx / 2.3) * np.cos(yy / 3.1) + 0.08 * rng.standard_normal((N, N)), 0.02, 0.85)
    ndbi = np.clip(-0.6 * ndvi + 0.15 + 0.05 * rng.standard_normal((N, N)), -0.6, 0.5)
    truth = 52 - 18 * ndvi + 6 * ndbi
    return ndvi, ndbi, truth


def _coarse(arr, k=3):
    """What a ~100 m thermal sensor sees: block means expanded back to the 30 m grid."""
    c = arr.reshape(N // k, k, N // k, k).mean(axis=(1, 3))
    return np.kron(c, np.ones((k, k)))


def _scenes(truth, n=3, seed=1):
    rng = np.random.default_rng(seed)
    return [_coarse(truth + off + 0.2 * rng.standard_normal(truth.shape)) for off in np.linspace(-1.5, 1.5, n)[:n]]


def _inputs(n_scenes=3):
    ndvi, ndbi, truth = _fine_field()
    scenes = _scenes(truth, n_scenes)
    comp = np.median(np.stack(scenes), axis=0)
    return comp, scenes, {"ndvi": ndvi, "ndbi": ndbi}, np.ones((N, N), bool), truth


def test_downscaled_field_averages_back_to_the_90m_lst():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, _ = _inputs()
    res = D.downscale(comp, scenes, preds, mask)
    assert res["accepted"] and res["blocks"] == (N // 3) ** 2
    back = D.aggregate(res["field"], ~np.isnan(res["field"]), 3)
    assert np.nanmax(np.abs(back - D.aggregate(comp, mask, 3))) < 1e-6
    assert res["checks"]["coarse_consistency_max_c"] < 0.1 == res["checks"]["gate_c"]


def test_downscaling_recovers_the_fine_pattern():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, truth = _inputs()
    field = D.downscale(comp, scenes, preds, mask)["field"]
    rmse = lambda a: float(np.sqrt(np.mean((a - (truth + 0)) ** 2)))
    # The composite carries the scene offsets' median (0), so the truth is directly comparable.
    assert rmse(field) < 0.6 * rmse(comp), (rmse(field), rmse(comp))


def test_validation_figures_are_reported_and_finite():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, _ = _inputs()
    ck = D.downscale(comp, scenes, preds, mask)["checks"]
    assert len(ck["loso_rmse_per_scene_c"]) == 3 and all(np.isfinite(ck["loso_rmse_per_scene_c"]))
    assert np.isfinite(ck["loso_rmse_c"]) and np.isfinite(ck["scale_check_rmse_c"])
    assert ck["published_rmse_range_c"] == [1.0, 3.5] and isinstance(ck["scale_check_within_published_range"], bool)


def test_too_few_scenes_or_blocks_skip_with_a_reason():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, _ = _inputs(n_scenes=1)
    res = D.downscale(comp, scenes, preds, mask)
    assert not res["accepted"] and res["field"] is None and "at least 2" in res["reason"]
    comp, scenes, preds, mask, _ = _inputs()
    small = np.zeros_like(mask)
    small[:12, :12] = True  # 16 blocks
    res = D.downscale(comp, scenes, preds, small)
    assert not res["accepted"] and "at least 30" in res["reason"]


def test_gate_rejects_a_field_that_does_not_average_back():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, _ = _inputs()
    res = D.downscale(comp, scenes, preds, mask, correct=False)
    assert not res["accepted"] and res["field"] is None and "Not accepted" in res["reason"]
    assert res["checks"]["coarse_consistency_max_c"] >= 0.1


def test_downscaling_is_deterministic():
    from urbanova.analysis import lst_downscale as D

    comp, scenes, preds, mask, _ = _inputs()
    a, b = D.downscale(comp, scenes, preds, mask), D.downscale(comp, scenes, preds, mask)
    assert np.array_equal(a["field"], b["field"], equal_nan=True) and a["checks"] == b["checks"]


# ------------------------------------------------------------------ WorldCover
def test_class_shares_sum_to_100():
    from urbanova.analysis.landcover import class_shares

    cls = np.full((10, 10), 50.0)
    cls[:3] = 10
    cls[9, 9] = np.nan
    shares = class_shares(cls, np.ones((10, 10), bool))
    assert set(shares) == {10, 50} and sum(shares.values()) == pytest.approx(100, abs=0.05) and shares[10] == pytest.approx(30.3, abs=0.05)


def test_worldcover_is_registered_with_approval_licence_and_attribution():
    from urbanova.providers.base import availability, registry
    from urbanova.providers.satellite import COLLECTIONS

    p = registry()["pc_esa_worldcover"]
    assert p["approval_status"] == "approved" and p["approved_on"] and p["license"] == "CC BY 4.0" and "ESA WorldCover" in p["attribution"]
    assert COLLECTIONS["pc_esa_worldcover"] == "esa-worldcover" and availability("pc_esa_worldcover")[0]


# ------------------------------------------------------------------ end to end (faked providers)
def _xy(t, shape):
    rows, cols = shape
    xs = t.c + (np.arange(cols) + 0.5) * t.a
    ys = t.f + (np.arange(rows) + 0.5) * t.e
    return np.meshgrid(xs, ys)


def _g(x, y):
    """NDVI-like surface, varying at ~30–60 m."""
    return 0.35 + 0.25 * np.sin(x / 23.0) * np.cos(y / 31.0)


def _fake_sources(monkeypatch, wc_fail=False):
    from urbanova.analysis import landcover, surface_heat, vegetation
    from urbanova.providers.base import ProviderError

    approve(monkeypatch, "pc_landsat_c2_l2", "pc_sentinel2_l2a")
    landsat = [{"id": f"LC09_TEST_{i}", "properties": {"datetime": f"2025-07-{10 + i:02d}T08:00:00Z", "eo:cloud_cover": 5, "platform": "landsat-9", "proj:epsg": 32636},
                "assets": {"qa_pixel": {"href": "qa"}, "lwir11": {"href": f"st{i}"}}} for i in range(3)]
    s2 = [{"id": f"S2_TEST_{i}", "properties": {"datetime": f"2025-06-{10 + i:02d}T08:00:00Z", "eo:cloud_cover": 3, "proj:epsg": 32636, "s2:processing_baseline": "05.10"},
           "assets": {"SCL": {"href": "scl"}, "B04": {"href": "red"}, "B08": {"href": "nir"}, "B11": {"href": "swir"}}} for i in range(2)]

    def lst_read(pid, href, epsg, crs, t, shape, **k):
        if href == "qa":
            return np.zeros(shape, dtype="float32")
        x, y = _xy(t, shape)
        # The sensor is coarse: the scene is the 90 m block mean of the fine field (+ a per-scene offset).
        g = _g(x, y)
        lst = 52 - 18 * g + int(href[2:]) * 0.5
        r3, c3 = (shape[0] // 3) * 3, (shape[1] // 3) * 3
        blk = lst[:r3, :c3].reshape(r3 // 3, 3, c3 // 3, 3).mean(axis=(1, 3))
        lst[:r3, :c3] = np.kron(blk, np.ones((3, 3)))
        return ((lst + 273.15 - 149.0) / 0.00341802).astype("float32")

    def s2_read(pid, href, epsg, crs, t, shape, **k):
        if href == "scl":
            return np.full(shape, 4.0, dtype="float32")
        x, y = _xy(t, shape)
        g = np.clip(_g(x, y), 0.05, 0.8)
        red = np.full(shape, 0.1)
        refl = {"red": red, "nir": red * (1 + g) / (1 - g), "swir": red * (1 + g) / (1 - g) * (1.2 - g)}[href]
        return (refl * 10000 + 1000).astype("float32")

    def wc_search(*a, **k):
        if wc_fail:
            raise ProviderError("pc_esa_worldcover", "http_error", "HTTP 503 (simulated)")
        return [{"id": "ESA_WorldCover_10m_2021_v200_N30E030", "properties": {"start_datetime": "2021-01-01T00:00:00Z", "esa_worldcover:product_version": "2.0.0", "proj:epsg": 4326},
                 "assets": {"map": {"href": "map"}}}]

    def wc_read(pid, href, epsg, crs, t, shape, **k):
        x, y = _xy(t, shape)
        return np.where(_g(x, y) > 0.4, 10.0, 50.0).astype("float32")

    monkeypatch.setattr(surface_heat, "search", lambda *a, **k: landsat)
    monkeypatch.setattr(surface_heat, "read_to_grid", lst_read)
    monkeypatch.setattr(vegetation, "search", lambda *a, **k: s2)
    monkeypatch.setattr(vegetation, "read_to_grid", s2_read)
    monkeypatch.setattr(landcover, "search_static", wc_search)
    monkeypatch.setattr(landcover, "read_to_grid", wc_read)


def test_downscaling_is_off_by_default_and_validated_as_a_boolean(client, auth, monkeypatch):
    _fake_sources(monkeypatch)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7]})
    assert b["run"]["configuration"]["modules"] == ["surface_heat"]
    assert "downscale" not in b["run"]["module_status"]["surface_heat"]["details"]
    assert not [m for m in b["metrics"] if m["metric_name"].startswith("lst_ds_")]
    r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"config": {"modules": ["surface_heat"], "lst_downscale": "yes"}})
    assert r.status_code == 422 and "lst_downscale" in r.text


def test_downscaling_on_adds_inputs_and_an_existing_condition_modelled_layer(client, auth, monkeypatch):
    _fake_sources(monkeypatch)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["surface_heat"], "hot_months": [7], "lst_downscale": True})
    assert b["run"]["configuration"]["modules"] == ["land_cover", "vegetation", "surface_heat"]
    ds = b["run"]["module_status"]["surface_heat"]["details"]["downscale"]
    assert ds["accepted"], ds
    assert {"ndvi", "ndbi"} <= set(ds["predictors"]) and any(p.startswith("wc_") for p in ds["predictors"])
    assert ds["checks"]["coarse_consistency_max_c"] < 0.1 and ds["checks"]["loso_rmse_c"] is not None
    m = metric(b, "lst_ds_site_median_c")
    assert m["condition"] == "existing" and m["output_kind"] == "modelled" and "ML-downscaled" in m["quality"]["label"]
    assert metric(b, "lst_ds_site_p90_c")["value"] >= m["value"]
    lay = next(l for l in b["layers"] if l["name"].startswith("LST downscaled to 30 m"))
    assert lay["output_kind"] == "modelled" and "RMSE" in lay["name"]
    assert any("ML-downscaled" in x for x in b["run"]["module_status"]["surface_heat"]["limitations"])


def test_land_cover_is_site_context_and_its_failure_is_contained(client, auth, monkeypatch):
    _fake_sources(monkeypatch)
    pid = make_project(client, auth)
    b = run(client, auth, pid, config={"modules": ["land_cover", "surface_heat"], "hot_months": [7]})
    st = b["run"]["module_status"]["land_cover"]
    assert st["status"] == "completed" and st["details"]["site_context"]["condition"] == "existing"
    shares = st["details"]["surroundings_shares_pct"]
    assert set(shares) == {"Tree cover", "Built-up"} and sum(shares.values()) == pytest.approx(100, abs=0.05)
    assert all(m["condition"] == "existing" for m in b["metrics"] if m["module"] == "land_cover")
    types = [c["type"] for c in b["site_context"]]
    assert types == ["H2", "land_cover"]
    lay = next(l for l in b["layers"] if l["module"] == "land_cover")
    assert {c["label"] for c in lay["legend"]["classes"]} == {"Tree cover", "Built-up"}
    # A WorldCover failure leaves land cover unavailable and nothing else.
    _fake_sources(monkeypatch, wc_fail=True)
    b = run(client, auth, pid, config={"modules": ["land_cover", "surface_heat"], "hot_months": [7]})
    assert b["run"]["module_status"]["land_cover"]["status"] == "unavailable"
    assert b["run"]["module_status"]["surface_heat"]["status"] in ("completed", "partial")
    assert [c["type"] for c in b["site_context"]] == ["H2"]


def test_downscaled_values_never_reach_hotspots_or_comparisons():
    import inspect

    from test_stage_s5 import _compare, _cost, _designs, _m

    from urbanova import comparison
    from urbanova.analysis import hotspots
    from urbanova.readiness import CONDITION

    assert CONDITION["surface_heat"] == "existing" and CONDITION["land_cover"] == "existing"
    assert "lst_ds_" not in inspect.getsource(hotspots) and "lst_ds_" not in str(comparison.CRITERIA)
    # In a real comparison the downscaled values (existing land, different per design here on purpose) are site context,
    # never a design difference.
    scen, metrics, checks, costs = _designs([100, 80], [10, 20], [_cost(50)])
    metrics["b"]["lst_ds_site_median_c"] = _m("lst_ds_site_median_c", 40.0, "existing", "°C")
    metrics["a1"]["lst_ds_site_median_c"] = _m("lst_ds_site_median_c", 35.0, "existing", "°C")
    out = _compare(scen, metrics, checks, costs)
    assert not [r for r in out["rows"] if r["metric"].startswith("lst_ds_")]
    assert any(s["metric"] == "lst_ds_site_median_c" for s in out["site_conditions_shared"])
    assert "lst_downscale" in comparison.COMPARABLE_CONFIG_KEYS
