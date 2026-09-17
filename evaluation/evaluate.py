from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .contracts import fingerprint, require, validate_document
from .metrics import match_regions, prf, table_slots, text_counts

VERSION = "1.0.1"


def score_document(reference, prediction, protocol):
    validate_document(reference, reference=True)
    if prediction is not None:
        validate_document(prediction)
        require(reference["source_sha256"] == prediction["source_sha256"], "Prediction and reference belong to different source PDFs.")
    pages = {p["number"]: p for p in (prediction or {}).get("pages", [])}
    ref_pages = {p["number"]: p for p in reference["pages"]}
    require(set(pages) <= set(ref_pages), "Prediction has pages absent from the reference page inventory.")
    counts = Counter()
    details = []
    for ref in reference["pages"]:
        pred = pages.get(ref["number"])
        if pred:
            require(abs(ref["width"] - pred["width"]) <= .01 and abs(ref["height"] - pred["height"]) <= .01,
                    f"Coordinate dimensions differ on page {ref['number']}; normalize the adapter output first.")
            require(ref.get("rotation", 0) == pred.get("rotation", 0), "Reference and prediction page rotations differ.")
        if not ref["tasks"]:
            continue
        counts["evaluated_pages"] += 1
        counts["missing_pages"] += int(pred is None)
        regions = pred["regions"] if pred else []
        page_result = {"page": ref["number"], "missing": pred is None, "tasks": ref["tasks"]}
        if "text" in ref["tasks"]:
            # Join by declared prediction order, not reference geometry. This is
            # independent of line/paragraph segmentation after whitespace collapse.
            extracted = " ".join(r["text"] for r in regions if r["kind"] == "text")
            text = text_counts(ref["text"], extracted)
            counts.update(text)
            counts["text_pages"] += 1
            page_result["text"] = {**text, "reference": ref["text"], "prediction": extracted}
        for task, kind in (("tables", "table"), ("figures", "figure")):
            if task not in ref["tasks"]:
                continue
            expected = ref[task]
            actual = [r for r in regions if r["kind"] == kind]
            matches = match_regions(expected, actual, protocol["iou_threshold"])
            matched_r, matched_p = {r for r, _, _ in matches}, {p for _, p, _ in matches}
            counts[f"{task}_pages"] += 1
            counts[f"{task}_tp"] += len(matches)
            counts[f"{task}_fn"] += len(expected) - len(matches)
            counts[f"{task}_fp"] += len(actual) - len(matches)
            counts[f"{task}_iou_sum"] += sum(v for _, _, v in matches)
            page_result[task] = {
                "matches": [{"reference_id": expected[r]["id"], "prediction_id": actual[p]["id"], "iou": v} for r, p, v in matches],
                "missed": [r for i, r in enumerate(expected) if i not in matched_r],
                "extra": [p for i, p in enumerate(actual) if i not in matched_p],
            }
            if task == "tables":
                ref_slots = sum(len(table_slots(r["cells"])) for r in expected)
                pred_slots = sum(len(table_slots(p["cells"])) for p in actual)
                correct = 0
                for r, p, _ in matches:
                    a, b = table_slots(expected[r]["cells"]), table_slots(actual[p]["cells"])
                    correct += sum(position in b and value == b[position] for position, value in a.items())
                    ad = (len(expected[r]["cells"]), len(expected[r]["cells"][0]) if expected[r]["cells"] else 0)
                    bd = (len(actual[p]["cells"]), len(actual[p]["cells"][0]) if actual[p]["cells"] else 0)
                    counts["table_dimensions_correct"] += int(ad == bd)
                counts["cell_tp"] += correct
                counts["cell_fn"] += ref_slots - correct
                counts["cell_fp"] += pred_slots - correct
        details.append(page_result)
    return {"source_sha256": reference["source_sha256"], "source_name": reference.get("source_name", ""),
            "missing_prediction": prediction is None, "counts": dict(counts), "pages": details}


def summarize(counts):
    c = Counter(counts)
    metrics = {
        "text": {"pages": c["text_pages"], "cer": c["character_errors"] / c["reference_characters"] if c["reference_characters"] else (0. if c["text_pages"] and not c["character_errors"] else None),
                 "wer": c["word_errors"] / c["reference_words"] if c["reference_words"] else (0. if c["text_pages"] and not c["word_errors"] else None),
                 **{k: c[k] for k in ("character_errors", "reference_characters", "word_errors", "reference_words")}},
        "table_cells": prf(c["cell_tp"], c["cell_fp"], c["cell_fn"]),
        "table_dimensions_recall": c["table_dimensions_correct"] / (c["tables_tp"] + c["tables_fn"]) if c["tables_tp"] + c["tables_fn"] else None,
    }
    for task in ("tables", "figures"):
        metrics[task] = {**prf(c[f"{task}_tp"], c[f"{task}_fp"], c[f"{task}_fn"]), "pages": c[f"{task}_pages"],
                         "matched_mean_iou": c[f"{task}_iou_sum"] / c[f"{task}_tp"] if c[f"{task}_tp"] else None}
    return metrics


def implementation_hash():
    # Universal newline decoding keeps Git LF/CRLF checkouts comparable.
    return fingerprint({p.name: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))})


def evaluate_collection(references, predictions, protocol, experiment):
    require(bool(references), "No references found.")
    require(bool(experiment.strip()), "An experiment name is required.")
    by_hash = {}
    configurations = set()
    for pred in predictions:
        validate_document(pred)
        digest = pred["source_sha256"]
        require(digest not in by_hash, "Multiple predictions for the same PDF. Use a folder containing one run per document for this experiment.")
        by_hash[digest] = pred
        configurations.add(fingerprint({k: pred[k] for k in ("backend", "backend_version", "settings")}))
    require(len(configurations) <= 1, "Mixed tools, versions or settings in one experiment. Evaluate each configuration separately.")
    ref_ids = [r["source_sha256"] for r in references]
    require(len(set(ref_ids)) == len(ref_ids), "Duplicate reference source PDFs.")
    require(set(by_hash) <= set(ref_ids), "Predictions include PDFs outside this reference set. Use the correct experiment folder.")
    results = [score_document(r, by_hash.get(r["source_sha256"]), protocol) for r in sorted(references, key=lambda r: r["source_sha256"])]
    counts = Counter()
    for result in results:
        counts.update(result["counts"])
    dataset_hash = fingerprint(sorted((r["source_sha256"], fingerprint(r)) for r in references))
    evaluator_hash = implementation_hash()
    return {"report_version": "1.0", "evaluator_version": VERSION, "experiment": experiment,
            "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": dataset_hash,
            "protocol": protocol, "evaluator_sha256": evaluator_hash,
            "comparison_key": fingerprint([dataset_hash, protocol, evaluator_hash]),
            "configuration": {k: predictions[0][k] for k in ("backend", "backend_version", "settings")} if predictions else None,
            "documents_expected": len(references), "documents_missing": sum(r["missing_prediction"] for r in results),
            "counts": dict(counts), "metrics": summarize(counts), "documents": results,
            "notes": ["CER/WER include missing text and are order-sensitive; whitespace and NFKC normalized, case preserved.",
                      "Missing documents/pages count as empty predictions. No overall winner score is calculated.",
                      "Null means undefined or unannotated. Empty-reference text insertions remain in error counts; CER/WER can exceed 1.",
                      "Table scoring checks grid slots and dimensions, not full merged-cell topology/TEDS.",
                      "Figure detection evaluates region boxes, not image fidelity or caption association."]}
