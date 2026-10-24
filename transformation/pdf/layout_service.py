"""Batch-scoped, authenticated loopback service for the unchanged layout model.

Only the ONNX session is shared. PDF objects, translation hooks, and job state
remain in their original isolated workers. No predictions are cached.
"""
import contextlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

ENV = 'TRANSFORMX_LAYOUT_SERVICE'
KEY = 'TRANSFORMX_LAYOUT_KEY'


def request(address, key, operation, payload=None, timeout=120):
    from multiprocessing.connection import Client
    with Client(tuple(address), authkey=bytes.fromhex(key)) as connection:
        connection.send((operation, payload))
        if not connection.poll(timeout):
            raise TimeoutError('Layout service did not respond within its deadline.')
        ok, value = connection.recv()
    if not ok:
        raise RuntimeError('Layout service failed: ' + value)
    return value


@contextlib.contextmanager
def shared_layout(python, folder, startup_timeout=120):
    """Start once per batch; restore environment and reap the child on exit."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    previous = {name: os.environ.get(name) for name in (ENV, KEY)}
    with tempfile.TemporaryDirectory(prefix='layout-', dir=folder) as temp:
        ready = Path(temp) / 'ready.json'
        env = dict(os.environ)
        env.pop(ENV, None)
        env[KEY] = key
        started = time.monotonic()
        with (folder / 'layout-service.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(
                [str(python), '-m', __name__, str(ready)], env=env,
                stdout=log, stderr=subprocess.STDOUT,
            )
            address = None
            try:
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError('Layout service startup failed; see layout-service.log.')
                    if time.monotonic() - started > startup_timeout:
                        raise TimeoutError('Layout service initialization exceeded its deadline.')
                    time.sleep(.1)
                metadata = json.loads(ready.read_text(encoding='utf-8'))
                address = metadata['address']
                metadata['startup_seconds'] = round(time.monotonic() - started, 6)
                (folder / 'layout-service.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                os.environ[ENV] = json.dumps(address)
                os.environ[KEY] = key
                yield metadata
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                if process.poll() is None and address:
                    try:
                        request(address, key, 'stop', timeout=2)
                    except (OSError, EOFError, TimeoutError, RuntimeError):
                        pass
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)


def install(model_class=None):
    """Replace only session construction/run; retain vendor preprocessing/NMS."""
    address, key = os.environ.get(ENV), os.environ.get(KEY)
    if not address:
        return False
    if not key:
        raise RuntimeError('Layout service authentication is missing.')
    address = json.loads(address)
    if len(address) != 2 or address[0] != '127.0.0.1':
        raise ValueError('Layout service must use IPv4 loopback.')
    if model_class is None:
        from babeldoc.docvision.doclayout import OnnxModel
        model_class = OnnxModel
    if getattr(model_class, '_transformx_shared_layout', False):
        return True
    import threading

    class Session:
        def run(self, outputs, inputs):
            return request(address, key, 'run', (outputs, inputs))

    def initialize(self, model_path):
        # Match the exact asset selected by the vendor in this job.
        import hashlib
        digest = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        metadata = request(address, key, 'metadata', digest)
        self.model_path = model_path
        self._stride = metadata['stride']
        self._names = metadata['names']
        self.model = Session()
        self.lock = threading.Lock()

    model_class.__init__ = initialize
    model_class._transformx_shared_layout = True
    return True


def serve(ready):
    from multiprocessing.connection import Listener
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.assets.assets import get_doclayout_onnx_model_path
    import hashlib
    started = time.monotonic()
    path = get_doclayout_onnx_model_path()
    model = OnnxModel(str(path))
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    metadata = {'stride': model._stride, 'names': model._names}
    with Listener(('127.0.0.1', 0), authkey=bytes.fromhex(os.environ[KEY])) as listener:
        # Bound orphan lifetime if the owning batch is interrupted abruptly.
        listener._listener._socket.settimeout(3600)
        pending = Path(ready).with_suffix('.tmp')
        pending.write_text(json.dumps({'address': listener.address,
            'model_sha256': digest, 'model_initialization_seconds': time.monotonic()-started,
            'prediction_cache': False}), encoding='utf-8')
        pending.replace(ready)
        calls = 0
        try:
            while True:
                with listener.accept() as connection:
                    if not connection.poll(120):
                        raise TimeoutError('Layout client stalled.')
                    operation, payload = connection.recv()
                    try:
                        if operation == 'stop':
                            connection.send((True, None))
                            break
                        if operation == 'metadata':
                            if payload != digest:
                                raise ValueError('Layout model checksum differs from the batch session.')
                            value = metadata
                        elif operation == 'run':
                            value = model.model.run(*payload)
                            calls += 1
                        else:
                            raise ValueError('Unknown layout operation.')
                        connection.send((True, value))
                    except Exception as error:
                        connection.send((False, type(error).__name__ + ': ' + str(error)))
        finally:
            print(json.dumps({'inference_calls': calls, 'session_count': 1}), flush=True)


if __name__ == '__main__':
    serve(sys.argv[1])
