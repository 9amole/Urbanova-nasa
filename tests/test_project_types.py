"""Project types from other clients (the .NET backend sends "Residential", "Mixed use"): stored in the canonical form."""

import pytest

from conftest import make_project


@pytest.mark.parametrize("sent, stored", [("residential", "residential"), ("Residential", "residential"), ("Mixed use", "mixed_use"),
                                          ("public-space", "public_space"), (" COMMERCIAL ", "commercial"), ("", None)])
def test_project_types_are_accepted_in_any_spelling(client, auth, sent, stored):
    pid = make_project(client, auth, project_type=sent, confirm=False)
    assert client.get(f"/api/v1/projects/{pid}", headers=auth).json()["project"]["project_type"] == stored


def test_an_unknown_project_type_is_refused_with_the_list(client, auth):
    r = client.post("/api/v1/projects", headers=auth, json={"name": "x", "latitude": 30.12, "longitude": 31.85, "project_type": "spaceport"})
    assert r.status_code == 422 and "residential" in r.json()["detail"]
    pid = make_project(client, auth, confirm=False)
    r = client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"project_type": "Industrial"})
    assert r.status_code == 200 and r.json()["project"]["project_type"] == "industrial"
    assert client.patch(f"/api/v1/projects/{pid}", headers=auth, json={"project_type": "spaceport"}).status_code == 422
