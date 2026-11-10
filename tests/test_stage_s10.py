"""Stage S10: the project Dashboard tab — every important output on one page, read from what is stored. Offline."""

from conftest import login, make_project, upload
from test_stage_s4 import _project_with_run
from test_stage_s5 import _two_scenarios


def _dash(client, auth, pid):
    r = client.get(f"/api/v1/projects/{pid}/project-dashboard", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_state_before_any_run_gives_the_next_step(client, auth):
    pid = make_project(client, auth)
    d = _dash(client, auth, pid)
    assert d["state"] == "empty" and "baseline" in d["next_step"].lower() and d["reports"] == [] and d["activity"] == []
    assert set(d["readiness"]) == {"ready", "ready_with_limitations", "blocked"}


def test_dashboard_matches_the_stored_run_and_never_turns_missing_into_zero(client, auth, monkeypatch):
    pid, b = _project_with_run(client, auth, monkeypatch, ai=False)
    d = _dash(client, auth, pid)
    assert d["state"] == "ready" and d["run"]["id"] == b["run"]["id"]
    stored = {m["metric_name"]: m for m in b["metrics"]}
    for tile in d["tiles"]["metrics"]:
        m = stored.get(tile["name"])
        if m and m["state"] == "available":
            assert tile["value"] == m["value"] and tile["unit"] == m["unit"]
        else:
            assert tile["value"] is None and tile["state"] != "available" and tile["reason"]  # missing is never 0
    # every hotspot of the run, in priority order, with the same suggested option as the 1 · Site summary
    assert [h["id"] for h in d["hotspots"]] == [h["id"] for h in sorted(b["hotspots"], key=lambda h: h["number"])]
    t = d["tiles"]["hotspots"]
    assert t["assessed"] and t["count"] == len(b["hotspots"]) == t["high"] + t["moderate"] + t["low"]
    site = client.get(f"/api/v1/projects/{pid}/dashboard", headers=auth).json()
    for top in site["top_hotspots"]:
        row = next(h for h in d["hotspots"] if h["id"] == top["id"])
        assert (row["option"] or {}).get("title") == (top["best_option"] or {}).get("title")
    # costs per currency only, and the counts add up
    c = d["tiles"]["costs"]
    priced = sum(1 for h in d["hotspots"] if h["option"] and h["option"]["cost"]["status"] == "priced")
    assert c["priced_options"] == priced and sum(v["options"] for v in c["by_currency"].values()) == priced
    for cur, v in c["by_currency"].items():
        rows = [h["option"]["cost"] for h in d["hotspots"] if h["option"] and h["option"]["cost"]["status"] == "priced" and h["option"]["cost"]["currency"] == cur]
        assert abs(v["typical"] - sum(r["typical"] for r in rows)) < 0.01
    assert d["quality"]["modules"] and d["designs"]["designs"][0]["kind"] == "baseline"


def test_two_designs_side_by_side_with_comparison_and_reports(client, auth):
    pid = make_project(client, auth)
    upload(client, auth, pid, "sample_rates_TEST_ONLY.csv", "rate_table")
    base, alt = _two_scenarios(client, auth, pid)
    client.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=auth, json={"config": {"modules": ["urban_form", "solar_shade"]}})
    cmp_ = client.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=auth, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]]}).json()
    rid = cmp_["run_ids"][base["id"]]
    rep = client.post(f"/api/v1/analysis-runs/{rid}/reports", headers=auth, json={"comparison_id": cmp_["id"]}).json()
    d = _dash(client, auth, pid)
    names = [x["name"] for x in d["designs"]["designs"]]
    assert names[0] == base["name"] and alt["name"] in names
    a = next(x for x in d["designs"]["designs"] if x["scenario_id"] == alt["id"])
    assert a["comparable"] is True and a["cost"]["totals_by_currency"] and "green_space_pct" in a["indicators"]
    assert a["indicators"]["green_space_pct"]["state"] == "available" and d["designs"]["directions"]["green_space_pct"] == "higher"
    assert d["designs"]["latest_comparison"]["id"] == cmp_["id"] and d["designs"]["latest_comparison"]["mode"] == "no_winner"
    assert d["reports"][0]["id"] == rep["id"] and d["reports"][0]["html"]
    assert len(d["activity"]) == 2 and {x["scenario"] for x in d["activity"]} == {base["name"], alt["name"]}
    stored = client.get(f"/api/v1/analysis-runs/{rid}", headers=auth).json()["run"]["module_status"].get("planning_compliance", {})
    assert len(d["rules"]) == len(((stored.get("details") or {}).get("checks")) or [])  # one row per stored rule check


def test_viewers_read_it_and_other_users_cannot(client, auth):
    pid = make_project(client, auth)
    viewer = login(client, "viewer@example.com")
    assert client.post(f"/api/v1/projects/{pid}/members", headers=auth, json={"email": "viewer@example.com", "role": "viewer"}).status_code in (200, 201)
    assert client.get(f"/api/v1/projects/{pid}/project-dashboard", headers=viewer).status_code == 200
    stranger = login(client, "stranger@example.com")
    assert client.get(f"/api/v1/projects/{pid}/project-dashboard", headers=stranger).status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/project-dashboard").status_code == 401


def test_the_tab_is_listed_and_the_help_assistant_knows_the_page(client):
    js = client.get("/app.js").text
    assert '["dashboard", "10 · Dashboard"]' in js and "dashboard: tabDashboard" in js
    page = client.get("/api/v1/support/pages/dashboard").json()
    assert page["title"] == "10 · Dashboard"
    from urbanova.ai.support import LINK

    assert LINK.match("#/p/prj_69e5367f2ebb/dashboard")
