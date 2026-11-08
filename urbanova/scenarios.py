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


def _check_geometry(gj, project: dict, local: str):
    """A drawn or edited geometry: valid GeoJSON, WGS 84, non-empty, within 5 km of the site."""
    try:
        g = shape(gj)
    except Exception:
        raise ScenarioError("The geometry is not valid GeoJSON.")
    geo.check_wgs84_bounds(g)
    if not g.is_valid or g.is_empty:
        raise ScenarioError("The geometry is invalid or empty.")
    site_pt = geo.to_local(shape({"type": "Point", "coordinates": [project["longitude"], project["latitude"]]}), local)
    if geo.to_local(g, local).distance(site_pt) > 5000:
        raise ScenarioError("The feature is more than 5 km from the site.")
    return g


def modify(scenario: dict, user: dict, ops: list[dict]) -> dict:
    """Apply edits to an alternative. ops: {op: update|remove|add, feature_id, properties, geometry}."""
    if scenario["kind"] == "baseline":
        raise ScenarioError("The baseline is immutable. Create an alternative to explore changes.")
    if scenario["status"] != "active":
        raise ScenarioError("Only active scenarios can be edited (archived scenarios are kept as they were; approve a draft first).")
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
            if op.get("geometry") is not None:
                g = _check_geometry(op["geometry"], project, local)
                log.append({"op": "update_geometry", "feature_id": f["id"], "before": f["geometry"], "after": mapping(g)})
                f["geometry"] = mapping(g)
                if not props:
                    continue
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
            g = _check_geometry(gj, project, local)
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


def create_draft_from_package(project: dict, user: dict, run: dict) -> dict:
    """Save a run's budget package (PHASE2_SPEC §6.6) as a DRAFT alternative. A draft is never analysed: runs accept
    only active scenarios, so it must be approved first."""
    pkg = (((run.get("module_status") or {}).get("interventions") or {}).get("details") or {}).get("package")
    if not pkg or pkg.get("status") != "draft" or not pkg.get("features"):
        raise ScenarioError("This run has no package of options within a budget. Run the analysis with a budget (budget_amount and budget_currency).")
    return create_draft(project, user, run, pkg["features"], f"Draft: options within {pkg['budget']['amount']:g} {pkg['budget']['currency']} ({run['id'][-6:]})",
                        {"event": "draft_from_package", "option_ids": pkg["option_ids"], "cost": pkg.get("cost")})


def create_draft(project: dict, user: dict, run: dict, fc: dict, name: str, provenance: dict) -> dict:
    """Save a design generated from measured options as a DRAFT alternative of the run's scenario (never analysed until approved)."""
    base = db.get("scenarios", run["scenario_id"]) if run.get("scenario_id") else None
    if not base or base["project_id"] != project["id"]:
        raise ScenarioError("The run's design scenario was not found.")
    fc = copy.deepcopy(fc)
    now = db.now()
    scen = {
        "id": db.new_id("scn"), "project_id": project["id"], "name": name[:120], "kind": "alternative", "parent_id": base["id"],
        "description": "Draft generated from measured intervention options. Review it, then approve it to analyse it as an alternative.",
        "features": fc, "status": "draft", "design_hash": design_hash(fc), "created_by": user["id"], "created_at": now, "updated_at": now,
        "modifications": [{"at": now, "by": user["id"], "run_id": run["id"], "source_scenario": base["id"], **provenance}],
    }
    db.insert("scenarios", scen)
    return scen


def draft_from_option(project: dict, user: dict, run: dict, option_id: str) -> dict:
    """Apply one measured option (PHASE2_SPEC §8.2 'apply recommendation'): replay its recipe on the run's design as a draft."""
    from . import interventions

    details = (((run.get("module_status") or {}).get("interventions") or {}).get("details") or {})
    opt = next((o for h in details.get("per_hotspot") or [] for o in h["options"] if o["id"] == option_id), None)
    if not opt:
        raise ScenarioError("That option is not part of this run.")
    base = db.get("scenarios", run["scenario_id"]) if run.get("scenario_id") else None
    if not base:
        raise ScenarioError("The run's design scenario was not found.")
    hs = next((h for h in db.select("hotspots", "run_id = ?", [run["id"]]) if h["number"] == opt["recipe"]["hotspot_number"]), None)
    if not hs:
        raise ScenarioError("The option's hotspot was not found in this run.")
    local = geo.utm_crs_for(project["latitude"], project["longitude"])
    hs_geom = geo.to_local(shape(hs["geometry"]), local) if hs.get("geometry") else None
    site = geo.to_local(shape(project["boundary"]), local) if project.get("boundary") else None
    design = interventions.Design(base["features"], local)
    applied = interventions.apply_recipe(design, opt["recipe"], hs_geom, hs, site)
    if not applied:
        raise ScenarioError("The option could not be applied to the current design (it may have changed since the run).")
    return create_draft(project, user, run, design.fc, f"Draft: {opt['title']} ({opt['intensity_label']}) for hotspot {hs['number']}",
                        {"event": "draft_from_option", "option_id": option_id, "intervention_id": opt["intervention_id"], "hotspot_number": hs["number"],
                         "measured_effect": opt.get("effect"), "cost": opt.get("cost")})


def approve_draft(scenario: dict, user: dict) -> dict:
    """Turn a draft into an active alternative (the user's approval), within the alternatives limit."""
    if scenario["status"] != "draft":
        raise ScenarioError("Only a draft scenario can be approved.")
    active = db.select("scenarios", "project_id = ? AND kind = 'alternative' AND status = 'active'", [scenario["project_id"]])
    if len(active) >= 5:
        raise ScenarioError("The Phase 1 limit is 5 active alternatives. Archive one before approving this draft.")
    if any(s["name"].lower() == scenario["name"].lower() for s in active):
        raise ScenarioError("An active alternative already has this name.")
    mods = (scenario.get("modifications") or []) + [{"at": db.now(), "by": user["id"], "event": "draft_approved"}]
    db.update("scenarios", "id", scenario["id"], {"status": "active", "modifications": mods, "updated_at": db.now()})
    return db.get("scenarios", scenario["id"])
