"""Run the PII evaluation set through a TransformX website and lay the results out for score.py.

usage: python -m evaluation.pii.run_site <set_dir> <label> <url> [--combined] <ssh-target> [ssh options...]
  e.g. python -m evaluation.pii.run_site output/pii-eval-v1 site-a http://127.0.0.1:8180 root@host -p 22 -i key

--combined redacts and translates each document (into English, English documents into Korean); the
translation segments can then be scored with evaluation/suite/translation.py and comet_score.py.

Each document is submitted as "PII only" in its own language, with the categories of the answer
key. When all jobs are done, every job's output is downloaded and its pii-findings.json and
privacy-verification.json files are copied from the server's job folder (one tar over ssh), so
score.py reads them as it reads a run.py run. Measure only: never tune detection on this set.
"""
import json
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from evaluation.robustness.run_site import call, submit


def main(set_dir, label, url, ssh, operation='pii'):
    set_dir = Path(set_dir)
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8'))
    runs = set_dir / 'runs' / label
    jobs = {}
    # The PDF set keeps its files in pdfs/; the Word and PowerPoint set (evaluation/office) names them in the key.
    files = ([set_dir / spec['file'] for name, spec in sorted(key.items()) if not name.startswith('_')]
             if not (set_dir / 'pdfs').is_dir() else sorted((set_dir / 'pdfs').glob('*.pdf')))
    for pdf in files:
        language = key.get(pdf.stem, {}).get('language', 'en')
        target = language if operation == 'pii' else ('ko' if language == 'en' else 'en')
        jobs[pdf.stem] = (submit(url, pdf, language, target, operation), time.monotonic(), language, target)
    print(len(jobs), 'jobs queued', flush=True)
    finished = {}
    for name, (job_id, started, language, target) in jobs.items():
        while True:
            job = json.loads(call(f'{url}/v1/jobs/{job_id}'))
            if job['status'] in ('succeeded', 'failed', 'interrupted', 'cancelled'):
                break
            time.sleep(3)
        folder = runs / name
        folder.mkdir(parents=True, exist_ok=True)
        output = None
        if job['status'] == 'succeeded':
            result = next(a for a in job['artifacts'] if a['kind'] == 'result')
            output = folder / ('output' + (Path(result['filename']).suffix.lower() or '.pdf'))
            output.write_bytes(call(f"{url}/v1/jobs/{job_id}/artifacts/{result['id']}"))
            if output.suffix != '.pdf':
                (folder / 'output-preview.pdf').write_bytes(call(f"{url}/v1/jobs/{job_id}/artifacts/{result['id']}/content"))
        finished[name] = (job_id, job, output, language, target)
        print(label, name, job['status'], job.get('error_code') or '', flush=True)
    # The findings files, from the server's job folders.
    archive = runs / 'findings.tgz'
    ids = ' '.join(row[0] for row in finished.values())
    command = (f'cd /workspace/transformx-app/runs/local && find {ids} \\( -name pii-findings.json -o '
               f'-name "privacy-verification*.json" \\) -print0 | tar --null -czf - -T -')
    with open(archive, 'wb') as out:
        subprocess.run(['ssh', *ssh, command], stdout=out, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(runs / 'server', filter='data')
    for name, (job_id, job, output, language, target) in finished.items():
        engine = next((runs / 'server' / job_id / 'engine').glob('*'), None)
        summary = {'label': label, 'doc': name, 'job': job_id, 'operation': operation, 'source': language, 'target': target,
                   'kind': key.get(name, {}).get('variant'),
                   'checks': (job.get('metrics') or {}).get('checks'),
                   'status': 'generated' if job['status'] == 'succeeded' else job['status'],
                   'failure_code': job.get('error_code'), 'error': job.get('error_code'),
                   'server_seconds': (job.get('metrics') or {}).get('server_seconds'),
                   'run_dir': str(engine.resolve()) if engine else None,
                   'output_file': str(output.resolve()) if output else None}
        (runs / name / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    combined = len(sys.argv) > 4 and sys.argv[4] == '--combined'
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[5:] if combined else sys.argv[4:], 'combined' if combined else 'pii')
