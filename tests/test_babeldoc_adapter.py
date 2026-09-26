from pathlib import Path
import importlib.util
import tempfile
import unittest

import fitz


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


if __name__ == "__main__":
    unittest.main()