"""Run a corpus through a TransformX website (tools/service/local_server.py) and keep the results.

    python -m evaluation.robustness.run_site CORPUS RESULTS --plan to-en|from-en|same [--url URL]
        [--targets ko,ja,...] [--only NAME,...]

Plans (corpus made by make_fullpage.py, manifest.json beside it):
  to-en    every non-English document into English
  from-en  every English document into each of --targets (default: the ten other languages)

All jobs are queued at once (the server runs them in order), then collected. Per job RESULTS/<plan>/
<target>/<doc>/ holds summary.json (status, failure code, wall and server seconds, pages, stage
times) and output.pdf, the layout evaluation/robustness/check_outputs.py reads. The corpus folder is
only read. RESULTS/<plan>/timing.json sums seconds per page per target.
"""
import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path

LANGS = ['en', 'ko', 'ja', 'zh', 'ar', 'hi', 'bn', 'fr', 'de', 'es', 'pt']


def call(url, data=None, headers=None, method=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


MEDIA = {'.pdf': 'application/pdf',
         '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def submit(base, pdf, source, target, operation='translation'):
    """pdf: a PDF, Word or PowerPoint file (the result comes back in the same format)."""
    options = {'operation': operation, 'source_language': source, 'target_language': target,
               'output_format': pdf.suffix.lower().lstrip('.'),
               'protected_values': [], 'document_context': '', 'worker_id': 'local',
               'pii_categories': [] if operation == 'translation' else ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts', 'Dates of birth']}
    boundary = '----tx' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{pdf.name}"\r\n'
            f'Content-Type: {MEDIA[pdf.suffix.lower()]}\r\n\r\n').encode() + pdf.read_bytes() + \
        (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="options"\r\n\r\n{json.dumps(options)}\r\n'
         f'--{boundary}--\r\n').encode()
    answer = call(base + '/v1/jobs', body, {'Content-Type': f'multipart/form-data; boundary={boundary}',
                                            'Idempotency-Key': uuid.uuid4().hex}, 'POST')
    return json.loads(answer)['job_id']


def plan_jobs(manifest, plan, targets, only):
    jobs = []
    for doc in manifest['documents']:
        stem = Path(doc['file']).stem
        if only and stem not in only:
            continue
        if plan == 'to-en' and doc['lang'] != 'en':
            jobs.append((doc, doc['lang'], 'en'))
        elif plan == 'from-en' and doc['lang'] == 'en':
            jobs.extend((doc, 'en', target) for target in targets)
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('corpus')
    parser.add_argument('results')
    parser.add_argument('--plan', choices=['to-en', 'from-en'], required=True)
    parser.add_argument('--url', default='http://localhost:8181')
    parser.add_argument('--targets', default=','.join(l for l in LANGS if l != 'en'))
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    corpus = Path(args.corpus)
    manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
    jobs = plan_jobs(manifest, args.plan, args.targets.split(','), set(filter(None, args.only.split(','))))
    root = Path(args.results) / args.plan
    queued = []
    for doc, source, target in jobs:
        job_id = submit(args.url, corpus / doc['file'], source, target)
        queued.append((doc, source, target, job_id, time.time()))
    print(f'{len(queued)} jobs queued', flush=True)
    timing = {}
    for doc, source, target, job_id, submitted in queued:
        while True:
            job = json.loads(call(f'{args.url}/v1/jobs/{job_id}'))
            if job['status'] in ('succeeded', 'failed', 'interrupted', 'cancelled'):
                break
            time.sleep(3)
        events = json.loads(call(f'{args.url}/v1/jobs/{job_id}/events'))
        stem = Path(doc['file']).stem
        folder = root / target / stem
        folder.mkdir(parents=True, exist_ok=True)
        metrics = job.get('metrics') or {}
        summary = {'doc': stem, 'job': job_id, 'source': source, 'target': target, 'kind': doc['kind'],
                   'status': 'generated' if job['status'] == 'succeeded' else job['status'],
                   'failure_code': job.get('error_code'), 'pages': job.get('pages') or doc.get('pages') or 1,
                   'wall_seconds': metrics.get('total_seconds'), 'server_seconds': metrics.get('server_seconds'),
                   'checks': metrics.get('checks'), 'stages': [(e['stage'], e['elapsed_seconds']) for e in events]}
        if job['status'] == 'succeeded':
            result = next(a for a in job['artifacts'] if a['kind'] == 'result')
            suffix = Path(result['filename']).suffix.lower() or '.pdf'
            (folder / ('output' + suffix)).write_bytes(call(f"{args.url}/v1/jobs/{job_id}/artifacts/{result['id']}"))
            if suffix != '.pdf':
                # What the website shows: the engine's PDF drawing of the Word or PowerPoint result.
                (folder / 'output.pdf').write_bytes(call(f"{args.url}/v1/jobs/{job_id}/artifacts/{result['id']}/content"))
                source_file = next(a for a in job['artifacts'] if a['kind'] == 'source')
                (folder / 'source-preview.pdf').write_bytes(
                    call(f"{args.url}/v1/jobs/{job_id}/artifacts/{source_file['id']}/content"))
        (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
        seconds = summary['wall_seconds'] or 0
        t = timing.setdefault(target, {'documents': 0, 'pages': 0, 'seconds': 0.0, 'failed': 0})
        t['documents'] += 1
        t['pages'] += summary['pages']
        t['seconds'] += seconds
        t['failed'] += summary['status'] != 'generated'
        print(f"{target} {stem}: {summary['status']} {summary['failure_code'] or ''} {seconds:.0f} s "
              f"({seconds / max(1, summary['pages']):.1f} s/page)", flush=True)
    for t in timing.values():
        t['seconds_per_page'] = round(t['seconds'] / max(1, t['pages']), 1)
    (root / 'timing.json').write_text(json.dumps(timing, indent=1), encoding='utf-8')
    print(json.dumps(timing, indent=1))


if __name__ == '__main__':
    main()
