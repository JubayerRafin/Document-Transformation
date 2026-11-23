"""Run the PII evaluation set through a TransformX website and lay the results out for score.py.

usage: python -m evaluation.pii.run_site <set_dir> <label> <url> <ssh-target> [ssh options...]
  e.g. python -m evaluation.pii.run_site output/pii-eval-v1 site-a http://127.0.0.1:8180 root@host -p 22 -i key

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


def main(set_dir, label, url, ssh):
    set_dir = Path(set_dir)
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8'))
    runs = set_dir / 'runs' / label
    jobs = {}
    for pdf in sorted((set_dir / 'pdfs').glob('*.pdf')):
        language = key.get(pdf.stem, {}).get('language', 'en')
        jobs[pdf.stem] = (submit(url, pdf, language, language, 'pii'), time.monotonic())
    print(len(jobs), 'jobs queued', flush=True)
    finished = {}
    for name, (job_id, started) in jobs.items():
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
            output = folder / 'output.pdf'
            output.write_bytes(call(f"{url}/v1/jobs/{job_id}/artifacts/{result['id']}"))
        finished[name] = (job_id, job, output)
        print(label, name, job['status'], job.get('error_code') or '', flush=True)
    # The findings files, from the server's job folders.
    archive = runs / 'findings.tgz'
    ids = ' '.join(job_id for job_id, _, _ in finished.values())
    command = (f'cd /workspace/transformx-app/runs/local && find {ids} \\( -name pii-findings.json -o '
               f'-name "privacy-verification*.json" \\) -print0 | tar --null -czf - -T -')
    with open(archive, 'wb') as out:
        subprocess.run(['ssh', *ssh, command], stdout=out, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(runs / 'server', filter='data')
    for name, (job_id, job, output) in finished.items():
        engine = next((runs / 'server' / job_id / 'engine').glob('*'), None)
        summary = {'label': label, 'doc': name, 'status': 'generated' if job['status'] == 'succeeded' else job['status'],
                   'failure_code': job.get('error_code'), 'error': job.get('error_code'),
                   'server_seconds': (job.get('metrics') or {}).get('server_seconds'),
                   'run_dir': str(engine.resolve()) if engine else None,
                   'output_file': str(output.resolve()) if output else None}
        (runs / name / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
