"""Standalone TransformX engine service: an HTTP API in front of the document engine, with no database,
no user accounts and no hosting dependency, so a deployment can put its own sign-in, storage and
front end around it (for example an internal Docker deployment).

    python tools/service/engine_server.py --config configs/engine.json --root /data/transformx --port 8080

API (JSON unless noted):
    GET    /health                     {"status": "ok", "queued": n, "running": job id or null}
    POST   /v1/jobs                    body: the PDF (Content-Type: application/pdf, at most 50 MB)
                                       query: operation=translation|pii|combined, source_language=en,
                                       target_language=ko, pii_categories=Names,Email addresses,...,
                                       pii_style=box|label; optional header X-TransformX-Options with a
                                       JSON object for terminology {"term": "required translation"},
                                       protected_values [...] and document_context "..."
                                       -> 202 {"job_id": "..."}
    GET    /v1/jobs                    [{"job_id", "status", "created"}]
    GET    /v1/jobs/{id}               {"job_id", "status": queued|running|generated|failed|cancelled,
                                        "stage", "failure_code", "warnings", "seconds"}
    GET    /v1/jobs/{id}/result        the output PDF once generated (application/pdf)
    DELETE /v1/jobs/{id}               cancels a queued or running job and deletes its files

Sign-in: with TRANSFORMX_SERVICE_API_KEY (or TRANSFORMX_SERVICE_API_KEY_FILE) set, every request
except /health needs "Authorization: Bearer <key>". Without it the service has no sign-in and must sit
behind a proxy that provides it; it listens on 127.0.0.1 unless --host says otherwise.

Jobs run one at a time on the GPU (TRANSFORMX_SERVICE_WORKERS to change), with the same engine, OCR
session and shared layout model as the site worker. Files of finished jobs are deleted after
TRANSFORMX_SERVICE_RETENTION_HOURS (default 24). Model endpoints, names and keys are configured as for
the worker (transformation/llm/endpoints.py).
"""
import argparse
import hmac
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
MAX_BYTES = 50_000_000
OPERATIONS = {'translation', 'pii', 'combined'}
LANGUAGE = re.compile(r'^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$')
DEFAULT_CATEGORIES = ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts']
JOB_ID = re.compile(r'^[0-9a-f]{32}$')


class Problem(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def secret(name):
    value = os.environ.get(name, '').strip()
    if value:
        return value
    path = os.environ.get(name + '_FILE', '').strip()
    return Path(path).read_text(encoding='utf-8').strip() if path else ''


def job_options(query, header):
    """The engine's job options from the request, validated."""
    q = {k: v[-1] for k, v in parse_qs(query).items()}
    operation = q.get('operation', 'translation')
    if operation not in OPERATIONS:
        raise Problem(400, 'INVALID_OPERATION')
    source, target = q.get('source_language', 'en'), q.get('target_language', 'ko')
    if not LANGUAGE.match(source) or not LANGUAGE.match(target) or (operation != 'pii' and source == target):
        raise Problem(400, 'INVALID_LANGUAGE')
    categories = [c.strip() for c in q.get('pii_categories', '').split(',') if c.strip()] or DEFAULT_CATEGORIES
    style = q.get('pii_style', 'box')
    if style not in {'box', 'label'}:
        raise Problem(400, 'INVALID_PII_STYLE')
    options = {'operation': operation, 'source_language': source, 'target_language': target, 'output_format': 'pdf',
               'protected_values': [], 'document_context': '', 'pii_categories': categories, 'pii_style': style}
    # One of the translation models in transformx.toml ([translation_models.<id>]); default: [translation].
    model = q.get('translation_model', 'default')
    if model != 'default' and operation != 'pii':
        from transformation import settings
        if model not in {c['id'] for c in settings.translation_choices()}:
            raise Problem(400, 'UNKNOWN_TRANSLATION_MODEL')
        options['translation_model'] = model
    if header:
        try:
            extra = json.loads(header)
        except ValueError:
            raise Problem(400, 'INVALID_OPTIONS')
        if not isinstance(extra, dict) or set(extra) - {'terminology', 'protected_values', 'document_context'}:
            raise Problem(400, 'INVALID_OPTIONS')
        if 'terminology' in extra:
            from transformation.llm.policy import validate_terminology
            try:
                options['terminology'] = validate_terminology(extra['terminology'])
            except (ValueError, TypeError):
                raise Problem(400, 'INVALID_GLOSSARY')
        if 'protected_values' in extra:
            values = extra['protected_values']
            if not isinstance(values, list) or len(values) > 200 or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in values):
                raise Problem(400, 'INVALID_OPTIONS')
            options['protected_values'] = values
        if 'document_context' in extra:
            if not isinstance(extra['document_context'], str) or len(extra['document_context']) > 2000:
                raise Problem(400, 'INVALID_OPTIONS')
            options['document_context'] = extra['document_context']
    return options


