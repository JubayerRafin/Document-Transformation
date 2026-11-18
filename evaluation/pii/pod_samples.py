"""Run PDFs on the GPU server exactly as a website job runs: a warm OCR session, the site worker's
own engine profile, and the job options the upload form sends. Run ON the pod from the app folder:

  /workspace/paddle-env/bin/python -m evaluation.pii.pod_samples <pdf_dir> <out_dir> [--style box] [--set KEY=VALUE]
      [--operation pii|translation|combined] [--source en] [--target ko]

Writes <out_dir>/<name>/{summary.json, findings/*.json, redacted.pdf, privacy-verification*.json}
(output.pdf instead of redacted.pdf for translation and combined jobs)
so only small files need to come back over a slow link.
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
CATEGORIES = ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf_dir'); ap.add_argument('out_dir'); ap.add_argument('--style', default='box')
    ap.add_argument('--config', default=str(APP / 'configs/gpu.worker.local.json'))
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE',
                    help='override an engine-profile value, e.g. pii_endpoint=http://127.0.0.1:8000/v1 or engine_options.pii_workers=16')
    ap.add_argument('--operation', default='pii', choices=['pii', 'translation', 'combined'])
    ap.add_argument('--source', default='en'); ap.add_argument('--target', default='ko')
    ap.add_argument('--terminology', help='JSON file of {term: required translation}, sent like a customer glossary')
    args = ap.parse_args()
    output_name = 'redacted.pdf' if args.operation == 'pii' else 'output.pdf'
    profile = json.loads(Path(args.config).read_text())['engine_profile']
    for item in args.set:
        key, _, value = item.partition('=')
        if not key or not _: ap.error('--set needs KEY=VALUE')
        try: value = json.loads(value)          # numbers, booleans, objects
        except ValueError: pass                 # anything else is a string
        *parents, leaf = key.split('.')         # engine_options.pii_workers=16
        target = profile
        for part in parents: target = target.setdefault(part, {})
        target[leaf] = value
    for key in ('python', 'ocr_python'): profile.setdefault(key, sys.executable)
    out = Path(args.out_dir).resolve(); out.mkdir(parents=True, exist_ok=True)
    from transformation.ocr.service import managed_session, prewarm
    # The OCR service only reads files inside its root, so the root is the folder holding every job.
    from transformation.pdf.layout_service import optional_shared_layout
    # Like the site worker: one OCR session and one shared layout model for all documents.
    with managed_session(profile['ocr_python'], out, bool(profile.get('persistent_ocr'))) as session,             optional_shared_layout(profile.get('python'), out / '_layout'):
        if session: prewarm(out / '_prewarm')
        for pdf in sorted(Path(args.pdf_dir).glob('*.pdf')):
            folder = out / pdf.stem / 'job'
            if folder.exists(): shutil.rmtree(folder)
            folder.mkdir(parents=True)
            source = folder / 'source.pdf'; source.write_bytes(pdf.read_bytes())
            options = {'operation': args.operation, 'source_language': args.source, 'target_language': args.target, 'output_format': 'pdf',
                       'protected_values': [], 'document_context': '', 'pii_categories': CATEGORIES, 'pii_style': args.style}
            if args.terminology: options['terminology'] = json.loads(Path(args.terminology).read_text(encoding='utf-8'))
            request = folder / 'request.json'
            request.write_text(json.dumps({'source': str(source), 'filename': pdf.name, 'options': options, 'profile': profile}))
            env = dict(os.environ)
            if session: session.ensure(); env['TRANSFORMX_OCR_SERVICE_PID'] = str(session.process.pid)
            started = time.monotonic()
            with (folder / 'process.log').open('w') as log:
                # Started like the site worker starts it (pre-loaded when TRANSFORMX_PRELOAD=1).
                from transformation.preload import popen
                popen([profile.get('engine_python', sys.executable), '-u', '-m', 'backend.engine_job', str(request),
                       str(folder / 'events.jsonl'), str(folder / 'terminal.json')], APP, log, env).wait()
            seconds = time.monotonic() - started
            terminal = json.loads((folder / 'terminal.json').read_text()) if (folder / 'terminal.json').exists() else {}
            keep = out / pdf.stem
            (keep / 'findings').mkdir(exist_ok=True)
            run = Path(terminal.get('run_dir') or folder)
            for i, f in enumerate(sorted(run.rglob('pii-findings.json'))):
                shutil.copy(f, keep / 'findings' / f'{i:02d}-{f.parent.name}.json')
            for name in ('privacy-verification.json', 'privacy-verification-after-masking.json', 'review-items.json', 'redaction-map.json'):
                if (run / name).exists(): shutil.copy(run / name, keep / name)
            if terminal.get('status') == 'generated' and terminal.get('output_file'): shutil.copy(terminal['output_file'], keep / output_name)
            stages, last = [], None
            for line in (folder / 'events.jsonl').read_text().splitlines() if (folder / 'events.jsonl').exists() else []:
                event = json.loads(line)
                if event['stage'] != last: stages.append([event['stage'], event['elapsed_seconds']]); last = event['stage']
            summary = {'doc': pdf.stem, 'status': terminal.get('status'), 'failure_code': terminal.get('failure_code'),
                       'error': (terminal.get('error') or '')[:300], 'wall_seconds': round(seconds, 1),
                       'server_seconds': terminal.get('seconds'), 'stages': stages, 'warnings': terminal.get('warnings', []),
                       'final_check_masked': terminal.get('final_check_masked', 0),
                       # Same fields as evaluation.pii.run, so evaluation.pii.score can read this folder too.
                       'run_dir': str(run), 'output_file': str(keep / output_name) if terminal.get('status') == 'generated' else None}
            (keep / 'summary.json').write_text(json.dumps(summary, indent=1))
            print(pdf.stem, summary['status'], summary['failure_code'] or '', round(seconds, 1), 's', flush=True)


if __name__ == '__main__':
    main()
