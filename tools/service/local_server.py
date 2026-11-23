"""TransformX on one machine: the website, its API and the document engine, with no accounts.

The website (frontend/out) is served next to the engine on the GPU server, so there is no hosted
API, database, sign-in or server registration. Open it through an SSH tunnel:

    ssh -L 8080:localhost:8080 user@gpu-server      then   http://localhost:8080

The API answers the same /v1 requests the website already makes. Jobs run one at a time with the
site worker's own job runner (tools/service/cloud_worker.py), so the engine path is unchanged.
Everything is stored under --root (default runs/local): one folder per job with the source, the
result, job.json and the engine's files. Set TRANSFORMX_PASSWORD to ask every visitor for a
password (HTTP basic sign-in; any user name), for example before opening a public address.
"""
import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1])]  # the site worker's runner, and the project's packages
import cloud_worker as runner  # noqa: E402  (the site worker's job runner, reused as it is)

PROJECT = runner.PROJECT
# Largest upload and longest document accepted (the website reads them from /v1/workers).
MAX_BYTES = int(float(os.environ.get('TRANSFORMX_SERVICE_MAX_MB', '100')) * 1_000_000)
# Long manuals (the Samsung user guide has 302 pages) are accepted; a 1,000-page job takes about two hours.
MAX_PAGES = int(os.environ.get('TRANSFORMX_MAX_PAGES', '1000'))
SERVER_ID = 'local'
STATUS_SECONDS = 45
CATEGORIES = ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts', 'Dates of birth']
OPTION_KEYS = {'operation', 'source_language', 'target_language', 'output_format', 'protected_values',
               'document_context', 'pii_categories', 'worker_id', 'pii_style', 'pii_keep', 'translation_model',
               'pii_model'}
LANGUAGE = re.compile(r'^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$')
MODEL_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
KEY = re.compile(r'^[A-Za-z0-9_-]{8,120}$')
JOB_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class Fault(Exception):
    """An API error the website shows by its code (frontend/src/lib/api.ts MESSAGES)."""
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def default_profile():
    """The engine profile the site's Workers panel used to download (cloud_worker.register)."""
    return {'python': sys.executable, 'ocr_python': sys.executable,
            'ocr_device': 'gpu:0', 'persistent_ocr': os.name == 'posix',
            'translation_endpoint': 'http://127.0.0.1:11434/v1', 'pii_endpoint': 'http://127.0.0.1:11434/v1',
            'timeout_seconds': 3600,
            'engine_options': {'table_strategy': 'auto', 'pdf_page_mode': 'document', 'http_keepalive': True,
                               'nativeized_ir_mode': 'reuse', 'babeldoc_dual_output': False,
                               'figure_engine': 'ocr-slots', 'light_pdf_io': True, 'ocr_cache_mode': 'reuse',
                               'translation_qps': 8}}


def check_options(raw):
    """The website's job options, checked like the hosted API did (cloud/src/index.mjs options())."""
    if not isinstance(raw, dict):
        raise Fault('INVALID_OPTIONS')
    if set(raw) - OPTION_KEYS:
        raise Fault('UNKNOWN_OPTION')
    o = {'operation': 'translation', 'source_language': 'en', 'target_language': 'ko', 'output_format': 'pdf',
         'protected_values': [], 'document_context': '', 'pii_categories': [], **raw}
    o.pop('worker_id', None)
    if o['operation'] not in ('translation', 'pii', 'combined') or o['output_format'] != 'pdf':
        raise Fault('INVALID_OPERATION')
    if not all(isinstance(v, str) and LANGUAGE.match(v) for v in (o['source_language'], o['target_language'])):
        raise Fault('INVALID_LANGUAGE')
    if not isinstance(o['document_context'], str) or len(o['document_context']) > 8000:
        raise Fault('INVALID_CONTEXT')
    values = o['protected_values']
    if not isinstance(values, list) or len(values) > 200 or any(not isinstance(v, str) or len(v) > 500 for v in values):
        raise Fault('INVALID_PROTECTED_VALUES')
    cats = o['pii_categories']
    if not isinstance(cats, list) or any(c not in CATEGORIES for c in cats) or (o['operation'] != 'translation' and not cats):
        raise Fault('INVALID_PII_CATEGORIES')
    if 'pii_style' in o and o['pii_style'] not in ('box', 'label', 'pseudonym'):
        raise Fault('INVALID_PII_STYLE')
    keep = o.get('pii_keep')
    if keep is not None and (not isinstance(keep, list) or len(keep) > 100
                             or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in keep)):
        raise Fault('INVALID_PII_KEEP')
    model = o.get('translation_model')
    if model is not None and (not isinstance(model, str) or not MODEL_ID.match(model)):
        raise Fault('INVALID_TRANSLATION_MODEL')
    if o['operation'] == 'pii' or model == 'default':
        o.pop('translation_model', None)
    pii_model = o.get('pii_model')
    if pii_model is not None and (not isinstance(pii_model, str) or not MODEL_ID.match(pii_model)):
        raise Fault('INVALID_PII_MODEL')
    if o['operation'] == 'translation' or pii_model == 'default':
        o.pop('pii_model', None)
    return o


