"""The project's 🔎 Trend Detective tab (Space Apps 2026): a trends-only run, its help page and the test-site boundary files."""

import json

import pytest

from conftest import ROOT, make_project, run
from test_climate_trends import power, real_trends, site_data  # noqa: F401  (fixtures: real climate_trends on fake NASA POWER)
from test_site_trends import landsat  # noqa: F401  (fixture: fake Landsat archive)

TREND_MODULES = ["climate_baseline", "climate_trends", "site_trends"]


def test_a_trends_only_run_gives_the_plot_and_the_region(client, auth, real_trends, landsat, monkeypatch):  # noqa: F811
    from urbanova.analysis import site_trends
    from urbanova.engine import core

    monkeypatch.setitem(core.MODULES, "site_trends", site_trends.run)
    pid = make_project(client, auth)
    b = run(client, auth, pid, None, {"modules": TREND_MODULES})
    ms = b["run"]["module_status"]
    assert ms["climate_trends"]["status"] == "completed" and ms["site_trends"]["status"] == "completed"
    assert ms["surface_heat"]["status"] == "skipped" and not ms["surface_heat"].get("site_mode")  # not selected, not run
    assert {r["id"] for r in ms["site_trends"]["details"]["rows"]} == {"lst", "diff", "ndvi"}
    assert any(lay["module"] == "site_trends" for lay in b["layers"])
    assert {f["rule_id"] for f in b["findings"]} >= {"TREND-WARM-01", "TREND-SITE-01", "TREND-SITE-02"}
    from urbanova import explain

    green = explain.answer("Is the site losing green cover?", b)["answer"]
    assert green.startswith("Trend in site greenness (NDVI)")  # the asked-about row first, never cut off
    assert explain.answer("Is the site heating faster than its surroundings?", b)["answer"].startswith("Trend in site minus surroundings")


def test_the_tab_has_a_help_page(client):
    page = client.get("/api/v1/support/pages/trends").json()
    assert "Trend Detective" in page["title"] and any("Run the trend detective" in s for s in page["steps"])


@pytest.mark.parametrize("name, lat, lon", [("spaceapps_new_cairo_plot.geojson", 30.0200, 31.5000), ("sample_site_boundary.geojson", 30.1200, 31.8500),
                                            ("spaceapps_novo_progresso_plot.geojson", -7.0300, -55.4000)])
def test_the_test_site_boundaries_are_valid_for_their_coordinates(name, lat, lon):
    from urbanova import geo

    geom, warnings = geo.validate_boundary(json.loads((ROOT / "samples" / name).read_text(encoding="utf-8")), lat, lon)
    assert geom["type"] in ("Polygon", "MultiPolygon") and not [w for w in warnings if "outside" in w.lower()]
