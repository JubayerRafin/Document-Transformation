"""Command line: extract one PDF or benchmark a folder with shared backends."""
import argparse
import csv
import json
from pathlib import Path
import sys

from extraction.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="Document Extraction Lab")
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("extract", help="Extract one PDF")
    single.add_argument("input", type=Path)
    single.add_argument("--backend", choices=["native", "docling"], default="native")
    batch = sub.add_parser("benchmark", help="Run PDFs through chosen backends; counts are not accuracy scores")
    batch.add_argument("input", type=Path)
    batch.add_argument("--backends", nargs="+", choices=["native", "docling"], default=["native", "docling"])
    reference = sub.add_parser("reference-init", help="Create an empty draft for human annotation; never model-generated ground truth")
    reference.add_argument("input", type=Path)
    reference.add_argument("--output", type=Path, required=True)
    imported = sub.add_parser("import-prediction", help="Wrap normalized pages from another tool in the shared IR")
    imported.add_argument("input", type=Path)
    imported.add_argument("--source", type=Path, required=True)
    imported.add_argument("--tool", required=True)
    imported.add_argument("--tool-version", required=True)
    imported.add_argument("--settings", type=Path, required=True)
    imported.add_argument("--output", type=Path, required=True)
    evaluate = sub.add_parser("evaluate", help="Score an experiment against reviewed references")
    evaluate.add_argument("--references", type=Path, required=True)
    evaluate.add_argument("--predictions", type=Path, required=True)
    evaluate.add_argument("--name", required=True)
    evaluate.add_argument("--protocol", type=Path, default=Path(__file__).resolve().parent / "config/evaluation.json")
    evaluate.add_argument("--output", type=Path, required=True)
    compare = sub.add_parser("compare", help="Compare reports only when reference and evaluator fingerprints match")
    compare.add_argument("reports", nargs="+", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    for command in (single, batch):
        command.add_argument("--output", type=Path, default=Path("runs"))
        command.add_argument("--no-ocr", action="store_true")
        command.add_argument("--artifacts-path")
        command.add_argument("--ocr-model-dir")
    args = parser.parse_args()
    if args.command in {"reference-init", "import-prediction", "evaluate", "compare"}:
        from evaluation.contracts import load_protocol
        from evaluation.workflow import init_reference, import_prediction, evaluate_paths, compare_reports, report_row
        try:
            if args.command == "reference-init":
                init_reference(args.input, args.output)
                print("Draft created. Human annotation and review are required before scoring.")
            elif args.command == "import-prediction":
                import_prediction(args.input, args.source, args.tool, args.tool_version, args.settings, args.output)
            elif args.command == "evaluate":
                report = evaluate_paths(args.references, args.predictions, load_protocol(args.protocol), args.name, args.output)
                print(json.dumps(report_row(report), indent=2))
            else:
                compare_reports(args.reports, args.output)
            print(f"Saved: {args.output.resolve()}")
            return 0
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print(f"Evaluation error: {exc}", file=sys.stderr)
            return 1
    options = {"ocr": not args.no_ocr, "artifacts_path": args.artifacts_path, "ocr_model_dir": args.ocr_model_dir}
    if args.command == "extract":
        run_dir, _, manifest = run_pipeline(args.input, args.output, args.backend, options, print)
        print(json.dumps(manifest, indent=2))
        print(f"Saved: {run_dir.resolve()}")
        return 0
    files = sorted(p for p in args.input.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not files:
        parser.error("No PDF files found in the input directory.")
    rows, failed = [], False
    for path in files:
        for backend in args.backends:
            row = {"file": str(path), "backend": backend}
            try:
                run_dir, _, manifest = run_pipeline(path, args.output, backend, options, print)
                row.update(status="completed", seconds=manifest["seconds"], pages=manifest["pages"],
                           text=manifest["regions"].get("text", 0), tables=manifest["regions"].get("table", 0),
                           figures=manifest["regions"].get("figure", 0), raster_pages=manifest["regions"].get("raster_page", 0),
                           warnings=manifest["warnings"], run_dir=str(run_dir), error="")
            except Exception as exc:
                failed = True
                row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                print(row["error"], file=sys.stderr)
            rows.append(row)
    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "benchmark.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "backend", "status", "seconds", "pages", "text", "tables", "figures", "raster_pages", "warnings", "run_dir", "error"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved benchmark diagnostics: {report.resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
