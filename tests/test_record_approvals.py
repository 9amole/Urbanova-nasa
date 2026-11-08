"""scripts/record_approvals.py: decisions are applied only with a named approver, and invalid forms change nothing."""

import csv
import json
import shutil
import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import record_approvals as RA  # noqa: E402


def _setup(tmp_path):
    basis = tmp_path / "basis.json"
    shutil.copy(RA.BASIS, basis)
    form = tmp_path / "form.csv"
    assert RA.main(["export", str(form), "--basis", str(basis)]) == 0
    rows = list(csv.DictReader(form.open(encoding="utf-8-sig")))
    return basis, form, rows


def _write(form, rows):
    with form.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, RA.COLUMNS)
        w.writeheader()
        w.writerows(rows)


def _rec(basis, rid):
    return next(r for r in json.loads(basis.read_text(encoding="utf-8"))["values"] if r["id"] == rid)


def test_export_lists_only_pending_values(tmp_path):
    basis, form, rows = _setup(tmp_path)
    ids = {r["id"] for r in rows}
    assert "hotspots.deficit_distance_m" in ids and "climate.TX35" in ids
    assert "surface_heat.block_size_m" not in ids  # statistical basis, not a pending choice


def test_approve_and_change_are_recorded_with_the_approver(tmp_path):
    from urbanova import basis as B

    basis, form, rows = _setup(tmp_path)
    for r in rows:
        if r["id"] == "hotspots.deficit_distance_m":
            r.update(decision="approve", approved_by="Dr. A (supervisor)", approved_on="2026-11-10")
        if r["id"] == "interventions.tree":
            r.update(decision="change", new_value=json.dumps({"spacings_m": [8, 10, 12], "canopy_radius_m": 3, "height_m": 6, "building_clearance_m": 2}),
                     approved_by="Dr. A (supervisor)", note="Local nursery stock")
    _write(form, rows)
    assert RA.main(["apply", str(form), "--basis", str(basis)]) == 0
    d = _rec(basis, "hotspots.deficit_distance_m")
    assert d["approved_by"] == "Dr. A (supervisor)" and d["approved_on"] == "2026-11-10" and d["value"] == 30
    assert not B.record_is_provisional(d)
    t = _rec(basis, "interventions.tree")
    assert t["value"]["spacings_m"] == [8, 10, 12] and "Changed from" in t["approval_note"] and t["approved_on"]


def test_rows_without_an_approver_or_with_errors_change_nothing(tmp_path):
    basis, form, rows = _setup(tmp_path)
    before = basis.read_text(encoding="utf-8")
    rows[0].update(decision="approve")  # no approver
    rows[1].update(decision="change", new_value="not json", approved_by="X")
    _write(form, rows)
    assert RA.main(["apply", str(form), "--basis", str(basis)]) == 2
    assert basis.read_text(encoding="utf-8") == before


def test_a_provisional_value_becomes_an_approved_project_choice(tmp_path):
    basis, form, rows = _setup(tmp_path)
    for r in rows:
        if r["id"] == "climate.TX35":
            r.update(decision="approve", approved_by="Dr. A (supervisor)")
    _write(form, rows)
    assert RA.main(["apply", str(form), "--basis", str(basis), "--dry-run"]) == 0
    assert _rec(basis, "climate.TX35")["basis_type"] == "provisional"  # dry run wrote nothing
    assert RA.main(["apply", str(form), "--basis", str(basis)]) == 0
    r = _rec(basis, "climate.TX35")
    assert r["basis_type"] == "project_choice" and r["approved_by"]