def glossary_entries(raw):
    """Same limits as the engine's terminology check: 100 entries of at most 200 characters."""
    if not isinstance(raw, list) or len(raw) > 100:
        raise Fault('INVALID_GLOSSARY')
    seen, out = set(), []
    for e in raw:
        if not isinstance(e, dict):
            raise Fault('INVALID_GLOSSARY')
        s = e.get('source').strip() if isinstance(e.get('source'), str) else ''
        t = e.get('target').strip() if isinstance(e.get('target'), str) else ''
        if (not s or not t or len(s) > 200 or len(t) > 200 or re.search(r'[\r\n\t]', s + t)
                or not any(ch.isalnum() for ch in s)):
            raise Fault('INVALID_GLOSSARY')
        if s.lower() in seen:
            raise Fault('DUPLICATE_GLOSSARY_TERM')
        seen.add(s.lower())
        out.append({'source': s, 'target': t})
    return out


def result_name(filename, operation):
    stem = Path(filename).stem
    suffix = {'translation': 'translated', 'pii': 'redacted', 'combined': 'translated-redacted',
              'manual_redaction': 'redacted'}[operation]
    return f'{stem}-{suffix}.pdf'


def redaction_regions(value, allow_empty=True):
    """Boxes drawn in the review editor, as page fractions (cloud/src/manual-redaction.mjs regions())."""
    if not isinstance(value, list) or len(value) > 500 or (not allow_empty and not value):
        raise Fault('INVALID_REDACTION_REGIONS')
    out = []
    for r in value:
        if not isinstance(r, dict) or sorted(r) != ['height', 'page', 'width', 'x', 'y']:
            raise Fault('INVALID_REDACTION_REGIONS')
        if type(r['page']) is not int or not 1 <= r['page'] <= MAX_PAGES:
            raise Fault('INVALID_REDACTION_REGIONS')
        box = [r[k] for k in ('x', 'y', 'width', 'height')]
        if not all(type(v) in (int, float) for v in box):
            raise Fault('INVALID_REDACTION_REGIONS')
        x, y, w, h = (float(v) for v in box)
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.0000001 or y + h > 1.0000001:
            raise Fault('INVALID_REDACTION_REGIONS')
        out.append({'page': r['page'], 'x': x, 'y': y, 'width': w, 'height': h})
    return out


class Store:
    """Jobs on disk: <root>/<job id>/job.json beside the job's files. One lock for all changes."""

    PUBLIC = ('id', 'filename', 'status', 'stage', 'quality', 'pages', 'created', 'updated', 'baseline',
              'options', 'metrics', 'source_sha256', 'assigned_worker_id', 'artifacts', 'error_code')

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        for path in self.root.glob('*/job.json'):
            try:
                job = json.loads(path.read_text(encoding='utf8'))
            except (OSError, ValueError):
                continue
            if job.get('status') == 'running':
                # The server stopped while this job ran; it is not replayed automatically.
                job.update(status='interrupted', stage='interrupted', error_code='WORKER_HEARTBEAT_LOST')
                self._write(job)
            self.jobs[job['id']] = job
        self.glossary_file = self.root / 'glossaries.json'

    def _write(self, job):
        folder = self.root / job['id']
        folder.mkdir(exist_ok=True)
        tmp = folder / 'job.json.tmp'
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf8')
        tmp.replace(folder / 'job.json')

    def save(self, job):
        with self.lock:
            job['updated'] = time.time()
            self.jobs[job['id']] = job
            self._write(job)

    def public(self, job):
        return {k: job.get(k) for k in self.PUBLIC}

    def listing(self, limit=100):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j['created'], reverse=True)[:limit]
            return [self.public(j) for j in jobs]

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id) if JOB_ID.match(job_id or '') else None
            if not job:
                raise Fault('NOT_FOUND', 404)
            return job

    def by_key(self, key):
        with self.lock:
            return next((j for j in self.jobs.values() if j.get('idempotency_key') == key), None)

    def next_queued(self):
        with self.lock:
            queued = [j for j in self.jobs.values() if j['status'] == 'queued']
            return min(queued, key=lambda j: j['created']) if queued else None

    def claim_next(self):
        """The oldest queued job, marked running in the same step, so two workers never take one job."""
        with self.lock:
            job = self.next_queued()
            if job:
                job.update(status='running', stage='starting', error_code=None)
                self.save(job)
            return job

    def event(self, job, stage, seconds):
        with self.lock:
            events = job.setdefault('events', [])
            if events and events[-1]['stage'] == stage:
                return
            events.append({'id': len(events) + 1, 'stage': stage, 'elapsed_seconds': round(float(seconds), 1),
                           'created': time.time()})
            job['stage'] = stage
            self.save(job)

    def glossaries(self):
        with self.lock:
            try:
                return json.loads(self.glossary_file.read_text(encoding='utf8'))
            except (OSError, ValueError):
                return {}

    def save_glossary(self, source, target, entries):
        with self.lock:
            data = self.glossaries()
            data[f'{source}>{target}'] = {'entries': entries, 'updated': time.time()}
            tmp = self.glossary_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
            tmp.replace(self.glossary_file)
            return data[f'{source}>{target}']


