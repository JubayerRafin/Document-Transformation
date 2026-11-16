"""Pre-loaded job processes: which commands qualify, and the plain-process fallback."""
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from transformation import preload


class PreloadTests(unittest.TestCase):
    def test_only_known_python_module_commands_qualify(self):
        self.assertEqual(preload._parse(['py', '-u', '-m', 'backend.engine_job', 'r.json']), ('backend.engine_job', ['r.json'], True))
        self.assertEqual(preload._parse(['py', '-m', 'transformation.pdf.babeldoc_worker', '--files', 'a']),
                         ('transformation.pdf.babeldoc_worker', ['--files', 'a'], False))
        for command in (['py', '-m', 'other.module'], ['py', 'script.py'], ['py', '-X', 'utf8', '-m', 'backend.engine_job']):
            self.assertIsNone(preload._parse(command))

    def test_disabled_or_unsupported_runs_a_normal_process(self):
        with tempfile.TemporaryFile() as log, patch.object(preload.subprocess, 'Popen') as popen:
            with patch.dict(os.environ, {preload.ENABLE_ENV: '0'}):
                preload.popen(['py', '-m', 'backend.engine_job'], '.', log, {'A': '1'})
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs['stderr'], subprocess.STDOUT)
            self.assertEqual(popen.call_args.kwargs['env'], {'A': '1'})

    def test_a_failing_preload_server_never_blocks_the_job(self):
        with tempfile.NamedTemporaryFile() as log, patch.object(preload, 'enabled', return_value=True), \
                patch.object(preload, '_ensure_server', side_effect=RuntimeError('no server')), \
                patch.object(preload.subprocess, 'Popen') as popen:
            preload.popen(['py', '-u', '-m', 'backend.engine_job', 'x'], '.', log, {})
        popen.assert_called_once()


if __name__ == '__main__':
    unittest.main()
