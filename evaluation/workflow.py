import csv
import hashlib
import json
from pathlib import Path

from .contracts import COORDINATES, read_json, require, validate_document
from .evaluate import evaluate_collection


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def init_reference(pdf_path, output):
    import pymupdf
    from extraction.pipeline import validate_pdf
    pdf_path, output = Path(pdf_path), Path(output)
    require(not output.exists(), "Reference already exists; edit it instead of overwriting annotations.")
    validate_pdf(pdf_path)
    reference = {"reference_version": "1.0", "source_name": pdf_path.name,
                 "source_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                 "coordinate_system": COORDINATES, "reviewed": False, "reviewed_by": "", "pages": []}
    with pymupdf.open(pdf_path) as pdf:
        for page in pdf:
            reference["pages"].append({"number": page.number + 1, "width": page.rect.width, "height": page.rect.height,
                "rotation": page.rotation, "tasks": ["text", "tables", "figures"], "text": "", "tables": [], "figures": []})
    save_json(output, reference)
    return reference


def import_prediction(normalized_path, pdf_path, tool, tool_version, settings_path, output):
    """Accept normalized pages from ANY tool; no extraction or cleanup here."""
    import pymupdf
    from extraction.pipeline import validate_pdf
    pdf_path, output = Path(pdf_path), Path(output)
    require(not output.exists(), "Prediction file already exists. Choose a new experiment output.")
    validate_pdf(pdf_path)
    normalized = read_json(normalized_path)
    require(normalized.get("coordinate_system") == COORDINATES, "Normalized input must declare displayed-page coordinates. This importer does not convert coordinates.")
    settings = read_json(settings_path)
    result = {"schema_version": "0.1.0", "source_name": pdf_path.name,
              "source_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
              "coordinate_system": COORDINATES, "backend": tool, "backend_version": tool_version,
              "settings": settings, "pages": normalized["pages"]}
    validate_document(result)
    with pymupdf.open(pdf_path) as pdf:
        for page in result["pages"]:
            require(page["number"] <= len(pdf), "Prediction has more pages than the source PDF.")
            source = pdf[page["number"] - 1]
            require(abs(page["width"] - source.rect.width) <= .01 and abs(page["height"] - source.rect.height) <= .01,
                    "Prediction page dimensions do not match the source PDF.")
            require(page.get("rotation", 0) == source.rotation, "Prediction rotation differs from the source PDF.")
    save_json(output, result)
    return result


def load_documents(path, reference=False):
    path = Path(path)
    require(path.exists(), f"Input path does not exist: {path}")
    if path.is_file():
        return [read_json(path)]
    documents = []
    for file in sorted(path.rglob("*.json")):
        if reference:
            # A malformed reference must fail validation, never disappear from
            # the denominator because its schema marker was accidentally edited.
            documents.append(read_json(file))
            continue
        if file.name in {"manifest.json", "reviews.json", "native-docling.json", "evaluation.json"}:
            continue
        value = read_json(file)
        if file.name == "document.json" or isinstance(value, dict) and "schema_version" in value and "source_sha256" in value:
            documents.append(value)
    require(not reference or bool(documents), "No reference JSON documents found.")
    return documents


def report_row(report):
    metrics = report["metrics"]
    return {"experiment": report["experiment"], "documents": report["documents_expected"], "missing_documents": report["documents_missing"],
            "missing_pages": report["counts"].get("missing_pages", 0), "CER_lower_better": metrics["text"]["cer"],
            "WER_lower_better": metrics["text"]["wer"], "character_errors": metrics["text"]["character_errors"],
            "table_detection_F1": metrics["tables"]["f1"], "table_cell_exact_F1": metrics["table_cells"]["f1"],
            "table_dimensions_recall": metrics["table_dimensions_recall"], "figure_detection_F1": metrics["figures"]["f1"],
            "figure_matched_mean_IoU": metrics["figures"]["matched_mean_iou"], "comparison_key": report["comparison_key"]}


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(report, output):
    output = Path(output)
    require(not output.exists() and not output.with_suffix(".csv").exists(), "Report already exists. Choose a new output filename to preserve experiment history.")
    save_json(output, report)
    write_csv(output.with_suffix(".csv"), [report_row(report)])


def compare_reports(paths, output):
    reports = [read_json(p) for p in paths]
    require(len(reports) >= 2, "Choose at least two reports to compare.")
    require(all(r.get("report_version") == "1.0" for r in reports), "Only evaluation report JSON files can be compared.")
    require(len({r["comparison_key"] for r in reports}) == 1, "Reports use different references, scoring rules, or evaluator code. Re-evaluate against the same shared benchmark.")
    require(not Path(output).exists(), "Comparison file already exists. Choose a new output filename.")
    write_csv(output, [report_row(r) for r in reports])


def evaluate_paths(reference_path, prediction_path, protocol, experiment, output):
    report = evaluate_collection(load_documents(reference_path, True), load_documents(prediction_path), protocol, experiment)
    write_report(report, output)
    return report
