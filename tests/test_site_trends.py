"""A1c site trends inside the boundary (Landsat 1984-, Space Apps 2026). Offline: the Landsat archive is faked on the real
study grid. In the default scenario the ring warms +0.3 °C per decade, the site (pixels near its centre) another
+0.4 K per decade, and the site loses greenness; 1990 and 1991 are cloudy."""

import pytest

from conftest import boundary

YEARS_CLOUDY = {1990, 1991}


def _platform(year: int) -> str:
    return "landsat-5" if year < 1999 else ("landsat-7" if year < 2013 else "landsat-8")


@pytest.fixture()
def landsat(monkeypatch):
    """Fake STAC search and COG reads. Returns the scenario dict (edit it in a test) and a read counter."""
    import numpy as np

    from urbanova.analysis import site_trends as st

    scen = {"ring_per_yr": 0.03, "site_extra_per_yr": 0.04, "ndvi_per_yr": -0.005, "sensor_jump": 0.0, "fail": False, "years": None}
    reads = {"n": 0}

    def search(provider, geom, start, end, max_cloud, limit=40, extra_query=None):
        from urbanova.providers.base import ProviderError

        if scen["fail"]:
            raise ProviderError(provider, "network_error", "down")
        y = int(start[:4])
        if scen["years"] and y not in scen["years"]:
            return []
        plat = _platform(y)
        th = "lwir11" if plat == "landsat-8" else "lwir"
        return [{"id": f"L{y}_{k}", "properties": {"datetime": f"{y}-07-{10 + k}T08:00:00Z", "platform": plat, "eo:cloud_cover": 5 + k, "proj:epsg": 32636},
                 "assets": {a: {"href": f"fake://{y}/{k}/{a}"} for a in ("qa_pixel", th, "red", "nir08")}} for k in range(2)]

    def read(provider, href, epsg, crs, transform, shape_, resampling="nearest", src_nodata=None):
        reads["n"] += 1
        _, _, y, k, asset = href.split("/")
        y, k = int(y), int(k)
        rows, cols = shape_
        yy, xx = np.mgrid[0:rows, 0:cols]
        inner = (np.hypot(yy - rows / 2, xx - cols / 2) * 30 < 150).astype("float32")
        rng = np.random.default_rng(y * 10 + k)
        if asset == "qa_pixel":
            return np.full(shape_, 8.0 if y in YEARS_CLOUDY else 0.0, dtype="float32")  # bit 3 = cloud
        dy = y - 1984
        if asset in ("lwir", "lwir11"):
            lst = 40 + scen["ring_per_yr"] * dy + inner * scen["site_extra_per_yr"] * dy + (scen["sensor_jump"] if y >= 2013 else 0) + rng.normal(0, 0.3)
            return ((lst + 273.15 - st.OFFSET) / st.SCALE).astype("float32") + np.zeros(shape_, dtype="float32")
        ndvi = 0.3 + inner * scen["ndvi_per_yr"] * dy + rng.normal(0, 0.01)
        red = np.full(shape_, 0.1, dtype="float32")
        refl = red if asset == "red" else red * (1 + ndvi) / (1 - ndvi)
        return ((refl - st.SR_OFFSET) / st.SR_SCALE).astype("float32")

    monkeypatch.setattr(st, "search", search)
    monkeypatch.setattr(st, "read_to_grid", read)
    monkeypatch.setattr(st, "availability", lambda pid: (True, None, ""))
    return scen, reads


def _run(tmp_path, with_boundary=True, sub="r1"):
    from urbanova.engine import AnalysisResult, analyse

    site = {"latitude": 30.12, "longitude": 31.85, **({"boundary": boundary()} if with_boundary else {})}
    res = analyse({"contract_version": "1.4", "site": site, "settings": {"modules": ["site_trends"]}}, tmp_path / sub)
    AnalysisResult.model_validate(res)
    return res["designs"][0]


def test_yearly_tests_find_a_known_slope():
    from urbanova import trends

    ann = [(1984 + i, 0.05 * i + (0.2 if i % 2 else -0.2)) for i in range(40)]
    r = trends.yearly_result(ann, [(f"{y}-07-15", v) for y, v in ann], "K")
    assert r["ok"] and r["rate"]["per_decade"]["slope"] == pytest.approx(0.5, abs=0.05) and r["verdict"]["level"] == "significant"
    assert r["period"]["years"] == 40 and not r["deseasonalised"] and len(r["series"]) == 40
    assert not trends.yearly_result(ann[:9], [], "K")["ok"]