def run_engine(folder, request, profile, env):
    """Start the engine for one job (as the site worker does); returns a process-like object."""
    from transformation.preload import popen
    log = (folder / 'process.log').open('w', encoding='utf-8')
    return popen([profile.get('engine_python', sys.executable), '-u', '-m', 'backend.engine_job', str(request),
                  str(folder / 'events.jsonl'), str(folder / 'terminal.json')], APP, log, env)


class Service:
    def __init__(self, root, profile, api_key='', workers=1, retention_hours=24.0, runner=run_engine):
        self.root, self.profile, self.api_key, self.runner = Path(root), profile, api_key, runner
        self.retention = retention_hours * 3600
        (self.root / 'jobs').mkdir(parents=True, exist_ok=True)
        self.pending, self.lock, self.running, self.cancelled = queue.Queue(), threading.Lock(), {}, set()
        self.ocr_session = None
        for _ in range(max(1, workers)):
            threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._clean, daemon=True).start()

    def folder(self, job_id):
        if not JOB_ID.match(job_id or ''):
            raise Problem(404, 'NOT_FOUND')
        path = self.root / 'jobs' / job_id
        if not path.is_dir():
            raise Problem(404, 'NOT_FOUND')
        return path

    def submit(self, data, options):
        if not data.startswith(b'%PDF'):
            raise Problem(415, 'NOT_A_PDF')
        job_id = uuid.uuid4().hex
        folder = self.root / 'jobs' / job_id
        folder.mkdir(parents=True)
        (folder / 'source.pdf').write_bytes(data)
        profile = self.profile
        if options.get('translation_model'):
            from transformation import settings
            choice = {c['id']: c for c in settings.translation_choices()}[options['translation_model']]
            profile = settings.choice_profile(choice, self.profile)
        (folder / 'request.json').write_text(json.dumps({'source': str(folder / 'source.pdf'), 'filename': 'source.pdf',
                                                         'options': options, 'profile': profile}), encoding='utf-8')
        (folder / 'status.json').write_text(json.dumps({'status': 'queued', 'created': time.time()}), encoding='utf-8')
        self.pending.put(job_id)
        return job_id

    def status(self, job_id):
        folder = self.folder(job_id)
        state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        result = {'job_id': job_id, 'status': state['status'], 'created': state.get('created')}
        events = folder / 'events.jsonl'
        if events.exists():
            lines = events.read_text(encoding='utf-8').splitlines()
            if lines:
                result['stage'] = json.loads(lines[-1]).get('stage')
        terminal = folder / 'terminal.json'
        if state['status'] in {'generated', 'failed'} and terminal.exists():
            done = json.loads(terminal.read_text(encoding='utf-8'))
            result.update(failure_code=done.get('failure_code'), warnings=done.get('warnings', []), seconds=done.get('seconds'))
        return result

    def result(self, job_id):
        folder = self.folder(job_id)
        if json.loads((folder / 'status.json').read_text(encoding='utf-8'))['status'] != 'generated':
            raise Problem(409, 'NOT_READY')
        output = Path(json.loads((folder / 'terminal.json').read_text(encoding='utf-8'))['output_file'])
        if not output.is_file():
            raise Problem(410, 'RESULT_REMOVED')
        return output.read_bytes()

    def cancel(self, job_id):
        folder = self.folder(job_id)
        with self.lock:
            self.cancelled.add(job_id)
            process = self.running.get(job_id)
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass
        shutil.rmtree(folder, ignore_errors=True)

    def jobs(self):
        found = []
        for folder in sorted((self.root / 'jobs').iterdir()):
            try:
                state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
                found.append({'job_id': folder.name, 'status': state['status'], 'created': state.get('created')})
            except (OSError, ValueError, KeyError):
                continue
        return found

    def _set(self, folder, status):
        path = folder / 'status.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state['status'] = status
        path.write_text(json.dumps(state), encoding='utf-8')

    def _work(self):
        while True:
            job_id = self.pending.get()
            folder = self.root / 'jobs' / job_id
            with self.lock:
                if job_id in self.cancelled or not folder.is_dir():
                    continue
            try:
                self._set(folder, 'running')
                env = dict(os.environ)
                model = json.loads((folder / 'request.json').read_text(encoding='utf-8'))['options'].get('translation_model')
                if model:
                    # The chosen model's server and key file reach only this job's process.
                    from transformation import settings
                    choice = {c['id']: c for c in settings.translation_choices()}.get(model)
                    if choice is None:
                        raise RuntimeError('translation model no longer configured')
                    env = settings.choice_environment(choice, env)
                if self.ocr_session:
                    self.ocr_session.ensure()
                    env['TRANSFORMX_OCR_SERVICE_PID'] = str(self.ocr_session.process.pid)
                process = self.runner(folder, folder / 'request.json', self.profile, env)
                with self.lock:
                    self.running[job_id] = process
                process.wait()
                with self.lock:
                    self.running.pop(job_id, None)
                    if job_id in self.cancelled:
                        continue
                terminal = folder / 'terminal.json'
                done = json.loads(terminal.read_text(encoding='utf-8')) if terminal.exists() else {}
                self._set(folder, 'generated' if done.get('status') == 'generated' else 'failed')
            except Exception:
                if folder.is_dir():
                    try:
                        self._set(folder, 'failed')
                    except Exception:
                        pass

    def _clean(self):
        while True:
            cutoff = time.time() - self.retention
            for folder in (self.root / 'jobs').iterdir():
                try:
                    state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
                    if state['status'] in {'generated', 'failed'} and state.get('created', time.time()) < cutoff:
                        shutil.rmtree(folder, ignore_errors=True)
                except (OSError, ValueError, KeyError):
                    continue
            time.sleep(600)


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'TransformXEngine'

        def log_message(self, *args):
            pass

        def _send(self, status, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            if self.close_connection:
                self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self):
            if not service.api_key:
                return True
            supplied = self.headers.get('Authorization', '')
            return hmac.compare_digest(supplied.encode(), ('Bearer ' + service.api_key).encode())

        def _route(self, method):
            url = urlparse(self.path)
            parts = [p for p in url.path.split('/') if p]
            # A refused request whose body was not read ends its connection, so the unread bytes are
            # never taken for the next request.
            self.close_connection = bool(self.headers.get('Content-Length') or self.headers.get('Transfer-Encoding'))
            try:
                if parts == ['health'] and method == 'GET':
                    return self._send(200, {'status': 'ok', 'queued': service.pending.qsize(),
                                            'running': next(iter(service.running), None)})
                if not self._authorised():
                    raise Problem(401, 'UNAUTHORISED')
                if parts == ['v1', 'jobs'] and method == 'GET':
                    return self._send(200, service.jobs())
                if parts == ['v1', 'translation-models'] and method == 'GET':
                    from transformation import settings
                    return self._send(200, [{'id': c['id'], 'label': c['label']} for c in settings.translation_choices()])
                if parts == ['v1', 'jobs'] and method == 'POST':
                    length = int(self.headers.get('Content-Length') or 0)
                    if length <= 0 or length > MAX_BYTES:
                        raise Problem(413, 'FILE_TOO_LARGE' if length > MAX_BYTES else 'EMPTY_BODY')
                    # Read the upload before checking the options so a refusal reaches the client
                    # instead of a reset connection.
                    data = self.rfile.read(length)
                    self.close_connection = False
                    options = job_options(url.query, self.headers.get('X-TransformX-Options'))
                    return self._send(202, {'job_id': service.submit(data, options)})
                if len(parts) == 3 and parts[:2] == ['v1', 'jobs'] and method == 'GET':
                    return self._send(200, service.status(parts[2]))
                if len(parts) == 4 and parts[:2] == ['v1', 'jobs'] and parts[3] == 'result' and method == 'GET':
                    return self._send(200, service.result(parts[2]), 'application/pdf')
                if len(parts) == 3 and parts[:2] == ['v1', 'jobs'] and method == 'DELETE':
                    service.cancel(parts[2])
                    return self._send(200, {'job_id': parts[2], 'status': 'cancelled'})
                raise Problem(404, 'NOT_FOUND')
            except Problem as problem:
                return self._send(problem.status, {'error': problem.code})
            except Exception:
                return self._send(500, {'error': 'INTERNAL_ERROR'})

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def do_DELETE(self):
            self._route('DELETE')

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', default=str(APP / 'configs/engine.json'),
                        help='optional JSON with an "engine_profile" object (the same profile the site worker '
                             'uses); transformx.toml can set everything instead')
    parser.add_argument('--root', help='folder for job files (default: service.jobs_folder or runs/engine-service)')
    parser.add_argument('--host', help='default: service.host or 127.0.0.1')
    parser.add_argument('--port', type=int, help='default: service.port or 8080')
    parser.add_argument('--settings', help='transformx.toml (default: $TRANSFORMX_SETTINGS or configs/transformx.toml)')
    args = parser.parse_args()
    from tools.service.cloud_worker import apply_profile_overrides
    from transformation import settings
    # Engine profile JSON < transformx.toml < environment variables.
    profile = json.loads(Path(args.config).read_text(encoding='utf-8')).get('engine_profile', {})         if Path(args.config).is_file() else {}
    settings.apply(profile, args.settings)
    profile = apply_profile_overrides(profile)
    args.root = args.root or os.environ.get('TRANSFORMX_SERVICE_ROOT') or str(APP / 'runs/engine-service')
    args.host = args.host or os.environ.get('TRANSFORMX_SERVICE_HOST') or '127.0.0.1'
    args.port = args.port or int(os.environ.get('TRANSFORMX_SERVICE_PORT') or 8080)
    global MAX_BYTES
    MAX_BYTES = int(float(os.environ.get('TRANSFORMX_SERVICE_MAX_MB') or MAX_BYTES / 1_000_000) * 1_000_000)
    for key in ('python', 'ocr_python'):
        profile.setdefault(key, sys.executable)
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from transformation.ocr.service import managed_session, prewarm
    from transformation.pdf.layout_service import optional_shared_layout
    persistent = bool(profile.get('persistent_ocr')) and os.name == 'posix'
    with managed_session(profile['ocr_python'], root, persistent) as session, \
            optional_shared_layout(profile.get('python'), root / 'layout-service'):
        if session:
            prewarm(root / 'prewarm')
        service = Service(root, profile, secret('TRANSFORMX_SERVICE_API_KEY'),
                          int(os.environ.get('TRANSFORMX_SERVICE_WORKERS', '1') or 1),
                          float(os.environ.get('TRANSFORMX_SERVICE_RETENTION_HOURS', '24') or 24))
        service.ocr_session = session
        server = ThreadingHTTPServer((args.host, args.port), handler(service))
        print(f'TransformX engine service on http://{args.host}:{args.port}'
              + (' (API key required)' if service.api_key else ' (no sign-in: put a proxy in front)'), flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
