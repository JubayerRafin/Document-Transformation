import copy
from pathlib import Path
import unittest

from evaluation.contracts import COORDINATES, load_protocol
from evaluation.evaluate import evaluate_collection
from evaluation.metrics import edit_distance, match_regions
from evaluation.workflow import compare_reports, save_json

PROTOCOL = load_protocol(Path(__file__).resolve().parents[1] / "config/evaluation.json")


def fixtures():
    reference = {"reference_version": "1.0", "source_name": "test", "source_sha256": "a" * 64,
                 "coordinate_system": COORDINATES, "reviewed": True, "reviewed_by": "test fixture", "pages": [
                     {"number": 1, "width": 100, "height": 100, "rotation": 0, "tasks": ["text", "tables", "figures"],
                      "text": "Hello world", "tables": [{"id": "rt", "bbox": [10, 40, 80, 70], "cells": [["Name", "Value"], ["A", "10"]]}],
                      "figures": [{"id": "rf", "bbox": [10, 75, 50, 95]}]}]}
    prediction = {"schema_version": "0.1.0", "source_name": "test", "source_sha256": "a" * 64,
                  "coordinate_system": COORDINATES, "backend": "fixture", "backend_version": "1", "settings": {}, "pages": [
                      {"number": 1, "width": 100, "height": 100, "rotation": 0, "regions": [
                          {"id": "ptext", "kind": "text", "bbox": [5, 5, 90, 20], "text": "Hello world"},
                          {"id": "pt", "kind": "table", "bbox": [10, 40, 80, 70], "cells": [["Name", "Value"], ["A", "10"]]},
                          {"id": "pf", "kind": "figure", "bbox": [10, 75, 50, 95]}]}]}
    return reference, prediction