class Engine:
    """Runs queued jobs one at a time and keeps the server's status (models, GPU, disk) current."""

    def __init__(self, store, cfg, fake=False):
        self.store, self.cfg, self.fake = store, cfg, fake
        self.status = {'ready': False, 'operations': [], 'gpu': '', 'note': 'Checking the models…',
                       'translation_models': [], 'pii_models': []}
        self.wake = threading.Event()
        self.ocr_session = None
        self.stopping = False

    def refresh_status(self):
        if self.fake:
            state = {'ready': True, 'operations': ['translation', 'pii', 'combined', 'manual_redaction'],
                     'gpu': 'Test engine (no GPU)',
                     'note': 'Test mode: jobs return a copy of the input.',
                     'translation_models': [{'id': 'default', 'label': 'TranslateGemma 12B', 'external': False}],
                     'pii_models': [{'id': 'default', 'label': 'Qwen 3.5 9B', 'external': False}]}
        else:
            state = runner.preflight(self.cfg)
            if runner.low_disk(self.store.root):
                state.update(ready=False, operations=[], note='Not ready: the job disk is almost full.')
        self.status = state
        return state

    def worker_view(self):
        s = self.status
        return {'id': SERVER_ID, 'name': s.get('gpu') or 'This server', 'enabled': True, 'online': True,
                'ready': bool(s.get('ready')),
                'capabilities': {'gpu': s.get('gpu', ''), 'operations': s.get('operations', []),
                                 'note': s.get('note', ''), 'translation_models': s.get('translation_models', []),
                                 'pii_models': s.get('pii_models', []),
                                 'limits': {'max_pages': MAX_PAGES, 'max_mb': MAX_BYTES // 1_000_000}}}

    def maintain(self):
        """Every STATUS_SECONDS: keep the OCR and layout services loaded, delete old job folders and
        check the models, GPU and disk."""
        while not self.stopping:
            try:
                if not self.fake:
                    runner.keep_ocr_warm(self.ocr_session)
                    from transformation.pdf.layout_service import ping
                    ping()
                    runner.clean_old_jobs(self.store.root)
                self.refresh_status()
            except Exception:
                traceback.print_exc()
            time.sleep(STATUS_SECONDS)

    def loop(self):
        """One job worker: runs queued jobs one after another. TRANSFORMX_SERVICE_WORKERS workers run
        side by side; the model servers take their requests together."""
        while not self.stopping:
            job = None if (not self.fake and runner.low_disk(self.store.root)) else self.store.claim_next()
            if not job:
                self.wake.wait(2)
                self.wake.clear()
                continue
            self.run(job)

    def run(self, job):
        if job['status'] != 'running':
            job.update(status='running', stage='starting', error_code=None)
            self.store.save(job)
        print('Starting job', job['id'], flush=True)
        try:
            if job['options']['operation'] == 'manual_redaction':
                output, metrics = self.manual_job(job)
            else:
                output, metrics = (self.fake_job if self.fake else self.engine_job)(job)
            data = output.read_bytes()
            result = self.store.root / job['id'] / 'result.pdf'
            if output.resolve() != result.resolve():
                shutil.copyfile(output, result)
            job['artifacts'].append({'id': str(uuid.uuid4()), 'kind': 'result',
                                     'filename': result_name(job['filename'], job['options']['operation']),
                                     'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
            job.update(status='succeeded', stage='complete', metrics=metrics, pages=metrics.get('pages', job['pages']))
            print('Finished job', job['id'], flush=True)
        except Exception as exc:
            with (self.store.root / 'errors.log').open('a', encoding='utf8') as f:
                f.write(f'--- {job["id"]} {time.ctime()}\n')
                traceback.print_exc(file=f)
            code = exc.code if isinstance(exc, runner.JobFailure) else 'ENGINE_FAILED'
            job.update(status='failed', stage='failed', error_code=code)
            print('Job failed (' + code + '); see', self.store.root / 'errors.log', flush=True)
        self.store.save(job)

    def engine_job(self, job):
        """cloud_worker.execute() without the hosted transfer: the source is already in the job folder."""
        folder = self.store.root / job['id']
        source = folder / 'source.pdf'
        request, events, terminal = folder / 'request.json', folder / 'engine-events.jsonl', folder / 'terminal.json'
        if self.ocr_session:
            self.ocr_session.ensure()
            os.environ['TRANSFORMX_OCR_SERVICE_PID'] = str(self.ocr_session.process.pid)
        profile, job_env = runner.job_translation(self.cfg, job['options'])
        profile = dict(profile, timeout_seconds=runner.job_limit(profile, job['pages']))
        request.write_text(json.dumps({'source': str(source), 'filename': job['filename'],
                                       'options': job['options'], 'profile': profile}), encoding='utf8')
        started = time.monotonic()
        progress = runner.progress_reporter(events, lambda stage, seconds: self.store.event(job, stage, seconds))
        limit = profile['timeout_seconds'] + runner.DEADLINE_GRACE_SECONDS
        with (folder / 'process.log').open('w', encoding='utf8') as log:
            process = runner.spawn([runner.engine_python(self.cfg), '-u', '-m', 'backend.engine_job', str(request),
                                    str(events), str(terminal)], cwd=PROJECT, stdout=log, stderr=runner.subprocess.STDOUT,
                                   env=job_env)
            runner.supervise(process, limit, progress, interval=runner.STAGE_CHECK_SECONDS)
        try:
            result = json.loads(terminal.read_text(encoding='utf8')) if terminal.exists() else {}
        except ValueError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        if process.returncode != 0 or result.get('status') != 'generated':
            raise runner.JobFailure(runner.failure_code(result), 'Engine did not generate an output.')
        output = Path(result.get('output_file') or result.get('output_pdf') or '').resolve()
        if not output.is_relative_to(folder.resolve()) or not output.is_file():
            raise runner.JobFailure('OUTPUT_INVALID', 'Output outside this job directory.')
        if result.get('source_sha256') != job['source_sha256']:
            raise runner.JobFailure('OUTPUT_INVALID', 'Engine source hash mismatch.')
        import pymupdf
        try:
            with pymupdf.open(output) as pdf:
                if pdf.needs_pass or not len(pdf):
                    raise ValueError('Invalid generated PDF.')
                pages = len(pdf)
        except Exception as exc:
            raise runner.JobFailure('OUTPUT_INVALID', 'Invalid generated PDF.') from exc
        checks = runner.summarize_checks(result)
        metrics = {'source_sha256': job['source_sha256'], 'total_seconds': time.monotonic() - started, 'pages': pages,
                   'gpu_verified': result.get('gpu_verified') is True, 'checks': checks,
                   'blockers': sum(v for v in checks.values() if type(v) is int)}
        if isinstance(result.get('seconds'), (int, float)):
            metrics['server_seconds'] = result['seconds']
        metrics['review_items'] = runner.review_items(result.get('review_items'), pages)
        metrics['redaction_map'] = runner.redaction_map(result.get('redaction_map'), pages)
        return output, metrics

    def manual_job(self, job):
        """Boxes drawn in the review editor, removed from the earlier result and verified
        (cloud_worker.execute's manual_redaction branch)."""
        folder = self.store.root / job['id']
        source, output = folder / 'source.pdf', folder / 'manually-redacted.pdf'
        plan, audit_path = folder / 'manual-regions.json', folder / 'manual-redaction-audit.json'
        plan.write_text(json.dumps(job['options']['regions']), encoding='utf8')
        started = time.monotonic()
        self.store.event(job, 'applying_manual_redactions', 0)
        python = runner.engine_python(self.cfg) if self.cfg.get('engine_profile') else sys.executable
        with (folder / 'process.log').open('w', encoding='utf8') as log:
            process = runner.spawn([python, '-m', 'transformation.validation.manual_redaction', str(source), str(output),
                                    str(plan), job['source_sha256'], str(audit_path)], cwd=PROJECT, stdout=log,
                                   stderr=runner.subprocess.STDOUT)
            runner.supervise(process, runner.MANUAL_LIMIT_SECONDS,
                             lambda elapsed: self.store.event(job, 'applying_manual_redactions', elapsed))
        if process.returncode != 0 or not audit_path.is_file():
            raise runner.JobFailure('MANUAL_REDACTION_FAILED', 'Manual redaction verification failed.')
        audit = json.loads(audit_path.read_text(encoding='utf8'))
        if (audit['source_sha256'] != job['source_sha256']
                or audit['output_sha256'] != hashlib.sha256(output.read_bytes()).hexdigest()):
            raise runner.JobFailure('MANUAL_REDACTION_FAILED', 'Manual redaction hash mismatch.')
        return output, {'source_sha256': job['source_sha256'], 'pages': audit['pages'],
                        'server_seconds': time.monotonic() - started, 'gpu_verified': False,
                        'manual_redaction_verified': True, 'manual_regions': audit['regions']}

    def fake_job(self, job):
        """Test mode (--fake-engine): walks through the stages and returns the source, no models needed."""
        started = time.monotonic()
        for stage in ('analysis', 'extraction', 'translation_and_reconstruction', 'validation'):
            self.store.event(job, stage, time.monotonic() - started)
            time.sleep(float(os.environ.get('TRANSFORMX_FAKE_STAGE_SECONDS', '1')))
        if job['filename'].lower().startswith('fail'):
            raise runner.JobFailure('ENGINE_FAILED', 'Test failure requested by the file name.')
        source = self.store.root / job['id'] / 'source.pdf'
        checks = {'acceptable': True, 'privacy_verification': 'not_run', 'known_sensitive_residuals': 0,
                  'geometry_blockers': 0, 'layout_review_candidates': 0, 'uninspected_content': 0,
                  'scan_acceptance_blockers': 0, 'translation_errors': 0}
        return source, {'source_sha256': job['source_sha256'], 'total_seconds': time.monotonic() - started,
                        'pages': job['pages'], 'gpu_verified': False, 'checks': checks, 'blockers': 0,
                        'review_items': [], 'redaction_map': []}


def create_app(store, engine, site_dir, password=''):
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

    app = FastAPI(title='TransformX', docs_url=None, redoc_url=None, openapi_url=None)
    site = Path(site_dir).resolve()

    @app.exception_handler(Fault)
    async def fault(_request, exc):
        return JSONResponse({'error': exc.code}, status_code=exc.status, headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def guard(request, call_next):
        if password:
            header = request.headers.get('authorization', '')
            given = ''
            if header.startswith('Basic '):
                try:
                    given = base64.b64decode(header[6:]).decode('utf8').partition(':')[2]
                except ValueError:
                    given = ''
            if not hmac.compare_digest(given.encode(), password.encode()):
                return Response('Password required.', status_code=401,
                                headers={'WWW-Authenticate': 'Basic realm="TransformX", charset="UTF-8"'})
        response = await call_next(request)
        if request.url.path.startswith('/v1/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    account = {'name': 'This server', 'role': 'admin', 'csrf': 'local'}

    @app.get('/v1/me')
    def me():
        return account

    @app.post('/v1/session')
    def sign_in():
        return account

    @app.delete('/v1/session')
    def sign_out():
        return {}

    @app.get('/v1/workers')
    def workers():
        return [engine.worker_view()]

    @app.get('/v1/system')
    def system():
        return {'database': 'Files on this server', 'worker': 'connected', 'gpu_verified': False,
                'software_version': 'local-v1', 'status': engine.status}

    @app.get('/v1/jobs')
    def jobs():
        return store.listing()

    @app.post('/v1/jobs', status_code=202)
    async def submit(request: Request):
        key = request.headers.get('idempotency-key', '')
        if not KEY.match(key):
            raise Fault('IDEMPOTENCY_KEY_REQUIRED')
        prior = store.by_key(key)
        if prior:
            return JSONResponse({'job_id': prior['id'], 'status_url': '/v1/jobs/' + prior['id']}, status_code=202)
        form = await request.form()
        upload = form.get('file')
        if upload is None or isinstance(upload, str):
            raise Fault('INVALID_PDF')
        name = upload.filename or ''
        if len(name) > 180 or re.search(r'[\\/\x00-\x1f]', name) or not name.lower().endswith('.pdf'):
            raise Fault('INVALID_PDF')
        data = await upload.read(MAX_BYTES + 1)
        await upload.close()
        if len(data) > MAX_BYTES:
            raise Fault('REQUEST_TOO_LARGE', 413)
        if len(data) < 5 or data[:5] != b'%PDF-':
            raise Fault('INVALID_PDF')
        try:
            options = check_options(json.loads(form.get('options') or '{}'))
        except ValueError:
            raise Fault('INVALID_OPTIONS')
        offered = {m['id'] for m in engine.status.get('translation_models', [])}
        if options.get('translation_model') and options['translation_model'] not in offered:
            raise Fault('TRANSLATION_MODEL_NOT_OFFERED')
        if options.get('pii_model') and options['pii_model'] not in {m['id'] for m in engine.status.get('pii_models', [])}:
            raise Fault('PII_MODEL_NOT_OFFERED')
        try:
            import pymupdf
            with pymupdf.open(stream=data, filetype='pdf') as pdf:
                if pdf.needs_pass:
                    raise Fault('INVALID_PDF')
                pages = len(pdf)
        except Fault:
            raise
        except Exception:
            raise Fault('INVALID_PDF')
        if not 1 <= pages <= MAX_PAGES:
            raise Fault('INVALID_PDF')
        if options['operation'] != 'pii':
            entry = store.glossaries().get(f"{options['source_language']}>{options['target_language']}")
            if entry and entry['entries']:
                options['terminology'] = {e['source']: e['target'] for e in entry['entries']}
        job_id = str(uuid.uuid4())
        folder = store.root / job_id
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        now = time.time()
        job = {'id': job_id, 'filename': name, 'status': 'queued', 'stage': 'queued', 'quality': 'requires_review',
               'pages': pages, 'created': now, 'updated': now, 'baseline': False, 'options': options, 'metrics': {},
               'source_sha256': digest, 'assigned_worker_id': SERVER_ID, 'error_code': None,
               'idempotency_key': key, 'events': [],
               'artifacts': [{'id': str(uuid.uuid4()), 'kind': 'source', 'filename': name, 'sha256': digest,
                              'size': len(data)}]}
        store.save(job)
        engine.wake.set()
        return {'job_id': job_id, 'status_url': '/v1/jobs/' + job_id}

    @app.get('/v1/jobs/{job_id}')
    def job(job_id: str):
        return store.public(store.get(job_id))

    @app.get('/v1/jobs/{job_id}/events')
    def events(job_id: str):
        return store.get(job_id).get('events', [])

    @app.post('/v1/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        with store.lock:
            j = store.get(job_id)
            if j['status'] != 'queued':
                raise Fault('ONLY_QUEUED_CANCELLATION_SUPPORTED', 409)
            j.update(status='cancelled', stage='cancelled')
            store.save(j)
        return {'status': 'cancelled'}

    @app.get('/v1/jobs/{job_id}/report')
    def report(job_id: str):
        return JSONResponse(store.public(store.get(job_id)),
                            headers={'Content-Disposition': 'attachment; filename="run-report.json"'})

    def artifact_file(job_id, artifact_id):
        j = store.get(job_id)
        a = next((a for a in j['artifacts'] if a['id'] == artifact_id), None)
        if not a:
            raise Fault('NOT_FOUND', 404)
        path = store.root / j['id'] / ('source.pdf' if a['kind'] == 'source' else 'result.pdf')
        if not path.is_file():
            raise Fault('NOT_FOUND', 404)
        return a, path

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}')
    def download(job_id: str, artifact_id: str):
        a, path = artifact_file(job_id, artifact_id)
        return FileResponse(path, media_type='application/pdf', filename=a['filename'])

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}/content')
    def content(job_id: str, artifact_id: str):
        _, path = artifact_file(job_id, artifact_id)
        return FileResponse(path, media_type='application/pdf', content_disposition_type='inline')

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}/url')
    def url(job_id: str, artifact_id: str):
        artifact_file(job_id, artifact_id)
        return {'url': f'/v1/jobs/{job_id}/artifacts/{artifact_id}/content'}

    # The review editor (redaction-review.html): a draft of boxes over a finished result, saved as it
    # is edited and then run as a manual_redaction job (cloud/src/manual-redaction.mjs).
    def describe(d):
        o = d['options']
        return {'job_id': d['id'], 'status': d['status'], 'revision': d['revision'], 'regions': o['regions'],
                'parent_job_id': o['parent_job_id'], 'parent_artifact_id': o['parent_artifact_id'],
                'original_job_id': o['original_job_id'], 'source_sha256': d['source_sha256'],
                'filename': d['filename']}

    def draft_job(job_id):
        d = store.get(job_id)
        if d['options'].get('operation') != 'manual_redaction':
            raise Fault('NOT_FOUND', 404)
        return d

    async def json_body(request):
        try:
            b = await request.json()
        except ValueError:
            raise Fault('INVALID_JSON')
        if not isinstance(b, dict):
            raise Fault('INVALID_JSON')
        return b

    @app.post('/v1/jobs/{job_id}/redactions', status_code=201)
    async def start_draft(job_id: str, request: Request):
        key = request.headers.get('idempotency-key', '')
        if not KEY.match(key):
            raise Fault('IDEMPOTENCY_KEY_REQUIRED')
        b = await json_body(request)
        with store.lock:
            j = store.get(job_id)
            if j['status'] != 'succeeded':
                raise Fault('RESULT_NOT_READY', 409)
            a = next((a for a in j['artifacts'] if a['kind'] == 'result'), None)
            if not a or b.get('artifact_id') != a['id'] or b.get('source_sha256') != a['sha256']:
                raise Fault('PDF_VERSION_CHANGED', 409)
            d = store.by_key(key)
            if not d:
                now = time.time()
                d = {'id': str(uuid.uuid4()), 'filename': re.sub(r'\.pdf$', '', j['filename'], flags=re.I) + '-manual.pdf',
                     'status': 'uploading', 'stage': 'manual_review', 'quality': 'requires_review',
                     'pages': j.get('pages', 0), 'created': now, 'updated': now, 'baseline': False, 'metrics': {},
                     'source_sha256': a['sha256'], 'assigned_worker_id': SERVER_ID, 'error_code': None,
                     'idempotency_key': key, 'revision': str(uuid.uuid4()), 'events': [], 'artifacts': [],
                     'options': {'operation': 'manual_redaction', 'parent_job_id': j['id'], 'parent_artifact_id': a['id'],
                                 'original_job_id': j['options'].get('original_job_id') or j['id'], 'regions': []}}
                store.save(d)
            if d['options'].get('parent_job_id') != j['id'] or d['source_sha256'] != a['sha256']:
                raise Fault('IDEMPOTENCY_CONFLICT', 409)
        return JSONResponse({'job_id': d['id']}, status_code=201)

    @app.get('/v1/jobs/{job_id}/redaction-draft')
    def get_draft(job_id: str):
        return describe(draft_job(job_id))

    @app.put('/v1/jobs/{job_id}/redaction-draft')
    async def save_draft(job_id: str, request: Request):
        b = await json_body(request)
        with store.lock:
            d = draft_job(job_id)
            if d['status'] != 'uploading':
                raise Fault('DRAFT_ALREADY_FINALIZED', 409)
            if b.get('revision') != d['revision']:
                raise Fault('DRAFT_CONFLICT', 409)
            d['options'] = {**d['options'], 'regions': redaction_regions(b.get('regions'))}
            d['revision'] = str(uuid.uuid4())
            store.save(d)
            return {'revision': d['revision']}

    @app.post('/v1/jobs/{job_id}/redaction-finalize')
    async def finalize_draft(job_id: str, request: Request):
        b = await json_body(request)
        with store.lock:
            d = draft_job(job_id)
            if d['status'] != 'uploading':
                return {'job_id': d['id'], 'status': d['status']}
            if b.get('revision') != d['revision']:
                raise Fault('DRAFT_CONFLICT', 409)
            redaction_regions(d['options']['regions'], allow_empty=False)
            if 'manual_redaction' not in engine.status.get('operations', []) and not engine.fake:
                raise Fault('MANUAL_WORKER_REQUIRED', 409)
            parent = store.get(d['options']['parent_job_id'])
            a = next((a for a in parent['artifacts'] if a['id'] == d['options']['parent_artifact_id']), None)
            if parent['status'] != 'succeeded' or not a or a['sha256'] != d['source_sha256']:
                raise Fault('PDF_VERSION_CHANGED', 409)
            shutil.copyfile(store.root / parent['id'] / 'result.pdf', store.root / d['id'] / 'source.pdf')
            d['artifacts'] = [{'id': str(uuid.uuid4()), 'kind': 'source', 'filename': d['filename'],
                               'sha256': a['sha256'], 'size': a['size']}]
            d.update(status='queued', stage='queued')
            store.save(d)
        engine.wake.set()
        return JSONResponse({'job_id': d['id'], 'status': 'queued'}, status_code=202)

    @app.get('/v1/glossaries')
    def glossaries():
        out = []
        for pair, g in sorted(store.glossaries().items()):
            source, _, target = pair.partition('>')
            out.append({'source_language': source, 'target_language': target, 'terms': len(g['entries']),
                        'updated': g['updated']})
        return out

    @app.get('/v1/glossary')
    def glossary(source: str = 'en', target: str = ''):
        if not LANGUAGE.match(source) or not LANGUAGE.match(target):
            raise Fault('INVALID_LANGUAGE')
        g = store.glossaries().get(f'{source}>{target}')
        return {'source_language': source, 'target_language': target,
                'entries': g['entries'] if g else [], 'updated': g['updated'] if g else None}

    @app.put('/v1/glossary')
    async def save_glossary(request: Request):
        try:
            b = await request.json()
        except ValueError:
            raise Fault('INVALID_JSON')
        source, target = b.get('source_language', ''), b.get('target_language', '')
        if not LANGUAGE.match(source or '') or not LANGUAGE.match(target or '') or source == target:
            raise Fault('INVALID_LANGUAGE')
        g = store.save_glossary(source, target, glossary_entries(b.get('entries')))
        return {'source_language': source, 'target_language': target, **g}

    @app.api_route('/v1/{rest:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
    def unknown(rest: str):
        raise Fault('ENDPOINT_NOT_IMPLEMENTED', 404)

    # The website: Next's static export (frontend/out): the product page at /, the workspace at /workspace.
    pages = {'/': 'index.html', '/index.html': 'index.html', '/workspace': 'workspace.html',
             '/workspace/': 'workspace.html', '/workspace.html': 'workspace.html'}

    @app.get('/studio')
    def studio():
        return RedirectResponse('/workspace')

    @app.get('/{path:path}')
    def static(path: str):
        route = '/' + path
        if route in pages:
            return FileResponse(site / pages[route], media_type='text/html')
        target = (site / path).resolve()
        if not target.is_relative_to(site):
            raise Fault('NOT_FOUND', 404)
        if target.is_dir():
            target = target / 'index.html'
        if not target.is_file() and (site / (path + '.html')).is_file():
            target = site / (path + '.html')
        if not target.is_file():
            return Response('Not found.', status_code=404)
        return FileResponse(target)

    return app


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default=os.environ.get('TRANSFORMX_HOST', '127.0.0.1'),
                   help='Address to listen on (default 127.0.0.1: reach it through an SSH tunnel).')
    p.add_argument('--port', type=int, default=int(os.environ.get('TRANSFORMX_PORT', '8080')))
    p.add_argument('--root', default=os.environ.get('TRANSFORMX_JOBS', str(PROJECT / 'runs' / 'local')),
                   help='Folder for jobs, results and glossaries.')
    p.add_argument('--site', default=os.environ.get('TRANSFORMX_SITE', str(PROJECT / 'frontend' / 'out')),
                   help='The built website (npm --prefix frontend run build).')
    p.add_argument('--fake-engine', action='store_true',
                   help='Test the website without models or a GPU: jobs return a copy of the input.')
    args = p.parse_args()
    if not (Path(args.site) / 'workspace.html').is_file():
        sys.exit(f'Website not built: {args.site}/workspace.html is missing (npm --prefix frontend run build).')
    password = os.environ.get('TRANSFORMX_PASSWORD', '')
    if args.host not in ('127.0.0.1', 'localhost', '::1') and not password:
        print('Warning: listening on', args.host, 'without TRANSFORMX_PASSWORD; anyone who can reach this '
              'address can use the server.', flush=True)
    store = Store(args.root)
    cfg = {'engine_profile': {}}
    if not args.fake_engine:
        from transformation import settings
        settings.apply()
        profile = default_profile()
        settings.apply(profile)
        profile = runner.apply_profile_overrides(profile)
        for key in ('python', 'ocr_python'):
            profile.setdefault(key, sys.executable)
        runner.run_limit(profile)
        cfg = {'engine_profile': profile}
    engine = Engine(store, cfg, fake=args.fake_engine)
    app = create_app(store, engine, args.site, password)
    import uvicorn
    with contextlib.ExitStack() as stack:
        if not args.fake_engine:
            profile = cfg['engine_profile']
            enabled = profile.get('persistent_ocr') is True and os.name == 'posix' and profile.get('ocr_device') == 'gpu:0'
            from transformation.ocr.service import managed_session
            from transformation.pdf.layout_service import optional_shared_layout
            engine.ocr_session = stack.enter_context(managed_session(profile['ocr_python'], store.root, enabled))
            layout = stack.enter_context(optional_shared_layout(profile.get('python'), store.root / 'layout-service'))
            # Table models loaded once for all jobs, in the engine's Python (extraction/docling_service.py).
            from extraction.docling_service import optional_shared_docling
            tables = stack.enter_context(optional_shared_docling(
                os.environ.get('TRANSFORMX_ENGINE_PYTHON') or profile.get('engine_python'), store.root / 'docling-service'))
            if tables:
                print(f"Shared table models ready ({tables['load_seconds']} s).", flush=True)
            if engine.ocr_session:
                from transformation.ocr.service import prewarm
                prewarm(store.root / 'prewarm')
            if layout:
                print('Shared layout model ready.', flush=True)
        engine.refresh_status()
        print('Server ready for: ' + (', '.join(engine.status['operations']) or 'none; ' + engine.status['note']),
              flush=True)
        threading.Thread(target=engine.maintain, name='status', daemon=True).start()
        workers = max(1, int(os.environ.get('TRANSFORMX_SERVICE_WORKERS', '1')))
        for index in range(workers):
            threading.Thread(target=engine.loop, name=f'jobs-{index + 1}', daemon=True).start()
        print(f'{workers} job worker(s).', flush=True)
        print(f'TransformX website: http://{args.host}:{args.port}/', flush=True)
        uvicorn.run(app, host=args.host, port=args.port, log_level='warning')
        engine.stopping = True


if __name__ == '__main__':
    main()
