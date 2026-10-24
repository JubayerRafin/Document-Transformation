"""Compare three original crops in fresh PaddleOCR processes against saved results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def worker(crop, output):
    os.environ.setdefault('FLAGS_use_mkldnn', 'false')
    os.environ.setdefault('FLAGS_enable_pir_api', '0')
    from importlib.metadata import version
    from paddleocr import TextRecognition
    model_name = 'PP-OCRv5_server_rec'
    cache = Path.home() / '.paddlex/official_models' / model_name
    if not cache.is_dir():
        raise ValueError(f'Existing model cache not found: {cache}')
    started = time.monotonic()
    model = TextRecognition(model_name=model_name, model_dir=str(cache), device='cpu',
                            enable_mkldnn=False, cpu_threads=10)
    loaded = time.monotonic()
    print('Model loaded; recognizing one original crop', flush=True)
    results = model.predict(str(crop))
    if len(results) != 1:
        raise ValueError('Expected exactly one recognition result.')
    result = {'text': str(results[0]['rec_text']), 'score': float(results[0]['rec_score']),
              'load_seconds': round(loaded-started, 2),
              'inference_seconds': round(time.monotonic()-loaded, 2),
              'versions': {p: version(p) for p in ('paddleocr', 'paddlepaddle', 'paddlex')},
              'flags': {k: os.environ[k] for k in ('FLAGS_use_mkldnn', 'FLAGS_enable_pir_api')}}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detection', type=Path, required=True)
    parser.add_argument('--crop', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.crop:
        worker(args.crop, args.result)
        return
    root = args.detection.resolve()
    previous = {r['id']: r for r in (json.loads(line) for line in
                (root/'recognition/lines.jsonl').read_text(encoding='utf-8').splitlines())}
    cases = [(49, 'document ID'), (33, 'security email'), (1, 'footer')]
    for number, _ in cases:
        if not (root/'recognition-crops'/f'line-{number:03}.png').is_file():
            raise ValueError(f'Original crop {number} is missing.')
        if f'p1_line{number}' not in previous:
            raise ValueError(f'Original recognition result {number} is missing.')
    folder = root / ('isolated-recognition-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
    folder.mkdir()
    summary = {'model': 'PP-OCRv5_server_rec', 'device': 'cpu', 'cpu_threads': 10,
               'timeout_per_crop_seconds': 180, 'results': [],
               'limitation': 'One fresh run per crop, compared with an earlier sequential run. Differences do not prove a runtime defect; no accuracy score is inferred.'}
    print(f'Diagnostics: {folder}', flush=True)
    for index, (number, label) in enumerate(cases, 1):
        crop = root/'recognition-crops'/f'line-{number:03}.png'
        result_path = folder/f'line-{number:03}.json'
        print(f'{index}/3: {label}; fresh process, up to 180 seconds.', flush=True)
        row = {'id': f'p1_line{number}', 'label': label,
               'crop_sha256': hashlib.sha256(crop.read_bytes()).hexdigest(),
               'previous_text': previous[f'p1_line{number}']['text']}
        with (folder/f'line-{number:03}.log').open('w', encoding='utf-8') as log:
            try:
                process = subprocess.run([sys.executable, '-X', 'faulthandler', '-u', str(Path(__file__).resolve()),
                                          '--detection', str(root), '--crop', str(crop), '--result', str(result_path)],
                                         stdout=log, stderr=subprocess.STDOUT, timeout=180,
                                         env=dict(os.environ, PYTHONUTF8='1'))
                row.update(status='completed' if process.returncode == 0 else 'failed', exit_code=process.returncode)
                if process.returncode == 0:
                    row['fresh_result'] = json.loads(result_path.read_text(encoding='utf-8'))
                    row['same_text_as_previous'] = row['fresh_result']['text'] == row['previous_text']
            except subprocess.TimeoutExpired:
                row.update(status='timed_out')
        summary['results'].append(row)
        (folder/'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{label}: {row['status']}", flush=True)
    print(f'Finished. Saved comparison: {folder / "comparison.json"}', flush=True)


if __name__ == '__main__':
    main()