def test_the_site_heats_faster_than_its_surroundings_and_loses_green(env, tmp_path, landsat):
    d = _run(tmp_path)
    mod = d["modules"]["site_trends"]
    assert mod["status"] == "completed", mod.get("reason")
    rows = {r["id"]: r for r in mod["details"]["rows"]}
    assert rows["diff"]["verdict"]["level"] == "significant" and rows["diff"]["per_decade"]["slope"] == pytest.approx(0.4, abs=0.1)
    assert rows["lst"]["per_decade"]["slope"] == pytest.approx(0.7, abs=0.15)
    assert rows["ndvi"]["verdict"]["level"] == "significant" and rows["ndvi"]["per_decade"]["slope"] < 0
    assert rows["diff"]["sensor_check"]["consistent"] and "Sensor check passed" in rows["diff"]["notes"][-1]
    cov = {c["year"]: c for c in mod["details"]["coverage"]}
    assert cov[1990]["scenes"] == 0 and cov[1985]["scenes"] == 2 and cov[2020]["sensors"] == ["landsat-8"]
    assert rows["diff"]["period"]["missing_years"] == 2  # the cloudy years are left out, never filled
    names = [lyr["name"] for lyr in d["layers"]]
    assert any(n.startswith("Surface temperature change") for n in names) and any("significant pixels" in n for n in names)
    lst, ndvi = mod["details"]["maps"]["lst"], mod["details"]["maps"]["ndvi"]
    assert lst["significant_pixels"] == lst["tested_pixels"]  # the whole area warms, so every pixel does
    assert 0 < ndvi["significant_pixels"] < ndvi["tested_pixels"] / 4  # only the centre loses green, after FDR
    titles = [f["title"] for f in d["findings"]]
    assert any(t.startswith("The site has been heating faster than its surroundings") for t in titles)
    assert any(t.startswith("Green cover on the site has declined") for t in titles)


def test_a_rerun_reads_nothing_again(env, tmp_path, landsat):
    _, reads = landsat
    _run(tmp_path, sub="a")
    first = reads["n"]
    d = _run(tmp_path, sub="b")
    assert first > 0 and reads["n"] == first and d["modules"]["site_trends"]["details"]["cached_scenes"] == d["modules"]["site_trends"]["details"]["scenes"]


def test_a_sensor_jump_gets_a_caution(env, tmp_path, landsat):
    scen, _ = landsat
    scen.update(ring_per_yr=-0.02, site_extra_per_yr=0.0, sensor_jump=3.0, ndvi_per_yr=0.0)
    rows = {r["id"]: r for r in _run(tmp_path)["modules"]["site_trends"]["details"]["rows"]}
    assert not rows["lst"]["sensor_check"]["consistent"] and rows["lst"]["notes"][-1].startswith("Caution")
    assert rows["diff"]["verdict"]["level"] != "significant"  # the offset is the same on and around the site, so it cancels


def test_a_construction_step_is_reported_even_without_a_steady_trend(env, tmp_path, landsat, monkeypatch):
    """Like the live New Cairo plot: built and irrigated in 2008, the site drops 4 K below the desert around it in one step."""
    from urbanova.analysis import site_trends as st

    scen, _ = landsat
    scen.update(ring_per_yr=0.0, site_extra_per_yr=0.0, ndvi_per_yr=0.0)
    real = st.read_to_grid

    def stepped(provider, href, epsg, crs, transform, shape_, resampling="nearest", src_nodata=None):
        import numpy as np

        arr = real(provider, href, epsg, crs, transform, shape_, resampling, src_nodata)
        y, asset = int(href.split("/")[2]), href.split("/")[4]
        if asset in ("lwir", "lwir11") and y >= 2008:
            rows, cols = shape_
            yy, xx = np.mgrid[0:rows, 0:cols]
            arr = arr - (np.hypot(yy - rows / 2, xx - cols / 2) * 30 < 150) * (4.0 / st.SCALE)
        return arr

    monkeypatch.setattr(st, "read_to_grid", stepped)
    rows = {r["id"]: r for r in _run(tmp_path)["modules"]["site_trends"]["details"]["rows"]}
    step = rows["diff"]["step"]
    assert step and step["year"] == 2008 and step["after"] - step["before"] == pytest.approx(-4, abs=0.6)
    assert rows["diff"]["notes"][0].startswith("Step change around 2008")


def test_too_few_clear_years_is_unavailable_with_a_reason(env, tmp_path, landsat):
    scen, _ = landsat
    scen["years"] = set(range(2016, 2025))
    mod = _run(tmp_path)["modules"]["site_trends"]
    assert mod["status"] == "unavailable" and "a trend needs at least 10" in mod["reason"]


def test_an_archive_outage_is_unavailable_never_a_crash(env, tmp_path, landsat):
    scen, _ = landsat
    scen["fail"] = True
    mod = _run(tmp_path)["modules"]["site_trends"]
    assert mod["status"] == "unavailable" and mod["provider_failures"]


def test_without_a_boundary_the_point_buffer_is_said(env, tmp_path, landsat):
    mod = _run(tmp_path, with_boundary=False)["modules"]["site_trends"]
    assert mod["limitations"][0].startswith("No site boundary")


def test_site_trends_are_registered_and_the_assistant_routes_to_them():
    from typing import get_args

    from urbanova import explain
    from urbanova.engine import core
    from urbanova.engine.contract import ModuleName
    from urbanova.readiness import CONDITION, RUNNABLE

    assert "site_trends" in core.SITE_LEVEL and "site_trends" in RUNNABLE and "site_trends" in get_args(ModuleName)
    assert CONDITION["site_trends"] == "existing"
    topic = next(mods for pat, mods, _ in explain.TOPICS if pat.search("Is the site losing green cover?"))
    assert "site_trends" in topic
