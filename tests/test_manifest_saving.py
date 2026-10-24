import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transformation.pdf.runner import write_manifest


class ManifestSavingTests(unittest.TestCase):
    def test_transient_lock_retries_and_saves(self):
        original = Path.replace
        attempts = []
        def replace(path, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError('sharing lock')
            return original(path, target)
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with patch.object(Path, 'replace', replace), patch('transformation.pdf.runner.time.sleep'):
                self.assertTrue(write_manifest(run, {'status': 'processing'}))
            self.assertEqual(len(attempts), 3)
            self.assertEqual(json.loads((run / 'manifest.json').read_text())['status'], 'processing')

    def test_persistent_lock_retains_old_manifest_and_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            write_manifest(run, {'status': 'processing'})
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')), patch('transformation.pdf.runner.time.sleep'):
                self.assertFalse(write_manifest(run, {'status': 'generated'}))
            self.assertEqual(json.loads((run / 'manifest.json').read_text())['status'], 'processing')
            self.assertEqual(json.loads((run / 'manifest.tmp').read_text())['status'], 'generated')
            self.assertTrue(write_manifest(run, {'status': 'generated'}))

    def test_other_io_errors_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    write_manifest(Path(folder), {'status': 'processing'})
