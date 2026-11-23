"""The self-hosted website's API (tools/service/local_server.py), with its test engine: no models or GPU."""
import json
import os
import time
import unittest
import uuid
from pathlib import Path

from tests.temp_workspace import temporary_workspace

try:
    from fastapi.testclient import TestClient
except ImportError:  # the website server is optional in the engine environments
    TestClient = None


def pdf_bytes(pages=1, text='Hello Alex Morgan'):
    import pymupdf
    with pymupdf.open() as doc:
        for _ in range(pages):
            doc.new_page().insert_text((72, 72), text)
        return doc.tobytes()


def minimal_docx(text):
    """The smallest Word package LibreOffice opens: content types, relationships, one paragraph."""
    import io
    import zipfile
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml', f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
                   '</w:body></w:document>')
    return out.getvalue()


@unittest.skipIf(TestClient is None, 'fastapi is not installed')
class LocalServerTests(unittest.TestCase):
    def setUp(self):
        os.environ['TRANSFORMX_FAKE_STAGE_SECONDS'] = '0'
        from tools.service import local_server as server
        self.server = server
        self.tmp = temporary_workspace()
        root = Path(self.tmp.__enter__())
        site = root / 'site'
        site.mkdir()
        (site / 'index.html').write_text('<h1>product</h1>')
        (site / 'workspace.html').write_text('<h1>workspace</h1>')
        self.store = server.Store(root / 'jobs')
        self.engine = server.Engine(self.store, {'engine_profile': {}}, fake=True)
        self.engine.refresh_status()
        self.client = TestClient(server.create_app(self.store, self.engine, site))

    def tearDown(self):
        self.tmp.__exit__(None, None, None)

    def submit(self, options=None, name='doc.pdf', data=None, key=None):
        opts = {'operation': 'translation', 'source_language': 'en', 'target_language': 'ko', 'output_format': 'pdf',
                'protected_values': [], 'document_context': '', 'pii_categories': [], 'worker_id': 'local', **(options or {})}
        return self.client.post('/v1/jobs', files={'file': (name, data or pdf_bytes(), 'application/pdf')},
                                data={'options': json.dumps(opts)},
                                headers={'Idempotency-Key': key or uuid.uuid4().hex})

    def run_queue(self):
        job = self.store.next_queued()
        while job:
            self.engine.run(job)
            job = self.store.next_queued()

    def test_no_sign_in_and_one_ready_server(self):
        self.assertEqual(self.client.get('/v1/me').json()['csrf'], 'local')
        workers = self.client.get('/v1/workers').json()
        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0]['ready'])
        self.assertIn('pii', workers[0]['capabilities']['operations'])

    def test_job_runs_and_its_files_download(self):
        r = self.submit()
        self.assertEqual(r.status_code, 202)
        job_id = r.json()['job_id']
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}').json()['status'], 'queued')
        self.run_queue()
        job = self.client.get(f'/v1/jobs/{job_id}').json()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual([a['kind'] for a in job['artifacts']], ['source', 'result'])
        self.assertEqual(job['artifacts'][1]['filename'], 'doc-translated.pdf')
        stages = [e['stage'] for e in self.client.get(f'/v1/jobs/{job_id}/events').json()]
        self.assertEqual(stages[0], 'analysis')
        result = job['artifacts'][1]
        download = self.client.get(f"/v1/jobs/{job_id}/artifacts/{result['id']}")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b'%PDF-'))
        url = self.client.get(f"/v1/jobs/{job_id}/artifacts/{result['id']}/url").json()['url']
        self.assertEqual(self.client.get(url).headers['content-type'], 'application/pdf')
        self.assertEqual(self.client.get('/v1/jobs').json()[0]['id'], job_id)

    def test_word_and_powerpoint_jobs_keep_their_format(self):
        import io
        import shutil
        if not shutil.which('soffice'):
            self.skipTest('LibreOffice draws the preview of Word and PowerPoint uploads')
        r = self.submit(name='letter.docx', data=minimal_docx('Hello Alex Morgan'))
        self.assertEqual(r.status_code, 202, r.text)
        job_id = r.json()['job_id']
        self.assertEqual(self.store.get(job_id)['options']['output_format'], 'docx')
        self.assertGreaterEqual(self.store.get(job_id)['pages'], 1)
        self.run_queue()
        job = self.client.get(f'/v1/jobs/{job_id}').json()
        self.assertEqual(job['status'], 'succeeded')
        source, result = job['artifacts']
        self.assertEqual(result['filename'], 'letter-translated.docx')
        download = self.client.get(f"/v1/jobs/{job_id}/artifacts/{result['id']}")
        self.assertTrue(download.content.startswith(b'PK'))
        self.assertIn('wordprocessingml', download.headers['content-type'])
        for artifact in (source, result):   # the website shows a PDF preview of each
            url = self.client.get(f"/v1/jobs/{job_id}/artifacts/{artifact['id']}/url").json()['url']
            view = self.client.get(url)
            self.assertEqual(view.headers['content-type'], 'application/pdf')
            self.assertTrue(view.content.startswith(b'%PDF-'))
        # Boxes are drawn on PDF pages only.
        draft = self.client.post(f'/v1/jobs/{job_id}/redactions', json={'artifact_id': result['id'], 'source_sha256': result['sha256']},
                                 headers={'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(draft.json()['error'], 'MANUAL_REDACTION_PDF_ONLY')

    def test_word_97_2003_upload_comes_back_as_docx(self):
        import shutil
        import subprocess
        import tempfile
        if not shutil.which('soffice'):
            self.skipTest('LibreOffice converts .doc files')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'letter.docx'
            source.write_bytes(minimal_docx('Hello Alex Morgan'))
            subprocess.run(['soffice', '-env:UserInstallation=' + (Path(folder) / 'profile').as_uri(), '--headless',
                            '--convert-to', 'doc:MS Word 97', '--outdir', folder, str(source)], capture_output=True, timeout=120)
            data = (Path(folder) / 'letter.doc').read_bytes()
        self.assertEqual(data[:4], bytes.fromhex("d0cf11e0"))
        r = self.submit(name='letter.doc', data=data)
        self.assertEqual(r.status_code, 202, r.text)
        job_id = r.json()['job_id']
        self.assertEqual(self.store.get(job_id)['options']['output_format'], 'docx')
        self.run_queue()
        job = self.client.get(f'/v1/jobs/{job_id}').json()
        self.assertEqual(job['status'], 'succeeded')
        source, result = job['artifacts']
        self.assertEqual((source['filename'], result['filename']), ('letter.doc', 'letter-translated.docx'))
        self.assertIn('msword', self.client.get(f"/v1/jobs/{job_id}/artifacts/{source['id']}").headers['content-type'])
        view = self.client.get(self.client.get(f"/v1/jobs/{job_id}/artifacts/{source['id']}/url").json()['url'])
        self.assertTrue(view.content.startswith(b'%PDF-'))
        self.assertEqual(self.submit(name='fake.doc', data=b'PK not a doc').json()['error'], 'INVALID_DOCUMENT')

    def test_pii_model_choice_is_offered_and_kept_only_for_pii_jobs(self):
        self.engine.status['pii_models'].append({'id': 'openai', 'label': 'GPT', 'external': True})
        workers = self.client.get('/v1/workers').json()
        self.assertEqual([m['id'] for m in workers[0]['capabilities']['pii_models']], ['default', 'openai'])
        pii = self.submit({'operation': 'pii', 'pii_categories': ['Names'], 'pii_model': 'openai'}).json()['job_id']
        translation = self.submit({'pii_model': 'openai'}).json()['job_id']
        default = self.submit({'operation': 'pii', 'pii_categories': ['Names'], 'pii_model': 'default'}).json()['job_id']
        self.assertEqual(self.store.get(pii)['options']['pii_model'], 'openai')
        self.assertNotIn('pii_model', self.store.get(translation)['options'])
        self.assertNotIn('pii_model', self.store.get(default)['options'])

    def test_parallel_workers_each_claim_a_different_job(self):
        import threading
        ids = {self.submit().json()['job_id'] for _ in range(6)}
        claimed, lock = [], threading.Lock()

        def worker():
            while True:
                job = self.store.claim_next()
                if not job:
                    return
                with lock:
                    claimed.append(job['id'])

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(sorted(claimed), sorted(ids))   # every job once, none twice
        self.assertTrue(all(self.store.get(i)['status'] == 'running' for i in ids))

    def test_same_idempotency_key_returns_the_same_job(self):
        first = self.submit(key='same-key-123').json()['job_id']
        second = self.submit(key='same-key-123').json()['job_id']
        self.assertEqual(first, second)
        self.assertEqual(len(self.client.get('/v1/jobs').json()), 1)

    def test_rejects_bad_input_with_the_websites_error_codes(self):
        self.assertEqual(self.submit(data=b'not a pdf').json()['error'], 'INVALID_PDF')
        self.assertEqual(self.submit({'operation': 'pii'}).json()['error'], 'INVALID_PII_CATEGORIES')
        self.assertEqual(self.submit({'pii_style': 'blur'}).json()['error'], 'INVALID_PII_STYLE')
        self.assertEqual(self.submit({'translation_model': 'missing'}).json()['error'], 'TRANSLATION_MODEL_NOT_OFFERED')
        self.assertEqual(self.submit(name='doc.txt').json()['error'], 'INVALID_DOCUMENT')
        self.assertEqual(self.submit(name='doc.docx', data=b'not a package').json()['error'], 'INVALID_DOCUMENT')
        pii = {'operation': 'pii', 'pii_categories': ['Names']}
        self.assertEqual(self.submit({**pii, 'pii_model': 'missing'}).json()['error'], 'PII_MODEL_NOT_OFFERED')
        self.assertEqual(self.submit({**pii, 'pii_model': 'Bad Name'}).json()['error'], 'INVALID_PII_MODEL')
        self.assertEqual(self.client.get('/v1/jobs/not-a-job').status_code, 404)

    def test_failed_job_reports_a_code_and_queued_jobs_can_be_cancelled(self):
        failed = self.submit(name='fail-me.pdf').json()['job_id']
        queued = self.submit().json()['job_id']
        self.assertEqual(self.client.post(f'/v1/jobs/{queued}/cancel').json()['status'], 'cancelled')
        self.run_queue()
        job = self.client.get(f'/v1/jobs/{failed}').json()
        self.assertEqual((job['status'], job['error_code']), ('failed', 'ENGINE_FAILED'))
        self.assertEqual(self.client.post(f'/v1/jobs/{failed}/cancel').status_code, 409)

    def test_glossary_is_saved_and_sent_with_translation_jobs(self):
        r = self.client.put('/v1/glossary', json={'source_language': 'en', 'target_language': 'ko',
                                                  'entries': [{'source': 'Northwind', 'target': '노스윈드'}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get('/v1/glossaries').json()[0]['terms'], 1)
        job_id = self.submit().json()['job_id']
        self.assertEqual(self.store.get(job_id)['options']['terminology'], {'Northwind': '노스윈드'})
        bad = self.client.put('/v1/glossary', json={'source_language': 'en', 'target_language': 'ko',
                                                    'entries': [{'source': 'a', 'target': 'b'}, {'source': 'A', 'target': 'c'}]})
        self.assertEqual(bad.json()['error'], 'DUPLICATE_GLOSSARY_TERM')

    def test_jobs_survive_a_restart_and_a_running_job_is_marked_interrupted(self):
        job_id = self.submit().json()['job_id']
        job = self.store.get(job_id)
        job['status'] = 'running'
        self.store.save(job)
        reopened = self.server.Store(self.store.root)
        self.assertEqual(reopened.get(job_id)['status'], 'interrupted')
        self.assertEqual(reopened.get(job_id)['error_code'], 'WORKER_HEARTBEAT_LOST')

    def test_review_editor_draft_runs_a_verified_manual_redaction(self):
        parent = self.submit().json()['job_id']
        self.run_queue()
        result = self.client.get(f'/v1/jobs/{parent}').json()['artifacts'][1]
        r = self.client.post(f'/v1/jobs/{parent}/redactions', headers={'Idempotency-Key': 'draft-key-1'},
                             json={'artifact_id': result['id'], 'source_sha256': result['sha256']})
        self.assertEqual(r.status_code, 201)
        draft = self.client.get(f"/v1/jobs/{r.json()['job_id']}/redaction-draft").json()
        box = [{'page': 1, 'x': 0.1, 'y': 0.05, 'width': 0.4, 'height': 0.05}]
        saved = self.client.put(f"/v1/jobs/{draft['job_id']}/redaction-draft",
                                json={'revision': draft['revision'], 'regions': box}).json()
        stale = self.client.put(f"/v1/jobs/{draft['job_id']}/redaction-draft",
                                json={'revision': draft['revision'], 'regions': []})
        self.assertEqual(stale.status_code, 409)
        done = self.client.post(f"/v1/jobs/{draft['job_id']}/redaction-finalize",
                                json={'revision': saved['revision'], 'worker_id': 'local'})
        self.assertEqual(done.json()['status'], 'queued')
        self.run_queue()
        job = self.client.get(f"/v1/jobs/{draft['job_id']}").json()
        self.assertEqual(job['status'], 'succeeded', job.get('error_code'))
        self.assertTrue(job['metrics']['manual_redaction_verified'])

    def finished_redaction(self, keep=None):
        options = {'operation': 'pii', 'pii_categories': ['Names', 'Email addresses']}
        if keep:
            options['pii_keep'] = keep
        job_id = self.submit(options).json()['job_id']
        self.run_queue()
        run = self.store.root / job_id / 'engine' / 'run-1'
        run.mkdir(parents=True)
        (run / 'pii-findings.json').write_text(json.dumps([
            {'region_id': 'pii-native-1-1', 'value': 'Alex Morgan', 'category': 'Names'},
            {'region_id': 'p2_r2:cell-1-1', 'value': 'alex.m@north\nwind.example', 'category': 'Email addresses'},
            {'region_id': 'pii-native-2-4', 'value': 'alex.m@north', 'category': 'Identifiers'},
            {'region_id': 'pii-native-3-1', 'value': 'Alex  Morgan', 'category': 'Names'}]), encoding='utf-8')
        picture = run / 'image-1'
        picture.mkdir()
        (picture / 'pii-findings.json').write_text(json.dumps([
            {'region_id': 'ocr-1', 'value': 'Northwind Ltd', 'category': 'Names'}]), encoding='utf-8')
        return job_id

    def test_removed_values_are_listed_and_a_new_version_keeps_the_chosen_ones(self):
        job_id = self.finished_redaction()
        listed = self.client.get(f'/v1/jobs/{job_id}/redacted-values').json()
        rows = {r['value']: r for r in listed['values']}
        # Spacing and case are ignored as the engine does; a piece of a wrapped value is listed with it.
        self.assertEqual(rows['Alex Morgan']['occurrences'], 2)
        self.assertEqual(rows['Alex Morgan']['pages'], [1, 3])
        self.assertEqual(rows['alex.m@north\nwind.example']['parts'], ['alex.m@north'])
        self.assertEqual(rows['alex.m@north\nwind.example']['pages'], [2])
        self.assertIn('Northwind Ltd', rows)   # found in a picture inside the document
        self.assertEqual(listed['kept'], [])
        # The values never enter the job record the website lists.
        self.assertNotIn('Alex Morgan', json.dumps(self.client.get(f'/v1/jobs/{job_id}').json()))
        r = self.client.post(f'/v1/jobs/{job_id}/keep-visible', json={'keep': ['Northwind Ltd', ' Alex Morgan ']},
                             headers={'Idempotency-Key': 'keep-key-1'})
        self.assertEqual(r.status_code, 202)
        new = self.client.get(f"/v1/jobs/{r.json()['job_id']}").json()
        self.assertEqual(new['parent_job_id'], job_id)
        self.assertEqual(new['options']['pii_keep'], ['Northwind Ltd', 'Alex Morgan'])
        self.assertEqual(new['options']['pii_categories'], ['Names', 'Email addresses'])
        self.assertEqual(new['source_sha256'], self.client.get(f'/v1/jobs/{job_id}').json()['source_sha256'])
        self.assertEqual((self.store.root / new['id'] / 'source.pdf').read_bytes(),
                         (self.store.root / job_id / 'source.pdf').read_bytes())
        again = self.client.post(f'/v1/jobs/{job_id}/keep-visible', json={'keep': ['Northwind Ltd']},
                                 headers={'Idempotency-Key': 'keep-key-1'})
        self.assertEqual(again.json()['job_id'], new['id'])
        self.run_queue()
        self.assertEqual(self.client.get(f"/v1/jobs/{new['id']}").json()['status'], 'succeeded')

    def test_a_kept_value_can_be_hidden_again_and_bad_requests_are_refused(self):
        job_id = self.finished_redaction(keep=['Northwind Ltd'])
        self.assertEqual(self.client.get(f'/v1/jobs/{job_id}/redacted-values').json()['kept'], ['Northwind Ltd'])
        same = self.client.post(f'/v1/jobs/{job_id}/keep-visible', json={'keep': ['Northwind Ltd']},
                                headers={'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(same.json()['error'], 'KEEP_LIST_UNCHANGED')
        hidden = self.client.post(f'/v1/jobs/{job_id}/keep-visible', json={'keep': []},
                                  headers={'Idempotency-Key': uuid.uuid4().hex})
        self.assertNotIn('pii_keep', self.client.get(f"/v1/jobs/{hidden.json()['job_id']}").json()['options'])
        for body in ({'keep': ['x' * 201]}, {'keep': [''] }, {'keep': 'Alex'}, {'keep': ['a'] * 101}):
            r = self.client.post(f'/v1/jobs/{job_id}/keep-visible', json=body, headers={'Idempotency-Key': uuid.uuid4().hex})
            self.assertEqual(r.json()['error'], 'INVALID_PII_KEEP', body)
        self.assertEqual(self.client.post(f'/v1/jobs/{job_id}/keep-visible', json={'keep': []}).json()['error'],
                         'IDEMPOTENCY_KEY_REQUIRED')
        translation = self.submit().json()['job_id']
        self.run_queue()
        self.assertEqual(self.client.get(f'/v1/jobs/{translation}/redacted-values').json()['error'], 'NOT_A_REDACTION_JOB')

    def test_website_pages_and_password(self):
        self.assertIn(b'product', self.client.get('/').content)
        self.assertIn(b'workspace', self.client.get('/workspace').content)
        self.assertEqual(self.client.get('/../secret').status_code, 404)
        guarded = TestClient(self.server.create_app(self.store, self.engine, self.client.app and Path(self.store.root).parent / 'site', 'pw'))
        self.assertEqual(guarded.get('/v1/me').status_code, 401)
        self.assertEqual(guarded.get('/v1/me', auth=('any', 'pw')).status_code, 200)
        self.assertEqual(guarded.get('/v1/me', auth=('any', 'wrong')).status_code, 401)


if __name__ == '__main__':
    unittest.main()
