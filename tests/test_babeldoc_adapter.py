from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

import fitz

from evaluation.contracts import validate_document


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "babeldoc_adapter.py"

spec = importlib.util.spec_from_file_location(
    "babeldoc_adapter",
    ADAPTER_PATH,
)
babeldoc_adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(babeldoc_adapter)


def make_page(media, crop=None):
    return {
        "mediabox": {"box": media},
        "cropbox": {"box": crop if crop is not None else dict(media)},
        "page_number": 0,
    }


class BabelDocGeometryTests(unittest.TestCase):
    def test_zero_origin_uncropped_page_is_supported(self):
        page = make_page(
            {"x": 0.0, "y": 0.0, "x2": 612.0, "y2": 792.0}
        )

        babeldoc_adapter.validate_page_geometry(page)

    def test_nonzero_mediabox_origin_is_rejected(self):
        page = make_page(
            {"x": 10.0, "y": 0.0, "x2": 622.0, "y2": 792.0}
        )

        with self.assertRaisesRegex(ValueError, "origin"):
            babeldoc_adapter.validate_page_geometry(page)

    def test_cropbox_difference_is_rejected(self):
        media = {
            "x": 0.0,
            "y": 0.0,
            "x2": 612.0,
            "y2": 792.0,
        }

        crop = {
            "x": 10.0,
            "y": 10.0,
            "x2": 602.0,
            "y2": 782.0,
        }

        with self.assertRaisesRegex(ValueError, "CropBox"):
            babeldoc_adapter.validate_page_geometry(
                make_page(media, crop)
            )

    def test_rotated_source_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "rotated.pdf"

            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            page.set_rotation(90)
            doc.save(pdf_path)
            doc.close()

            with self.assertRaisesRegex(ValueError, "rotation 90"):
                babeldoc_adapter.validate_source_pdf_rotation(
                    pdf_path,
                    1,
                )


class SavedBabelDocPredictionTests(unittest.TestCase):
    def test_saved_predictions_have_shared_metadata(self):
        prediction_dir = ROOT / "experiments" / "babeldoc"

        names = [
            "Sample02",
            "Sample03",
            "Sample04",
            "YOLOv3",
            "TrafficSF",
        ]

        required_keys = {
            "schema_version",
            "source_name",
            "source_sha256",
            "backend",
            "backend_version",
            "settings",
            "coordinate_system",
            "pages",
        }

        for name in names:
            path = prediction_dir / f"{name}.json"
            self.assertTrue(path.exists(), f"Missing prediction: {path}")

            data = json.loads(path.read_text(encoding="utf-8"))

            validate_document(data, reference=False)

            self.assertTrue(
                required_keys.issubset(data),
                f"{name} missing shared prediction metadata: "
                f"{sorted(required_keys - set(data))}",
            )

            sha256 = data["source_sha256"]
            self.assertEqual(len(sha256), 64, f"{name} has invalid SHA-256 length")
            self.assertEqual(
                sha256,
                sha256.lower(),
                f"{name} source SHA-256 must be lowercase",
            )
            self.assertTrue(
                all(ch in "0123456789abcdef" for ch in sha256),
                f"{name} source SHA-256 is not hexadecimal",
            )

            self.assertEqual(data["backend"], "babeldoc")
            self.assertEqual(data["backend_version"], "0.6.4")
            self.assertIsInstance(data["settings"], dict)
            self.assertTrue(data["pages"], f"{name} has no pages")

if __name__ == "__main__":
    unittest.main()
