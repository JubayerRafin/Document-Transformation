"""The OCR cache key changes with any OCR source file (2026-11-23: a Tesseract change reused old readings)."""
import pathlib
import unittest
from unittest.mock import patch

import pymupdf

from transformation.ocr.cache import location


class OcrCacheKeyTests(unittest.TestCase):
    def test_a_change_to_any_ocr_source_file_changes_the_cache_location(self):
        with pymupdf.open() as doc:
            doc.new_page(width=200, height=100).insert_text((20, 50), 'Invoice 42')
            data = doc.tobytes()
        before = location(data, 'bn', 'gpu', '/tmp/cache')
        original = pathlib.Path.read_bytes

        def changed(self):
            content = original(self)
            return content + b'# changed' if self.name == 'tesseract_line.py' else content
        with patch.object(pathlib.Path, 'read_bytes', changed):
            after = location(data, 'bn', 'gpu', '/tmp/cache')
        self.assertNotEqual(before, after)
        self.assertEqual(before, location(data, 'bn', 'gpu', '/tmp/cache'))


if __name__ == '__main__':
    unittest.main()
