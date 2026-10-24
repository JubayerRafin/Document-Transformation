"""Isolate the existing PP-OCRv5 detector; no translation or package changes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def worker(image, output, max_side=None, device="cpu", model_factory=None):
    # Match the settings used by run_paddleocr.py.
    os.environ.setdefault('FLAGS_use_mkldnn', 'false')
    os.environ.setdefault('FLAGS_enable_pir_api', '0')
    from importlib.metadata import version
    from paddleocr import TextDetection
    cache = Path.home() / '.paddlex/official_models/PP-OCRv5_server_det'
    if not cache.is_dir():
        raise RuntimeError(f'Expected existing detector cache not found: {cache}')
    print(json.dumps({p: version(p) for p in ('paddleocr', 'paddlepaddle-gpu' if device.startswith('gpu') else 'paddlepaddle', 'paddlex') }), flush=True)
    print(f'Loading PP-OCRv5_server_det on {device}', flush=True)
    resize = {'limit_side_len': max_side, 'limit_type': 'max'} if max_side else {}
    print(f'Resize override: {resize or "model defaults"}; CPU threads: 10', flush=True)
    model = (model_factory or TextDetection)(model_name='PP-OCRv5_server_det', model_dir=str(cache),
                          device=device, enable_mkldnn=False, cpu_threads=10, **resize)
    print('Detector loaded; starting image inference', flush=True)
    results = model.predict(str(image))
    for result in results:
        result.save_to_json(save_path=str(output))
        result.save_to_img(save_path=str(output))
    print(f'Detector completed: {len(results)} result(s)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('C:/Users/Jubayer/Desktop/TransformX/document-extraction-lab-source/PDF_version/Sample01.pdf'))
    parser.add_argument('--max-side', type=int, choices=[512, 768, 960, 1536, 3072], help='Detector resize limit')
    parser.add_argument('--timeout', type=int, default=180, help='Maximum diagnostic runtime in seconds (30 to 900)')
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--device", choices=["cpu", "gpu:0"], default="cpu")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 900:
        parser.error('--timeout must be between 30 and 900 seconds')
    if args.worker:
        worker(args.worker, args.output, args.max_side, args.device)
        return
    import pymupdf
    folder = Path('runs/paddleocr-sample01') / ('detector-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6])
    folder.mkdir(parents=True)
    with pymupdf.open(args.source) as pdf:
        # 72 dpi preserves this sample's 1000 x 1400 displayed-page dimensions.
        pix = pdf[0].get_pixmap(dpi=72, colorspace=pymupdf.csRGB, alpha=False)
        image = folder / 'page-1.png'
        pix.save(image)
        size = [pix.width, pix.height]
    print(f'Diagnostic folder: {folder.resolve()}', flush=True)
    print(f'Testing only the text detector. Maximum wait: {args.timeout} seconds.', flush=True)
    started = time.monotonic()
    with (folder / 'detector.log').open('w', encoding='utf-8') as log:
        try:
            command = [sys.executable, '-X', 'faulthandler', '-u', str(Path(__file__).resolve()),
                       '--worker', str(image.resolve()), '--output', str(folder.resolve()), '--device', args.device]
            if args.max_side:
                command.extend(['--max-side', str(args.max_side)])
            result = subprocess.run(command,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout,
                                    env=dict(os.environ, PYTHONUTF8='1'))
            code = result.returncode
            status = 'completed' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            code, status = None, 'timed_out'
    report = {'status': status, 'exit_code': code,
              'windows_exit_hex': f'0x{code & 0xffffffff:08X}' if code is not None else None,
              'seconds': round(time.monotonic()-started,1), 'image_pixels': size, 'device': args.device,
              'detector_max_side': args.max_side, 'cpu_threads': 10,
              'timeout_seconds': args.timeout,
              'source': str(args.source.resolve()),
              'scope': 'Detector-only diagnostic at 72 dpi, not a full extraction or accuracy evaluation'}
    (folder / 'diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
    print(f'Log saved: {folder / "detector.log"}', flush=True)


if __name__ == '__main__':
    main()
