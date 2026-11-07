"""Baseline and alternative design scenarios (FR-25, FR-50, Module 6).

* The baseline is an immutable snapshot of the reviewed plan features.
* An alternative starts as an independent copy; every change is logged.
* Archiving never deletes: historical runs and comparisons stay explainable.
"""

from __future__ import annotations

import copy
import hashlib
import json

from shapely.geometry import mapping, shape

from . import db, geo, plans


class ScenarioError(ValueError):
    pass


def design_hash(fc: dict) -> str:
    return hashlib.sha256(json.dumps(fc, sort_keys=True).encode()).hexdigest()[:16]


def create_baseline(project: dict, user: dict, name: str | None = None) -> dict:
    feats = plans.active_features(project["id"])
    if not feats:
        raise ScenarioError("There are no plan features. Upload an urban plan (GeoJSON or DXF) first.")
    unconfirmed = [f for f in feats if plans.has_unconfirmed_units([f])]
    if unconfirmed:
        # FR-02: areas from a drawing with unresolved scale are never presented as reliable.
        raise ScenarioError(
            f"{len(unconfirmed)} feature(s) come from a DXF drawing with undeclared units. Confirm on the Inputs tab that the "
            "drawing units are metres (or re-export the drawing with its units set) before creating the baseline."
        )
    unclassified = [f for f in feats if f["feature_type"] == "unclassified"]
    existing = db.select("scenarios", "project_id = ? AND kind = 'baseline' AND status = 'active'", [project["id"]])
    fc = plans.feature_collection(feats)
    now = db.now()
    for old in existing:
        # Replacing the baseline is explicit and traceable (Module 6: one active baseline).
        db.update("scenarios", "id", old["id"], {"status": "superseded", "updated_at": now})
    scen = {
        "id": db.new_id("scn"), "project_id": project["id"], "name": name or "Baseline (original design)", "kind": "baseline",
        "parent_id": existing[0]["id"] if existing else None,
        "description": "Immutable snapshot of the reviewed plan features.", "features": fc,
        "modifications": [{"at": now, "by": user["id"], "event": "baseline_created", "feature_count": len(feats),
                           "unclassified_excluded_from_classes": len(unclassified), "superseded": [o["id"] for o in existing]}],
        "status": "active", "design_hash": design_hash(fc), "created_by": user["id"], "created_at": now, "updated_at": now,
    }
    db.insert("scenarios", scen)
    return scen