class EvaluationTests(unittest.TestCase):
    def score(self, r, p):
        return evaluate_collection([r], [p] if p is not None else [], PROTOCOL, "test")

    def test_fingerprint_ignores_checkout_newlines_but_detects_code_changes(self):
        from temp_workspace import temporary_workspace
        from unittest.mock import patch
        from evaluation.evaluate import implementation_hash
        with temporary_workspace() as directory:
            module = Path(directory) / "evaluate.py"
            with patch("evaluation.evaluate.__file__", str(module)):
                module.write_bytes(b"VERSION = 1\n# scoring\n")
                lf = implementation_hash()
                module.write_bytes(b"VERSION = 1\r\n# scoring\r\n")
                self.assertEqual(lf, implementation_hash())
                module.write_bytes(b"VERSION = 2\n# scoring\n")
                self.assertNotEqual(lf, implementation_hash())

    def test_perfect_prediction(self):
        result = self.score(*fixtures())["metrics"]
        self.assertEqual(result["text"]["cer"], 0)
        self.assertEqual(result["table_cells"]["f1"], 1)
        self.assertEqual(result["figures"]["f1"], 1)

    def test_missing_document_counts_as_errors(self):
        r, _ = fixtures()
        result = self.score(r, None)
        self.assertEqual(result["documents_missing"], 1)
        self.assertEqual(result["metrics"]["text"]["cer"], 1)
        self.assertEqual(result["metrics"]["table_cells"]["f1"], 0)
        self.assertEqual(result["metrics"]["figures"]["fn"], 1)

    def test_missing_page_is_not_skipped(self):
        r, p = fixtures()
        p["pages"] = []
        result = self.score(r, p)
        self.assertEqual(result["counts"]["missing_pages"], 1)
        self.assertEqual(result["metrics"]["text"]["wer"], 1)

    def test_draft_reference_rejected(self):
        r, p = fixtures()
        r["reviewed"] = False
        with self.assertRaisesRegex(ValueError, "draft"):
            self.score(r, p)

    def test_wrong_source_rejected(self):
        r, p = fixtures()
        p["source_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            self.score(r, p)

    def test_duplicate_predictions_rejected(self):
        r, p = fixtures()
        with self.assertRaisesRegex(ValueError, "Multiple predictions"):
            evaluate_collection([r], [p, p], PROTOCOL, "test")

    def test_segmentation_and_whitespace_do_not_change_text_score(self):
        r, p = fixtures()
        p["pages"][0]["regions"][0]["text"] = "Hello\n"
        p["pages"][0]["regions"].insert(1, {"id": "ptext2", "kind": "text", "bbox": [5, 20, 90, 30], "text": " world"})
        self.assertEqual(self.score(r, p)["metrics"]["text"]["cer"], 0)

    def test_duplicate_figure_is_false_positive(self):
        r, p = fixtures()
        extra = copy.deepcopy(p["pages"][0]["regions"][-1])
        extra["id"] = "duplicate"
        p["pages"][0]["regions"].append(extra)
        self.assertEqual(self.score(r, p)["metrics"]["figures"]["fp"], 1)

    def test_cell_mismatch_penalizes_precision_and_recall(self):
        r, p = fixtures()
        p["pages"][0]["regions"][1]["cells"][1][1] = "20"
        self.assertEqual(self.score(r, p)["metrics"]["table_cells"]["f1"], .75)

    def test_unannotated_task_is_not_scored(self):
        r, p = fixtures()
        r["pages"][0]["tasks"] = ["text"]
        self.assertIsNone(self.score(r, p)["metrics"]["figures"]["f1"])

    def test_empty_reference_hallucinations_not_reported_as_zero_error(self):
        r, p = fixtures()
        r["pages"][0]["text"] = ""
        result = self.score(r, p)["metrics"]["text"]
        self.assertIsNone(result["cer"])
        self.assertGreater(result["character_errors"], 0)

    def test_maximum_cardinality_matching_avoids_greedy_failure(self):
        reference = [{"id": "a", "bbox": [0, 0, 10, 10]}, {"id": "b", "bbox": [3, 0, 13, 10]}]
        prediction = [{"id": "a", "bbox": [1, 0, 11, 10]}, {"id": "b", "bbox": [0, 0, 6, 10]}]
        self.assertEqual(len(match_regions(reference, prediction, .5)), 2)

    def test_edit_distance_known_case(self):
        self.assertEqual(edit_distance("kitten", "sitting"), 3)

    def test_changed_reference_changes_comparison_key(self):
        r, p = fixtures()
        first = self.score(r, p)
        r["pages"][0]["text"] += "!"
        self.assertNotEqual(first["comparison_key"], self.score(r, p)["comparison_key"])

    def test_coordinate_mismatch_rejected(self):
        r, p = fixtures()
        p["pages"][0]["width"] = 101
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.score(r, p)

    def test_comparison_rejects_changed_protocol(self):
        from unittest.mock import patch
        r, p = fixtures()
        first = self.score(r, p)
        protocol = dict(PROTOCOL, iou_threshold=.75)
        second = evaluate_collection([r], [p], protocol, "second")
        with patch("evaluation.workflow.read_json", side_effect=[first, second]):
            with self.assertRaisesRegex(ValueError, "different references"):
                compare_reports(["first", "second"], "unused.csv")

    def test_mixed_configurations_rejected(self):
        r, p = fixtures()
        r2, p2 = copy.deepcopy(r), copy.deepcopy(p)
        r2["source_sha256"] = p2["source_sha256"] = "b" * 64
        p2["settings"] = {"ocr": "different"}
        with self.assertRaisesRegex(ValueError, "Mixed tools"):
            evaluate_collection([r, r2], [p, p2], PROTOCOL, "mixed")

    def test_extra_table_slots_penalized(self):
        r, p = fixtures()
        p["pages"][0]["regions"][1]["cells"].append(["Extra", "20"])
        result = self.score(r, p)["metrics"]
        self.assertEqual(result["table_cells"]["fp"], 2)
        self.assertEqual(result["table_dimensions_recall"], 0)


if __name__ == "__main__":
    unittest.main()
