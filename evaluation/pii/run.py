"""Run every PDF of an evaluation set in PII-only mode on a GPU server over SSH.

The working tree's engine code is bundled and executed on the server (no local processing).
usage: python -m evaluation.pii.run <set_dir> <label> --host H --port P [--key ~/.ssh/id] [name ...]
Results: <set_dir>/runs/<label>/<doc>/summary.json
"""
import argparse, json, time
from pathlib import Path

from transformation.workflows import run_workflow

CATEGORIES = ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('set_dir'); ap.add_argument('label'); ap.add_argument('names', nargs='*')
    ap.add_argument('--host', required=True); ap.add_argument('--port', type=int, required=True)
    ap.add_argument('--key', default='~/.ssh/id_ed25519'); ap.add_argument('--skip-done', action='store_true')
    args = ap.parse_args()
    root_set = Path(args.set_dir).resolve(); remote = {'host': args.host, 'port': args.port, 'key': args.key}
    for path in sorted((root_set / 'pdfs').glob('*.pdf')):
        name = path.stem
        if args.names and name not in args.names: continue
        root = root_set / 'runs' / args.label / name; root.mkdir(parents=True, exist_ok=True)
        if args.skip_done and (root / 'summary.json').exists(): continue
        started = time.monotonic(); last = {}; stages = []
        try:
            for event in run_workflow(path.read_bytes(), path.name, 'PII only', execution_remote=remote, root=root,
                                      pii_categories=CATEGORIES, source='en', target='ko', timeout=3600, ocr_cache_mode='fresh'):
                if event.get('stage') != last.get('stage'):
                    stages.append([event.get('stage'), event.get('server_processing_seconds', event.get('seconds'))])
                last = event
        except Exception as exc:  # recorded per document so one failure does not stop the set
            last = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'[:500]}
        summary = {'label': args.label, 'doc': name, 'status': last.get('status'), 'error': last.get('error'),
                   'failure_code': last.get('failure_code'), 'wall_seconds': round(time.monotonic() - started, 1),
                   'server_seconds': last.get('seconds'), 'stages': stages, 'run_dir': last.get('run_dir'),
                   'output_file': last.get('output_file'), 'warnings': last.get('warnings', [])}
        (root / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
        print(args.label, name, summary['status'], summary['server_seconds'], 's', summary.get('failure_code') or '', flush=True)


if __name__ == '__main__':
    main()