def create_alternative(project: dict, user: dict, name: str, description: str | None = None, from_id: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ScenarioError("Give the alternative a name.")
    base = db.get("scenarios", from_id) if from_id else None
    if not base:
        base = next(iter(db.select("scenarios", "project_id = ? AND kind = 'baseline' AND status = 'active'", [project["id"]])), None)
    if not base or base["project_id"] != project["id"]:
        raise ScenarioError("Create the baseline scenario first.")
    active = db.select("scenarios", "project_id = ? AND kind = 'alternative' AND status = 'active'", [project["id"]])
    if len(active) >= 5:
        raise ScenarioError("The Phase 1 limit is 5 active alternatives. Archive one before creating another.")
    if any(s["name"].lower() == name.lower() for s in active):
        raise ScenarioError("An active alternative already has this name.")
    fc = copy.deepcopy(base["features"])
    now = db.now()
    scen = {
        "id": db.new_id("scn"), "project_id": project["id"], "name": name, "kind": "alternative", "parent_id": base["id"],
        "description": (description or "").strip() or None, "features": fc,
        "modifications": [{"at": now, "by": user["id"], "event": "created_from", "source_scenario": base["id"], "source_name": base["name"]}],
        "status": "active", "design_hash": design_hash(fc), "created_by": user["id"], "created_at": now, "updated_at": now,
    }
    db.insert("scenarios", scen)
    return scen


EDITABLE_PROPS = {"feature_type", "height_m", "floors", "canopy_radius_m"}


def modify(scenario: dict, user: dict, ops: list[dict]) -> dict:
    """Apply edits to an alternative. ops: {op: update|remove|add, feature_id, properties, geometry}."""
    if scenario["kind"] == "baseline":
        raise ScenarioError("The baseline is immutable. Create an alternative to explore changes.")
    if scenario["status"] != "active":
        raise ScenarioError("Archived scenarios cannot be edited.")
    if not ops:
        raise ScenarioError("No changes were supplied.")
    fc = copy.deepcopy(scenario["features"])
    by_id = {f["id"]: f for f in fc["features"]}
    log = []
    project = db.get("projects", scenario["project_id"])
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    for op in ops:
        kind = op.get("op")
        if kind == "update":
            f = by_id.get(op.get("feature_id"))
            if not f:
                raise ScenarioError(f"Feature {op.get('feature_id')} is not in this scenario.")
            props = op.get("properties") or {}
            bad = set(props) - EDITABLE_PROPS
            if bad:
                raise ScenarioError(f"These properties cannot be edited: {', '.join(sorted(bad))}.")
            before = {k: f["properties"].get(k) for k in props}
            for k, v in props.items():
                if k == "feature_type":
                    if v not in plans.FEATURE_TYPES:
                        raise ScenarioError(f"Unknown feature type '{v}'.")
                    f["properties"][k] = v
                elif v in (None, ""):
                    f["properties"].pop(k, None)
                else:
                    try:
                        num = float(v)
                    except (TypeError, ValueError):
                        raise ScenarioError(f"{k} must be a number.")
                    if num <= 0 or num > 1000:
                        raise ScenarioError(f"{k} must be between 0 and 1000.")
                    f["properties"][k] = int(num) if k == "floors" else num
                    f["properties"][f"{'height' if k == 'height_m' else k.split('_')[0]}_source"] = f"scenario edit ({scenario['name']})"
            log.append({"op": "update", "feature_id": f["id"], "before": before, "after": props})
        elif kind == "remove":
            fid = op.get("feature_id")
            if fid not in by_id:
                raise ScenarioError(f"Feature {fid} is not in this scenario.")
            fc["features"] = [f for f in fc["features"] if f["id"] != fid]
            log.append({"op": "remove", "feature_id": fid, "feature_type": by_id[fid]["properties"].get("feature_type")})
            by_id.pop(fid)
        elif kind == "add":
            gj = op.get("geometry")
            props = op.get("properties") or {}
            ftype = props.get("feature_type")
            if ftype not in plans.FEATURE_TYPES or ftype == "unclassified":
                raise ScenarioError("Added features need a valid feature_type.")
            try:
                g = shape(gj)
            except Exception:
                raise ScenarioError("The added geometry is not valid GeoJSON.")
            geo.check_wgs84_bounds(g)
            if not g.is_valid or g.is_empty:
                raise ScenarioError("The added geometry is invalid or empty.")
            site_pt = geo.to_local(shape({"type": "Point", "coordinates": [project["longitude"], project["latitude"]]}), local)
            if geo.to_local(g, local).distance(site_pt) > 5000:
                raise ScenarioError("The added feature is more than 5 km from the site.")
            fid = db.new_id("ftr")
            clean = {k: v for k, v in props.items() if k in EDITABLE_PROPS}
            clean.update(feature_id=fid, feature_type=ftype, review_status="proposed", classification_source=f"added in scenario '{scenario['name']}'")
            fc["features"].append({"type": "Feature", "id": fid, "geometry": mapping(g), "properties": clean})
            log.append({"op": "add", "feature_id": fid, "feature_type": ftype})
        else:
            raise ScenarioError("Each change needs op = update, remove or add.")
    mods = list(scenario.get("modifications") or []) + [{"at": db.now(), "by": user["id"], "event": "modified", "changes": log}]
    db.update("scenarios", "id", scenario["id"], {"features": fc, "modifications": mods, "design_hash": design_hash(fc), "updated_at": db.now()})
    return db.get("scenarios", scenario["id"])


def import_features_into(scenario: dict, user: dict, fc_obj: dict) -> dict:
    """Add all features from an uploaded GeoJSON FeatureCollection to an alternative."""
    ops = []
    for f in fc_obj.get("features", []):
        props = dict(f.get("properties") or {})
        ftype, _, _ = plans.classify(props)
        props["feature_type"] = ftype
        if ftype == "unclassified":
            raise ScenarioError("Every imported feature needs a recognisable feature_type property.")
        ops.append({"op": "add", "geometry": f.get("geometry"), "properties": props})
    return modify(scenario, user, ops)


def archive(scenario: dict, user: dict) -> dict:
    if scenario["kind"] == "baseline":
        raise ScenarioError("The baseline cannot be archived; replace it by creating a new baseline.")
    mods = list(scenario.get("modifications") or []) + [{"at": db.now(), "by": user["id"], "event": "archived"}]
    db.update("scenarios", "id", scenario["id"], {"status": "archived", "modifications": mods, "updated_at": db.now()})
    return db.get("scenarios", scenario["id"])


def public(s: dict, include_features: bool = True) -> dict:
    out = dict(s)
    if not include_features:
        out.pop("features", None)
        out["feature_count"] = len(s["features"]["features"])
    return out
