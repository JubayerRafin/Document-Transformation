import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

from backend.engine_job import input_problem


class EngineInputTests(unittest.TestCase):
    def test_unusable_files_are_rejected_with_their_own_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            good, locked, damaged, other = (Path(folder, n) for n in ('good.pdf', 'locked.pdf', 'damaged.pdf', 'note.txt'))
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((72, 72), 'Reference SEC-2026-00417')
                data = pdf.tobytes()
                pdf.save(good)
                pdf.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='owner')
            damaged.write_bytes(data[: len(data) // 3])
            other.write_text('not a pdf')
            self.assertIsNone(input_problem(good))
            for path in (locked, damaged, other):
                self.assertEqual(input_problem(path), 'INPUT_REJECTED', path.name)


if __name__ == '__main__':
    unittest.main()
