"""Record supervisor decisions on basis values (PHASE2_SPEC §13, item 1).

A value whose basis is a `project_choice` without `approved_by` (or `provisional`) is labelled PROVISIONAL and cannot
produce the top severity. This script exports the pending values as a CSV form, and writes the filled-in form back into
urbanova/config/basis.json. It never invents an approval: a row is applied only when it names the person who decided.

  Export the form:   .venv/Scripts/python scripts/record_approvals.py export decisions.csv
  Apply decisions:   .venv/Scripts/python scripts/record_approvals.py apply decisions.csv [--dry-run]

Form columns: id, current_value, unit, basis_type, reason, decision, new_value, approved_by, approved_on, note.
  decision = approve      keep the value; set approved_by / approved_on (a provisional value becomes a project_choice)
  decision = change       set the value to new_value (JSON, e.g. 25 or [6, 9, 12]) and approve it
  decision = (blank)      leave the record untouched
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASIS = ROOT / "urbanova" / "config" / "basis.json"
COLUMNS = ["id", "current_value", "unit", "basis_type", "reason", "decision", "new_value", "approved_by", "approved_on", "note"]


def pending(records: list[dict]) -> list[dict]:
    return [r for r in records if r["basis_type"] == "provisional" or (r["basis_type"] == "project_choice" and not r.get("approved_by"))]


def export(basis_path: Path, out: Path) -> int:
    recs = pending(json.loads(basis_path.read_text(encoding="utf-8"))["values"])
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()
        for r in recs:
            w.writerow({"id": r["id"], "current_value": json.dumps(r["value"], ensure_ascii=False), "unit": r.get("unit") or "",
                        "basis_type": r["basis_type"], "reason": r.get("reason") or r.get("applicability") or ""})
    print(f"wrote {len(recs)} pending values to {out}")
    return 0


def apply(basis_path: Path, form: Path, dry_run: bool = False) -> int:
    data = json.loads(basis_path.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["values"]}
    errors, changes = [], []
    with form.open(encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            decision = (row.get("decision") or "").strip().lower()
            if not decision:
                continue
            rid = (row.get("id") or "").strip()
            who = (row.get("approved_by") or "").strip()
            when = (row.get("approved_on") or "").strip() or date.today().isoformat()
            if rid not in by_id:
                errors.append(f"line {n}: unknown id {rid!r}")
                continue
            if decision not in ("approve", "change"):
                errors.append(f"line {n}: decision must be 'approve', 'change' or blank, not {decision!r}")
                continue
            if not who:
                errors.append(f"line {n}: {rid}: approved_by is required (name and role of the person who decided)")
                continue
            try:
                date.fromisoformat(when)
            except ValueError:
                errors.append(f"line {n}: {rid}: approved_on must be YYYY-MM-DD")
                continue
            rec = by_id[rid]
            new = rec["value"]
            if decision == "change":
                try:
                    new = json.loads(row.get("new_value") or "")
                except json.JSONDecodeError:
                    errors.append(f"line {n}: {rid}: new_value must be JSON (a number, list or object)")
                    continue
                if type(new) is not type(rec["value"]) and not (isinstance(new, (int, float)) and isinstance(rec["value"], (int, float))):
                    errors.append(f"line {n}: {rid}: new_value has a different shape from the current value {json.dumps(rec['value'])}")
                    continue
            changes.append((rec, new, who, when, (row.get("note") or "").strip(), decision))
    if errors:
        print("Nothing was written:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 2
    for rec, new, who, when, note, decision in changes:
        old = rec["value"]
        rec["value"] = new
        if rec["basis_type"] == "provisional":
            rec["basis_type"] = "project_choice"
        rec["approved_by"], rec["approved_on"] = who, when
        if note or decision == "change":
            rec["approval_note"] = (f"Changed from {json.dumps(old, ensure_ascii=False)}. " if decision == "change" else "") + note
        print(f"{decision:8s} {rec['id']}: {json.dumps(new, ensure_ascii=False)} — {who}, {when}")
    if not dry_run and changes:
        basis_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(changes)} decision(s) {'checked (dry run)' if dry_run else 'recorded'}; {len(pending(data['values']))} value(s) still pending.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["export", "apply"])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--basis", type=Path, default=BASIS, help="basis.json to read and update (default: the package copy)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return export(a.basis, a.csv) if a.action == "export" else apply(a.basis, a.csv, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
