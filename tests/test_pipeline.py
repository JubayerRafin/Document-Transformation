import json
import os
from pathlib import Path
import tempfile
import shutil
import uuid
import unittest
from unittest.mock import patch

import pymupdf

from extraction.models import Document, Page, Region
from extraction.pipeline import export_zip, run_pipeline, validate_pdf
from extraction.reconcile import reconcile
from extraction.ocr_config import resolve_ocr_models


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.environ.get("LAB_TEST_TMP", tempfile.gettempdir())).resolve()
        self.root = self.base / f"extraction-test-{uuid.uuid4().hex}"
        # Ordinary inherited directory permissions also work in Windows sandboxes.
        self.root.mkdir(mode=0o755)

    def tearDown(self):
        if self.root.resolve().parent != self.base or not self.root.name.startswith("extraction-test-"):
            raise RuntimeError("Refusing cleanup outside the test directory.")
        shutil.rmtree(self.root)

    def make_pdf(self, rotation=0, scanned=False, table=False):
        path = self.root / "fixture.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=500)
            if scanned:
                from PIL import Image, ImageDraw
                import io
                image = Image.new("RGB", (400, 500), "white")
                ImageDraw.Draw(image).text((30, 30), "Scanned content", fill="black")
                data = io.BytesIO()
                image.save(data, format="PNG")
                page.insert_image(page.rect, stream=data.getvalue())
            else:
                page.insert_text((35, 45), "Outside table remains visible.")
                if table:
                    for x in (30, 180, 330):
                        page.draw_line((x, 90), (x, 180))
                    for y in (90, 120, 150, 180):
                        page.draw_line((30, y), (330, y))
                    for x, y, text in [(40, 110, "Name"), (190, 110, "Value"), (40, 140, "Alpha"), (190, 140, "10"), (40, 170, "Beta"), (190, 170, "20")]:
                        page.insert_text((x, y), text)
            page.set_rotation(rotation)
            doc.save(path)
        return path

    def make_ocr_fixture(self):
        import hashlib
        config = {"profile": "test", "engine": "rapidocr", "runtime": "onnxruntime", "models": {}}
        for key in ("det_model_path", "rec_model_path", "cls_model_path"):
            name = f"{key}.onnx"
            payload = key.encode()
            (self.root / name).write_bytes(payload)
            config["models"][key] = {"filename": name, "sha256": hashlib.sha256(payload).hexdigest()}
        lock = self.root / "ocr.json"
        lock.write_text(json.dumps(config))
        return lock

    def test_ocr_uses_exact_weights_despite_extra_files(self):
        lock = self.make_ocr_fixture()
        (self.root / "zzz_rec_new.onnx").write_bytes(b"different model")
        paths, record = resolve_ocr_models(self.root, lock)
        self.assertEqual(Path(paths["rec_model_path"]).name, "rec_model_path.onnx")
        self.assertEqual(len(record["models"]), 3)
        self.assertEqual(len(record["config_sha256"]), 64)

    def test_ocr_rejects_modified_weights(self):
        lock = self.make_ocr_fixture()
        (self.root / "rec_model_path.onnx").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            resolve_ocr_models(self.root, lock)

    def test_ocr_rejects_missing_weights(self):
        lock = self.make_ocr_fixture()
        (self.root / "rec_model_path.onnx").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "Pinned OCR model missing"):
            resolve_ocr_models(self.root, lock)

    def test_table_content_not_duplicated_in_body(self):
        run, data, meta = run_pipeline(self.make_pdf(table=True), self.root / "runs")
        regions = data["pages"][0]["regions"]
        tables = [r for r in regions if r["kind"] == "table"]
        text = " ".join(r["text"] for r in regions if r["kind"] == "text")
        self.assertEqual(len(tables), 1)
        self.assertIn("Alpha", str(tables[0]["cells"]))
        self.assertNotIn("Alpha", text)
        self.assertIn("Outside table remains visible.", text)
        self.assertTrue((run / tables[0]["asset"]).exists())
        self.assertEqual(meta["status"], "completed")

    def test_rotated_geometry_matches_display(self):
        _, data, _ = run_pipeline(self.make_pdf(rotation=90), self.root / "runs")
        page = data["pages"][0]
        self.assertEqual((page["width"], page["height"]), (500, 400))
        text = next(r for r in page["regions"] if r["kind"] == "text")
        self.assertGreater(text["bbox"][0], 400)
        self.assertGreater(text["bbox"][1], 20)

    def test_scanned_page_is_not_reported_as_extracted_text(self):
        _, data, meta = run_pipeline(self.make_pdf(scanned=True), self.root / "runs")
        self.assertEqual(meta["regions"].get("text", 0), 0)
        self.assertEqual(meta["regions"].get("raster_page"), 1)
        self.assertTrue(any("OCR is required" in w for w in data["pages"][0]["warnings"]))

    def test_invalid_pdf_rejected(self):
        path = self.root / "not.pdf"
        path.write_text("not a document")
        with self.assertRaisesRegex(ValueError, "not a PDF"):
            validate_pdf(path)

    def test_failed_backend_never_exports_completed_result(self):
        source = self.make_pdf()
        with patch("extraction.backends.native.extract", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                run_pipeline(source, self.root / "runs")
        manifest_path = next((self.root / "runs").glob("*/manifest.json"))
        self.assertEqual(json.loads(manifest_path.read_text())["status"], "failed")
        self.assertFalse((manifest_path.parent / "document.json").exists())

    def test_ids_stable_and_archives_include_assets(self):
        import io
        import zipfile
        source = self.make_pdf(table=True)
        first, data, _ = run_pipeline(source, self.root / "runs")
        second, other, _ = run_pipeline(source, self.root / "runs")
        self.assertNotEqual(first, second)
        self.assertEqual(data["pages"], other["pages"])
        with zipfile.ZipFile(io.BytesIO(export_zip(first))) as archive:
            self.assertIn("document.json", archive.namelist())
            self.assertIn("assets/page-1.png", archive.namelist())

    def test_reconciliation_preserves_uncertain_text(self):
        doc = Document("x", "hash", "test", "1", [Page(1, 100, 100, 0, regions=[
            Region("table", "table", [10, 10, 90, 90]),
            Region("text", "text", [20, 20, 70, 30], "Keep me")])])
        reconcile(doc)
        self.assertEqual(len(doc.pages[0].regions), 2)
        self.assertEqual(doc.pages[0].regions[1].metadata["overlaps_tables"], ["table"])

    def test_invalid_ir_geometry_rejected(self):
        doc = Document("x", "h", "t", "1", [Page(1, 100, 100, 0, regions=[Region("r", "text", [-1, 0, 50, 20])])])
        with self.assertRaises(ValueError):
            doc.validate()


if __name__ == "__main__":
    unittest.main()
