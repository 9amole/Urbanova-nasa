"""Command-line runner for the engine.

    python -m urbanova.engine analyse request.json --out out/ [--result result.json]
    python -m urbanova.engine extract-plan plan.dxf --lat 30.12 --lon 31.85 [--epsg 32636] [--result plan.json]
    python -m urbanova.engine extract-rules conditions.pdf [--result rules.json]
    python -m urbanova.engine schema --out schemas/
    python -m urbanova.engine validate result.json

Exit codes: 0 success, 2 invalid input (errors printed as JSON on stdout), 1 unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _emit(obj, result_path: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if result_path:
        Path(result_path).write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)


def main(argv: list[str] | None = None) -> int:
    from . import api
    from .contract import AnalysisRequest, AnalysisResult

    p = argparse.ArgumentParser(prog="python -m urbanova.engine", description="URBANOVA analysis engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyse", help="run an analysis request")
    a.add_argument("request")
    a.add_argument("--out", required=True, help="directory for map overlays and GeoTIFFs")
    a.add_argument("--result", help="write the result JSON here instead of stdout")
    e = sub.add_parser("extract-plan", help="extract reviewable features from a GeoJSON or DXF plan")
    e.add_argument("file")
    e.add_argument("--lat", type=float, required=True)
    e.add_argument("--lon", type=float, required=True)
    e.add_argument("--epsg")
    e.add_argument("--result")
    r = sub.add_parser("extract-rules", help="extract candidate planning conditions from a PDF")
    r.add_argument("file")
    r.add_argument("--result")
    s = sub.add_parser("schema", help="write the request and result JSON Schemas")
    s.add_argument("--out", required=True)
    v = sub.add_parser("validate", help="check a result file against the result schema")
    v.add_argument("result")
    args = p.parse_args(argv)
    try:
        if args.cmd == "analyse":
            request = json.loads(Path(args.request).read_text(encoding="utf-8"))
            _emit(api.analyse(request, args.out), args.result)
        elif args.cmd == "extract-plan":
            path = Path(args.file)
            _emit(api.extract_plan(path.name, path.read_bytes(), {"latitude": args.lat, "longitude": args.lon}, args.epsg), args.result)
        elif args.cmd == "extract-rules":
            path = Path(args.file)
            _emit(api.extract_rules(path.name, path.read_bytes()), args.result)
        elif args.cmd == "schema":
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            for name, model in (("analysis_request", AnalysisRequest), ("analysis_result", AnalysisResult)):
                (out / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {out}/analysis_request.schema.json and analysis_result.schema.json")
        elif args.cmd == "validate":
            AnalysisResult.model_validate_json(Path(args.result).read_text(encoding="utf-8"))
            print("valid")
    except api.EngineInputError as exc:
        _emit({"error": "invalid_input", "errors": exc.errors}, None)
        return 2
    except json.JSONDecodeError as exc:
        _emit({"error": "invalid_input", "errors": [{"loc": [], "msg": f"Not valid JSON: {exc}"}]}, None)
        return 2
    except Exception as exc:  # noqa: BLE001 — the CLI reports, the library raises
        if args.cmd == "validate":
            print(f"invalid: {exc}")
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
