"""Evaluate AI models on a fixed set of evidence packs (PHASE2_SPEC §5 acceptance, §9 model choice).

Builds 30 evidence packs offline (synthetic sample site and the New Cairo case-study designs, each analysed with a
synthetic hot day, under five sets of selected questions), sends each pack to every model to test, and records
whether the output passed the grounding validator first time, after the one retry, or fell back, plus latency.
Writes docs/AI_EVALUATION.md. Uses a private cache so earlier results are not reused.

Usage (the key is read from the environment and never printed):
    set URBANOVA_LLM_GROQ_API_KEY=...            (PowerShell: $env:URBANOVA_LLM_GROQ_API_KEY = "...")
    .venv/Scripts/python scripts/eval_ai.py groq:openai/gpt-oss-120b groq:llama-3.3-70b-versatile
    .venv/Scripts/python scripts/eval_ai.py ollama:qwen3:8b --out=docs/AI_EVALUATION_LOCAL.md   (local model, no key)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

OBJECTIVE_SETS = [("main_concerns",), ("shade",), ("main_concerns", "shade", "vegetation"), ("surface_heat", "planning_compliance"), ("compare_alternatives", "data_gaps")]


def designs() -> list[tuple[str, dict, bool]]:
    s = ROOT / "samples"
    nc = ROOT / "case_studies" / "new_cairo_nc01"
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    out = [("sample baseline", load(s / "sample_plan_baseline.geojson"), True), ("sample baseline, no options", load(s / "sample_plan_baseline.geojson"), False)]
    base = out[0][1]
    adds = load(s / "sample_alternative_additions.geojson")
    out.append(("sample alternative", {"type": "FeatureCollection", "features": base["features"] + adds["features"]}, True))
    noh = json.loads(json.dumps(base))
    for f in noh["features"]:
        f["properties"].pop("height_m", None)
    out.append(("sample, no heights", noh, True))
    for name in ("nc01_baseline_plan.geojson", "nc01_alternative_full.geojson"):
        if (nc / name).exists():
            out.append((f"New Cairo {name.split('_')[1]}", load(nc / name), True))
    return out


def bundles() -> list[tuple[str, dict]]:
    from test_stage_s2 import sample_context
    from test_stage_s3 import test_rates

    from urbanova.engine.core import run_design
    from urbanova.orchestrator import answer_objectives

    out = []
    for name, fc, options in designs():
        with tempfile.TemporaryDirectory() as tmp:
            ctx = sample_context(Path(tmp), features=fc)
            if name.startswith("New Cairo"):
                b = json.loads((ROOT / "case_studies" / "new_cairo_nc01" / "nc01_site_boundary.geojson").read_text(encoding="utf-8"))
                g = b["features"][0]["geometry"] if b.get("type") == "FeatureCollection" else b.get("geometry", b)
                from shapely.geometry import shape

                c = shape(g).centroid
                ctx.project = {**ctx.project, "boundary": g, "latitude": c.y, "longitude": c.x}
            ctx.rates = test_rates()
            mods = ["urban_form", "solar_shade", "heat_stress", "hotspots"] + (["interventions"] if options else [])
            res = run_design(ctx, mods)
        avail = {m["metric_name"] for m in res["metrics"] if m["state"] == "available"}
        for objs in OBJECTIVE_SETS:
            out.append((f"{name} / {'+'.join(objs)}", {"run": {"id": "run_eval", "status": res["status"], "module_status": res["module_status"]},
                        "metrics": res["metrics"], "findings": res["findings"], "hotspots": res["hotspots"], "site_context": res["site_context"],
                        "recommendations": res["recommendations"], "objectives": answer_objectives(list(objs), res["module_status"], avail)}))
    return out


def main(models: list[str], out_path: str = "docs/AI_EVALUATION.md") -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from urbanova.ai import insights, llm
    from urbanova.settings import settings

    if not models:
        sys.exit("Give one or more models as provider:model, e.g. groq:openai/gpt-oss-120b")
    settings.offline = False
    settings.data_dir = Path(tempfile.mkdtemp(prefix="urbanova-eval-"))  # private cache and call budget
    packs = bundles()
    print(f"{len(packs)} evidence packs")
    rows = []
    for spec in models:
        prov, _, model = spec.partition(":")
        os.environ["URBANOVA_LLM_PROVIDERS"] = prov
        os.environ[f"URBANOVA_LLM_{prov.upper()}_MODEL"] = model
        if not any(p["ready"] for p in llm.configured()):
            print(f"skip {spec}: {llm.configured()[0]['reason']}")
            continue
        stats = {"first": 0, "retry": 0, "fallback": 0, "provider_failure": 0, "seconds": []}
        for name, b in packs:
            t0 = time.monotonic()
            out = insights.generate(b, True)
            stats["seconds"].append(time.monotonic() - t0)
            outcomes = [a["outcome"] for a in out["attempts"]]
            if out["generated_by"] == "ai":
                stats["first" if outcomes[-1:] == ["accepted"] and len(outcomes) == 1 else "retry"] += 1
            else:
                stats["provider_failure" if "provider_failure" in outcomes or not outcomes else "fallback"] += 1
                errs = [e for a in out["attempts"] for e in a.get("errors") or []][:3] or [a.get("detail") for a in out["attempts"]] or [out.get("reason")]
                print(f"  fallback: {name}: {errs}")
            time.sleep(float(os.environ.get("EVAL_PAUSE_S", "40")))  # one pack per ~minute keeps inside free-tier per-minute token limits
        n = len(packs)
        sec = sorted(stats["seconds"])
        rows.append((spec, n, stats, sec[len(sec) // 2] if sec else 0))
        print(spec, stats["first"], stats["retry"], stats["fallback"], stats["provider_failure"])
    lines = ["# AI model evaluation", "", f"Generated by `scripts/eval_ai.py` on {time.strftime('%Y-%m-%d')} with {len(packs)} evidence packs "
             "(synthetic sample site and New Cairo case-study designs, synthetic hot day, five sets of selected questions).", "",
             "| Model | Packs | Accepted first time | Accepted after retry | Fallback (validation) | Provider failure | Median seconds |", "|---|---|---|---|---|---|---|"]
    for spec, n, st, med in rows:
        lines.append(f"| {spec} | {n} | {st['first']} | {st['retry']} | {st['fallback']} | {st['provider_failure']} | {med:.1f} |")
    lines += ["", "Every accepted output passed the grounding validator (PHASE2_SPEC §5 step 4); fallbacks show the deterministic summary."]
    (ROOT / out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", out_path)


if __name__ == "__main__":
    args = sys.argv[1:]
    out = next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), "docs/AI_EVALUATION.md")
    main([a for a in args if not a.startswith("--out=")], out)
