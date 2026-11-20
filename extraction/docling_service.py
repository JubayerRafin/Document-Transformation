"""Loopback service that keeps Docling's table models loaded, shared by all jobs of a worker.

Loading Docling's layout and table models took about 13 s per job on an L40S, against about 0.8 s
per page once loaded, so table extraction was 17-23 s of a 26 s two-page translation (2026-11-21).
The website starts this service once (optional_shared_docling); a job's DoclingTableExtractor asks it
for each page and loads the models itself when the service is absent or fails.
"""
import contextlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

ENV = 'TRANSFORMX_DOCLING_SERVICE'
KEY = 'TRANSFORMX_DOCLING_KEY'


def request(address, key, operation, payload=None, timeout=300):
    from multiprocessing.connection import Client
    with Client(tuple(address), authkey=bytes.fromhex(key)) as connection:
        connection.send((operation, payload))
        if not connection.poll(timeout):
            raise TimeoutError('Docling service did not respond within its deadline.')
        ok, value = connection.recv()
    if not ok:
        raise RuntimeError('Docling service failed: ' + value)
    return value


def configured():
    """(address, key) of the service named in the environment, or None."""
    address, key = os.environ.get(ENV), os.environ.get(KEY)
    if not address or not key:
        return None
    address = json.loads(address)
    if len(address) != 2 or address[0] != '127.0.0.1':
        raise ValueError('Docling service must use IPv4 loopback.')
    return address, key


def ping(timeout=5):
    service = configured()
    if not service:
        return False
    try:
        request(*service, 'ping', timeout=timeout)
        return True
    except Exception:
        return False


def extract_page(pdf_path, page_num, tables_dir, start_index=0):
    """{'elements': table fields (dicts), 'page_results': [...], 'errors': [...]} for one page."""
    service = configured()
    if not service:
        raise RuntimeError('No Docling service configured.')
    return request(*service, 'extract', {'pdf': str(pdf_path), 'page': int(page_num),
                                         'tables_dir': str(tables_dir), 'start_index': int(start_index)})


@contextlib.contextmanager
def shared_docling(python, folder, startup_timeout=300):
    """Start the service and name it in this process's environment (inherited by jobs)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    previous = {name: os.environ.get(name) for name in (ENV, KEY)}
    with tempfile.TemporaryDirectory(prefix='docling-', dir=folder) as temp:
        ready = Path(temp) / 'ready.json'
        env = dict(os.environ)
        env.pop(ENV, None)
        env[KEY] = key
        started = time.monotonic()
        root = Path(__file__).resolve().parents[1]
        with (folder / 'docling-service.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([str(python), '-m', 'extraction.docling_service', str(ready)], env=env,
                                       cwd=root, stdout=log, stderr=subprocess.STDOUT)
            address = None
            try:
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError('Docling service startup failed; see docling-service.log.')
                    if time.monotonic() - started > startup_timeout:
                        raise TimeoutError('Docling service startup exceeded its deadline.')
                    time.sleep(.2)
                metadata = json.loads(ready.read_text(encoding='utf-8'))
                address = metadata['address']
                metadata['startup_seconds'] = round(time.monotonic() - started, 3)
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
                    except Exception:
                        pass
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)


@contextlib.contextmanager
def optional_shared_docling(python, folder):
    """The service when it can start (the engine's Python exists and TRANSFORMX_SHARED_DOCLING is not
    "0"); otherwise nothing, and each job loads the models itself."""
    if os.environ.get('TRANSFORMX_SHARED_DOCLING') == '0' or not python or not Path(python).is_file():
        yield None
        return
    stack = contextlib.ExitStack()
    try:
        metadata = stack.enter_context(shared_docling(python, folder))
    except Exception as error:
        stack.close()
        print(f'Shared table models unavailable ({type(error).__name__}); each job loads its own.', flush=True)
        yield None
        return
    with stack:
        yield metadata


def _warm(extractor):
    """Run one page with a small table, so the first job does not wait for the models."""
    import pymupdf
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'warm.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for row in range(4):
                for col in range(3):
                    rect = pymupdf.Rect(72 + col * 120, 100 + row * 24, 192 + col * 120, 124 + row * 24)
                    page.draw_rect(rect)
                    page.insert_text((rect.x0 + 4, rect.y0 + 16), f'Cell {row}{col}', fontsize=10)
            pdf.save(path)
        extractor.tables_dir = temp
        extractor.extract_page(str(path), 1)


def serve(ready):
    from multiprocessing.connection import Listener
    import threading
    from extraction.deep_doc_adapter import DoclingAdapter
    started = time.monotonic()
    parent = os.getppid()

    def watch_parent():
        # Exit with the website or worker that started it (see transformation/pdf/layout_service.py).
        while True:
            time.sleep(2)
            if os.getppid() != parent:
                os._exit(0)
    threading.Thread(target=watch_parent, daemon=True).start()
    os.environ.pop(ENV, None)  # the extractor below loads the models itself
    extractor = DoclingAdapter({}, tempfile.mkdtemp(prefix='docling-tables-'))
    if not extractor.available:
        raise SystemExit('Docling is not available in this Python.')
    _warm(extractor)
    key = bytes.fromhex(os.environ[KEY])
    lock = threading.Lock()
    listener = Listener(('127.0.0.1', 0), authkey=key)
    Path(ready).write_text(json.dumps({'address': list(listener.address), 'pid': os.getpid(),
                                       'load_seconds': round(time.monotonic() - started, 3)}), encoding='utf-8')
    print(f'Docling service ready in {time.monotonic() - started:.1f} s.', flush=True)
    stopping = threading.Event()

    def handle(connection):
        with connection:
            try:
                operation, payload = connection.recv()
                if operation == 'ping':
                    connection.send((True, 'pong'))
                elif operation == 'stop':
                    connection.send((True, 'stopping'))
                    stopping.set()
                    os._exit(0)
                elif operation == 'extract':
                    Path(payload['tables_dir']).mkdir(parents=True, exist_ok=True)
                    with lock:  # one page at a time on the shared models
                        extractor.tables_dir = payload['tables_dir']
                        extractor.page_results, extractor.errors = [], []
                        elements = extractor.extract_page(payload['pdf'], payload['page'], payload['start_index'])
                        reply = {'page_results': extractor.page_results, 'errors': extractor.errors, 'elements': [
                            {name: getattr(e, name) for name in ('page_num', 'y_coordinate', 'element_type', 'content',
                                                                 'bbox', 'matrix', 'cell_records', 'source_engine')
                             if hasattr(e, name)} for e in elements]}
                    connection.send((True, reply))
                else:
                    connection.send((False, 'unknown operation'))
            except Exception as error:
                try:
                    connection.send((False, f'{type(error).__name__}: {error}'))
                except Exception:
                    pass

    while not stopping.is_set():
        try:
            connection = listener.accept()
        except Exception:
            continue
        threading.Thread(target=handle, args=(connection,), daemon=True).start()


if __name__ == '__main__':
    import sys
    serve(sys.argv[1])
