"""End-to-end smoke run of the Phase 1 journey against the API (in-process, live providers unless URBANOVA_OFFLINE=1).

Usage: .venv/Scripts/python scripts/smoke.py [data_dir]
Prints a compact transcript; exits non-zero on any unexpected HTTP status.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("URBANOVA_DATA_DIR", sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="urbanova_smoke_"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from urbanova.api import app  # noqa: E402

S = ROOT / "samples"
c = TestClient(app)


def ok(resp, code=(200, 201, 202)):
    if resp.status_code not in (code if isinstance(code, tuple) else (code,)):
        print("UNEXPECTED", resp.status_code, resp.text[:800])
        sys.exit(1)
    return resp.json()


def wait(run_ids, h):
    for _ in range(600):
        runs = [c.get(f"/api/v1/analysis-runs/{r}", headers=h).json()["run"] for r in run_ids]
        if all(r["status"] not in ("queued", "running") for r in runs):
            return runs
        time.sleep(1)
    raise SystemExit("timeout")


def main():
    t0 = time.time()
    # Local demo account. The password is generated per data directory and written only to the
    # git-ignored data folder, so no credential is stored in the repository.
    cred_file = Path(os.environ["URBANOVA_DATA_DIR"]) / "demo_credentials.txt"
    if cred_file.exists():
        email, password = cred_file.read_text(encoding="utf-8").split()[:2]
    else:
        email, password = "demo@example.com", secrets.token_urlsafe(12)
        ok(c.post("/api/v1/auth/register", json={"email": email, "name": "Demo User", "password": password}), 201)
        cred_file.write_text(f"{email} {password}\n(local demo account for this data directory only)\n", encoding="utf-8")
    h = {"Authorization": "Bearer " + ok(c.post("/api/v1/auth/login", json={"email": email, "password": password}))["token"]}
    print("demo account credentials written to", cred_file)
    boundary = json.loads((S / "sample_site_boundary.geojson").read_text())
    p = ok(c.post("/api/v1/projects", headers=h, json={"name": "Demo — synthetic sample site", "latitude": 30.1200, "longitude": 31.8500, "boundary": boundary,
                                                         "project_type": "mixed_use", "objectives": ["main_concerns"]}))["project"]
    pid = p["id"]
    print("project", pid, "site area", round(p["site_area_m2"]), "m2")
    r = c.post(f"/api/v1/projects/{pid}/analysis-runs", headers=h, json={})
    print("run before confirmation ->", r.status_code, r.json()["detail"][:80])
    ok(c.post(f"/api/v1/projects/{pid}/confirm-location", headers=h))
    up = ok(c.post(f"/api/v1/projects/{pid}/uploads", headers=h, data={"category": "urban_plan"}, files={"file": ("plan.geojson", (S / "sample_plan_baseline.geojson").read_bytes())}))
    print("plan:", up["outcome"]["features_extracted"], "features", up["outcome"]["by_type"])
    doc = ok(c.post(f"/api/v1/projects/{pid}/uploads", headers=h, data={"category": "planning_document"}, files={"file": ("conditions.pdf", (S / "sample_planning_conditions.pdf").read_bytes())}))
    print("document:", doc["outcome"]["candidates"], "candidates; ambiguities:", [a["message"][:60] for a in doc["outcome"]["ambiguities"]])
    rules = ok(c.get(f"/api/v1/projects/{pid}/rules", headers=h))["rules"]
    for rl in rules:
        if rl["rule_type"] in ("max_building_coverage_pct", "max_height_m", "min_green_space_pct", "min_setback_m") or (rl["rule_type"] == "plot_area_m2" and rl["value"] == 40000):
            ok(c.post(f"/api/v1/rules/{rl['id']}/decision", headers=h, json={"decision": "verified"}))
    ok(c.post(f"/api/v1/projects/{pid}/uploads", headers=h, data={"category": "rate_table"}, files={"file": ("rates.csv", (S / "sample_rates_TEST_ONLY.csv").read_bytes())}))
    base = ok(c.post(f"/api/v1/projects/{pid}/scenarios", headers=h, json={"kind": "baseline"}))
    alt = ok(c.post(f"/api/v1/projects/{pid}/scenarios", headers=h, json={"kind": "alternative", "name": "Greener with shade"}))
    park = next(f for f in alt["features"]["features"] if f["properties"].get("name") == "Surface car park")
    ok(c.patch(f"/api/v1/scenarios/{alt['id']}", headers=h, json={"changes": [{"op": "remove", "feature_id": park["id"]}]}))
    ok(c.post(f"/api/v1/scenarios/{alt['id']}/import", headers=h, files={"file": ("adds.geojson", (S / "sample_alternative_additions.geojson").read_bytes())}))
    rd = ok(c.post(f"/api/v1/projects/{pid}/readiness-check", headers=h, json={}))
    print("readiness:", {a["analysis"]: a["status"] for a in rd["analyses"]})
    runs = ok(c.post(f"/api/v1/projects/{pid}/analysis-runs/all-scenarios", headers=h, json={"config": {}}))
    done = wait([x["id"] for x in runs], h)
    for d in done:
        print("run", d["id"], d["status"], {k: v["status"] for k, v in d["module_status"].items()})
        for k, v in d["module_status"].items():
            if v["status"] != "completed":
                print(f"    {k}: {v['status']} — {(v.get('reason') or '; '.join(v.get('warnings') or []))[:160]}")
    b_run = next(d for d in done if d["scenario_id"] == base["id"])
    bundle = ok(c.get(f"/api/v1/analysis-runs/{b_run['id']}", headers=h))
    for m in bundle["metrics"]:
        val = m["value"] if m["state"] == "available" else f"[{m['state']}] {m['reason'][:60]}"
        print(f"  {m['module']:20s} {m['metric_name']:45s} {val} {m['unit'] or ''}")
    for f in bundle["findings"]:
        prov = " [PROVISIONAL THRESHOLD]" if (f.get("threshold") or {}).get("status") == "provisional_not_approved" else ""
        print("  FINDING", f["severity"] + prov, f["classification"], "-", f["title"])
    cmp_ = ok(c.post(f"/api/v1/projects/{pid}/scenario-comparisons", headers=h, json={"baseline_id": base["id"], "alternative_ids": [alt["id"]],
                                                                                       "criteria": [{"metric": "green_space_pct", "weight": 1}, {"metric": "solar_open_space_exposed_pct_1500", "weight": 1}]}))
    res = cmp_["result"]
    print("comparison comparable:", res["comparable"], res.get("issues"))
    for row in res.get("rows", []):
        print(f"  {row['metric']:42s} {row['baseline'].get('value')} -> {row['alternative'].get('value')} {row['outcome']}")
    print("  cost:", json.dumps(res.get("costs"), default=str)[:400])
    print("  ranking:", json.dumps((res.get("ranking") or {}).get("aggregate")))
    rep = ok(c.post(f"/api/v1/analysis-runs/{b_run['id']}/reports", headers=h, json={"comparison_id": cmp_["id"]}))
    pdf = c.get(f"/api/v1/reports/{rep['id']}/download?format=pdf", headers=h)
    print("report", rep["status"], "-", rep["summary"]["label"], "| pdf bytes", len(pdf.content), pdf.content[:5])
    print("objectives:", [(o["objective"], o["answerable"]) for o in bundle["objectives"]])
    print("ask:", ok(c.post(f"/api/v1/analysis-runs/{b_run['id']}/ask", headers=h, json={"question": "How many degrees cooler will the alternative be?"}))["answer"][:90])
    print("export bytes", len(c.get(f"/api/v1/analysis-runs/{b_run['id']}/export", headers=h).content))
    print(f"done in {time.time() - t0:.0f}s; data dir {os.environ['URBANOVA_DATA_DIR']}")


if __name__ == "__main__":
    main()
