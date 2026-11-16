"""Start job processes from a pre-loaded parent, so each job skips the same heavy imports.

Every translation job used to start fresh Python processes and pay their imports again: about 3 s
for BabelDOC and 6 s for Docling in the document engine (measured 2026-11-16 on the RTX 4090 pod).
A preload server imports those modules once, then forks one child per job. The child runs exactly
what `python -m <module> <args>` would run, with the job's own working directory, environment and
log file, in its own process, so jobs stay isolated from each other as before.

Fork safety: the server only imports (and runs an optional warm-up); it refuses to serve if any
thread is running, and it never touches the GPU. Children initialise CUDA themselves.

Linux only, opt-in with TRANSFORMX_PRELOAD=1; anything unexpected falls back to a normal process.
The server listens on a Unix socket in a private directory and exits after 30 idle minutes.
"""
import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENABLE_ENV = 'TRANSFORMX_PRELOAD'
IDLE_SECONDS = 1800
# What each job entry point imports before it does any work. Only pure imports belong here.
PRELOADS = {
    'transformation.pdf.babeldoc_worker': (
        'babeldoc.main', 'babeldoc.format.pdf.high_level', 'babeldoc.docvision.doclayout',
        'babeldoc.format.pdf.document_il.utils.fontmap', 'transformation.llm.ollama_native',
        'transformation.llm.translategemma', 'transformation.pdf.babeldoc_worker'),
    'backend.engine_job': (
        'transformation.workflows', 'transformation.pipeline', 'extraction.coordinator',
        'extraction.deep_doc_adapter', 'docling.document_converter', 'backend.engine_job'),
}
# Optional warm-up run in the server after the imports (must not start threads or use the GPU).
WARMUPS = {'transformation.pdf.babeldoc_worker': 'transformation.pdf.babeldoc_speed:warm'}


def enabled():
    return os.environ.get(ENABLE_ENV) == '1' and os.name == 'posix' and hasattr(os, 'fork')


def socket_path(python, module, pythonpath):
    key = hashlib.sha256(json.dumps([str(python), module, pythonpath, PRELOADS.get(module)]).encode()).hexdigest()[:16]
    folder = Path(tempfile.gettempdir()) / f'transformx-preload-{os.getuid()}'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
        raise RuntimeError('Preload directory is not private.')
    return folder / f'{key}.sock'


# ---------------------------------------------------------------- server (runs in the job's Python)

def _child(request):
    code = 1
    try:
        os.setsid()
        log = os.open(request['stdout'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(log, 1); os.dup2(log, 2); os.close(log)
        os.chdir(request['cwd'])
        os.environ.clear(); os.environ.update(request['env'])
        if request.get('unbuffered'):  # python -u
            sys.stdout.reconfigure(line_buffering=True, write_through=True)
            sys.stderr.reconfigure(line_buffering=True, write_through=True)
        if request['cwd'] not in sys.path: sys.path.insert(0, request['cwd'])
        sys.argv = [request['module']] + list(request['args'])
        import runpy
        runpy.run_module(request['module'], run_name='__main__', alter_sys=True)
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if exc.code is not None and not isinstance(exc.code, int): print(exc.code, file=sys.stderr)
    except BaseException:
        import traceback
        traceback.print_exc()
    finally:
        try: sys.stdout.flush(); sys.stderr.flush()
        except Exception: pass
        os._exit(code)


def serve(path, module):
    import importlib
    for name in PRELOADS[module]: importlib.import_module(name)
    if module in WARMUPS:
        target, _, function = WARMUPS[module].partition(':')
        getattr(importlib.import_module(target), function)()
    if threading.active_count() != 1:
        raise RuntimeError('Preloading started a thread; forking would be unsafe.')
    path = Path(path)
    if path.exists(): path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path)); os.chmod(path, 0o600); listener.listen(8)
    listener.settimeout(IDLE_SECONDS)
    print('preload ready', flush=True)
    try:
        while True:
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                return
            with conn:
                conn.settimeout(30)
                try:
                    request = json.loads(conn.makefile('r').readline() or 'null')
                    if not isinstance(request, dict) or not {'module', 'args', 'cwd', 'env', 'stdout'} <= set(request):
                        continue  # a liveness probe or a broken client: keep serving
                except (OSError, ValueError):
                    continue
                pid = os.fork()
                if pid == 0:
                    listener.close(); conn.close(); _child(request)
                conn.sendall((json.dumps({'pid': pid}) + '\n').encode())
                _, status = os.waitpid(pid, 0)
                try: conn.sendall((json.dumps({'returncode': os.waitstatus_to_exitcode(status)}) + '\n').encode())
                except OSError: pass
    finally:
        listener.close()
        if path.exists(): path.unlink()


