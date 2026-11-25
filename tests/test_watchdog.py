"""tools/gpu/watchdog.sh against stand-in servers: what it restarts, and when it leaves things alone.

The start scripts are replaced by stubs that only record how they were called; the website and the
model servers are small HTTP servers. Linux only (bash, flock, pgrep, curl), as on the GPU server.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('bash', 'flock', 'pgrep', 'curl')
LINUX = os.name == 'posix' and all(shutil.which(t) for t in TOOLS)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_until(check, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.2)
    return False


@unittest.skipUnless(LINUX, 'needs bash, flock, pgrep and curl (the GPU server is Linux)')
class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.app = self.dir / 'app'
        gpu = self.app / 'tools' / 'gpu'
        gpu.mkdir(parents=True)
        (self.app / 'tools' / 'service').mkdir()
        shutil.copy(ROOT / 'tools' / 'gpu' / 'watchdog.sh', gpu / 'watchdog.sh')
        self.calls = self.dir / 'calls.txt'
        for name in ('start_site', 'start_worker'):
            (gpu / f'{name}.sh').write_text(f'echo "{name} $*" >> "{self.calls}"\n')
        # The website stand-in: started as "python -u tools/service/local_server.py", the command line
        # the watchdog looks for.
        (self.app / 'tools' / 'service' / 'local_server.py').write_text(
            'import http.server, sys\n'
            'http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), http.server.SimpleHTTPRequestHandler)'
            '.serve_forever()\n')
        models = self.dir / 'models'
        (models / 'v1').mkdir(parents=True)
        (models / 'v1' / 'models').write_text('{"data": []}')
        self.site_port, self.live, self.dead = free_port(), free_port(), free_port()
        self.processes = []
        self.processes.append(subprocess.Popen([sys.executable, '-m', 'http.server', str(self.live), '--bind',
                                                '127.0.0.1', '--directory', str(models)],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.servers_env = self.dir / 'servers.env'
        self.servers_env.write_text(f'TRANSFORMX_PII_ENDPOINT=http://127.0.0.1:{self.live}/v1\n'
                                    f'TRANSFORMX_TRANSLATION_ENDPOINT=http://127.0.0.1:{self.live}/v1\n')
        self.log = self.dir / 'watchdog.log'
        self.pause = self.dir / 'paused'
        self.env = dict(os.environ, TRANSFORMX_WATCHDOG_SECONDS='1', TRANSFORMX_WATCHDOG_FAILURES='2',
                        TRANSFORMX_WATCHDOG_LOG=str(self.log), TRANSFORMX_PAUSE_FILE=str(self.pause),
                        TRANSFORMX_SERVERS_ENV=str(self.servers_env), TRANSFORMX_PORT=str(self.site_port),
                        TRANSFORMX_WATCHDOG_LOCK=str(self.dir / 'watchdog.lock'), TRANSFORMX_MIN_FREE_GB='0')
        wait_until(lambda: self.answers(self.live))

    def tearDown(self):
        for p in self.processes:
            p.kill()
            p.wait()
        shutil.rmtree(self.dir, ignore_errors=True)

    def answers(self, port):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            return False

    def start_site(self):
        p = subprocess.Popen([sys.executable, '-u', 'tools/service/local_server.py', str(self.site_port)],
                             cwd=self.app, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(p)
        self.assertTrue(wait_until(lambda: self.answers(self.site_port)))

    def start_watchdog(self):
        p = subprocess.Popen(['bash', str(self.app / 'tools' / 'gpu' / 'watchdog.sh')], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(p)
        self.assertTrue(wait_until(lambda: self.log.exists() and 'watchdog started' in self.log.read_text()))
        return p

    def recorded(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_everything_answering_restarts_nothing(self):
        self.start_site()
        self.start_watchdog()
        time.sleep(4)
        self.assertEqual(self.recorded(), [])

    def test_stopped_website_is_restarted(self):
        self.start_watchdog()
        self.assertTrue(wait_until(lambda: self.recorded()))
        self.assertEqual(self.recorded()[0], 'start_site --restart')
        self.assertIn('website stopped', self.log.read_text())

    def test_stopped_model_server_is_started_without_touching_the_website(self):
        self.servers_env.write_text(f'TRANSFORMX_PII_ENDPOINT=http://127.0.0.1:{self.live}/v1\n'
                                    f'TRANSFORMX_TRANSLATION_ENDPOINT=http://127.0.0.1:{self.dead}/v1\n')
        self.start_site()
        self.start_watchdog()
        self.assertTrue(wait_until(lambda: self.recorded()))
        self.assertEqual(self.recorded()[0], 'start_worker --servers-only')
        self.assertIn(f'port {self.dead}', self.log.read_text())

    def test_one_slow_check_is_not_enough(self):
        self.env['TRANSFORMX_WATCHDOG_FAILURES'] = '100'
        self.start_watchdog()
        time.sleep(3)
        self.assertEqual(self.recorded(), [])
        self.assertIn('check 1/100: website stopped', self.log.read_text())

    def test_pause_leaves_everything_alone(self):
        self.pause.touch()
        self.start_watchdog()
        time.sleep(4)
        self.assertEqual(self.recorded(), [])

    def test_only_one_watchdog_runs(self):
        self.pause.touch()
        self.start_watchdog()
        second = subprocess.run(['bash', str(self.app / 'tools' / 'gpu' / 'watchdog.sh')], env=self.env,
                                timeout=10, capture_output=True)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(self.log.read_text().count('watchdog started'), 1)


if __name__ == '__main__':
    unittest.main()
