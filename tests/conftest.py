import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLES = ROOT / "samples"


class _SyncExecutor:
    def submit(self, fn, *a, **kw):
        fn(*a, **kw)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated data dir, offline providers, synchronous jobs."""
    from urbanova import db, orchestrator
    from urbanova.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(orchestrator, "_executor", _SyncExecutor())
    db.reset_connection()
    yield settings
    db.reset_connection()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from urbanova.api import app

    return TestClient(app)


def login(client, email="tester@example.com"):
    client.post("/api/v1/auth/register", json={"email": email, "name": "Tester", "password": "correct-horse-battery"})
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def auth(client):
    return login(client)


def boundary():
    return json.loads((SAMPLES / "sample_site_boundary.geojson").read_text())


def make_project(client, auth, with_boundary=True, confirm=True, **extra):
    body = {"name": "Test site", "latitude": 30.1200, "longitude": 31.8500, **extra}
    if with_boundary:
        body["boundary"] = boundary()
    r = client.post("/api/v1/projects", headers=auth, json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    if confirm:
        assert client.post(f"/api/v1/projects/{pid}/confirm-location", headers=auth).status_code == 200
    return pid


def upload(client, auth, pid, name, category, content=None, **form):
    data = content if content is not None else (SAMPLES / name).read_bytes()
    return client.post(f"/api/v1/projects/{pid}/uploads", headers=auth, data={"category": category, **form}, files={"file": (name, data)})


def run(client, auth, pid, scenario_id=None, config=None):
    r = client.post(f"/api/v1/projects/{pid}/analysis-runs", headers=auth, json={"scenario_id": scenario_id, "config": config or {}})
    assert r.status_code == 202, r.text
    return client.get(f"/api/v1/analysis-runs/{r.json()['id']}", headers=auth).json()


def unapprove(monkeypatch, *ids, clear_value=False):
    """Make basis values unapproved (or empty) for one test, to exercise the provisional path whatever basis.json records."""
    from urbanova import basis

    for i in ids:
        rec = basis.get(i)
        monkeypatch.setitem(rec, "approved_by", None)
        if clear_value:
            monkeypatch.setitem(rec, "value", None)