# ---------------------------------------------------------------- client (used where Popen was)

class PreloadedProcess:
    """The parts of subprocess.Popen the engine uses: pid, poll, wait, terminate, kill, returncode."""

    def __init__(self, path, module, args, cwd, env, stdout, unbuffered=False):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(10); self._sock.connect(str(path))
        self._sock.sendall((json.dumps({'module': module, 'args': list(args), 'cwd': str(cwd), 'env': dict(env),
                                        'stdout': str(stdout), 'unbuffered': unbuffered}) + '\n').encode())
        self._buffer = b''
        self._sock.settimeout(60)
        self.pid = self._message()['pid']
        self.returncode = None
        self.args = [module, *args]

    def _message(self, timeout=None):
        while b'\n' not in self._buffer:
            if timeout is not None:
                ready, _, _ = select.select([self._sock], [], [], timeout)
                if not ready: return None
            chunk = self._sock.recv(4096)
            if not chunk: raise ConnectionError('Preload server closed the connection.')
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b'\n')
        return json.loads(line)

    def poll(self):
        if self.returncode is None:
            try:
                message = self._message(timeout=0)
            except (OSError, ValueError):
                message = {'returncode': -1}
            if message is not None: self.returncode = message['returncode']; self._sock.close()
        return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.05)
        return self.returncode

    def _signal(self, sig):
        if self.returncode is None:
            try: os.kill(self.pid, sig)
            except ProcessLookupError: pass

    def terminate(self): self._signal(signal.SIGTERM)
    def kill(self): self._signal(signal.SIGKILL)


def _ensure_server(python, module, env):
    path = socket_path(python, module, env.get('PYTHONPATH', ''))
    if path.exists():
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); probe.settimeout(2)
            probe.connect(str(path)); probe.close()
            # A connection with no request is dropped by the server; it is alive.
            return path
        except OSError:
            path.unlink(missing_ok=True)
    log = path.with_suffix('.log')
    with open(log, 'a') as out:
        server = subprocess.Popen([str(python), '-m', 'transformation.preload', str(path), module],
                                  cwd=str(Path(__file__).resolve().parents[1]), env=dict(env),
                                  stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if path.exists(): return path
        if server.poll() is not None: raise RuntimeError(f'Preload server exited; see {log}.')
        time.sleep(.1)
    server.kill()
    raise RuntimeError('Preload server did not start in time.')


def _parse(command):
    """(module, args, unbuffered) for [python, (-u), -m, module, args...], else None."""
    rest = list(command[1:]); unbuffered = False
    if rest[:1] == ['-u']: unbuffered = True; rest = rest[1:]
    if len(rest) >= 2 and rest[0] == '-m' and rest[1] in PRELOADS: return rest[1], rest[2:], unbuffered
    return None


def popen(command, cwd, stdout, env=None, **popen_kwargs):
    """Start `python [-u] -m module args` from a preload server when enabled and supported, else Popen.
    stdout is an open file whose name is the log path; stderr goes to the same log."""
    env = dict(os.environ if env is None else env)
    parsed = _parse(command)
    if enabled() and parsed and getattr(stdout, 'name', None):
        module, args, unbuffered = parsed
        try:
            path = _ensure_server(command[0], module, env)
            stdout.flush()
            return PreloadedProcess(path, module, args, cwd, env, stdout.name, unbuffered)
        except Exception as exc:  # never block a job on the optimisation
            print(f'Preload unavailable ({exc}); starting a normal process.', file=sys.stderr, flush=True)
    return subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=subprocess.STDOUT, env=env, **popen_kwargs)


if __name__ == '__main__':
    serve(sys.argv[1], sys.argv[2])
