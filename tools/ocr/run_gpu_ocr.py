"""Run the same PaddleOCR components on a Linux GPU host; save portable evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import time

import pymupdf
try:
    from .raster_policy import render_page
except ImportError:
    from raster_policy import render_page


def _run_stage(stage, arguments, out, limit, model_pool):
    with (out/f'{stage}.log').open('w') as log:
        if model_pool is None:
            subprocess.run([sys.executable, '-u', *arguments], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            return
        from contextlib import redirect_stdout,redirect_stderr
        with redirect_stdout(log),redirect_stderr(log):
            if stage=='detection':
                from tools.ocr.diagnose_paddle_detector import worker
                worker(out/'page-1.png',out,limit,'gpu:0',model_factory=model_pool.detection)
            else:
                from tools.ocr.recognize_paddle_boxes import main as recognize
                recognize(arguments[1:],model_factory=model_pool.recognition)


def _out_of_memory(exc, log):
    """Paddle reports GPU memory exhaustion as MemoryError (also inside a subprocess's log)."""
    text=f'{type(exc).__name__} {exc}'
    try:text+=log.read_text(errors='replace')[-4000:]
    except OSError:pass
    return 'MemoryError' in text or 'out of memory' in text.lower() or 'ResourceExhausted' in text


def _free_gpu_memory():
    try:
        import paddle
        paddle.device.cuda.empty_cache()
    except Exception:
        pass
    import gc
    gc.collect()


def run(source, output, source_language='unknown', model_pool=None):
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = Path(source).resolve()
    with pymupdf.open(source) as pdf:
        if len(pdf) != 1 or pdf.needs_pass or pdf[0].rotation or pdf[0].get_text().strip():
            raise ValueError('Expected one unrotated image-only PDF page.')
        pix,raster = render_page(pdf[0])
        pix.save(out/'page-1.png')
        size = [pix.width, pix.height]
    repo = Path(__file__).resolve().parent
    report = {'status':'processing', 'device':'gpu:0', 'raster':raster,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    environment=Path('/workspace/transformx-environment.json')
    if environment.is_file(): report['environment']=json.loads(environment.read_text())
    started = time.monotonic()
    try:
        detector_limit=1536 if max(size)<=1536 else 3072
        for stage in ('detection', 'recognition'):
            tick = time.monotonic()
            # GPU memory is shared with the model servers and other OCR runs. Running out of it is
            # retried after freeing cached memory; detection then tries a smaller detector size
            # (a large A3 scan failed the whole document with MemoryError, 2026-11-19).
            limits=[detector_limit,detector_limit]+([1536] if stage=='detection' and detector_limit>1536 else [])
            for attempt,limit in enumerate(limits):
                arguments=(['diagnose_paddle_detector.py', '--worker', str(out/'page-1.png'), '--output', str(out), '--max-side', str(limit), '--device', 'gpu:0']
                           if stage=='detection' else
                           ['recognize_paddle_boxes.py', '--detection', str(out), '--device', 'gpu:0', '--source-language', source_language])
                arguments[0] = str(repo/arguments[0])
                try:
                    _run_stage(stage, arguments, out, limit, model_pool)
                    if stage=='detection':detector_limit=limit
                    break
                except Exception as exc:
                    if attempt+1==len(limits) or not _out_of_memory(exc, out/f'{stage}.log'):raise
                    report.setdefault('memory_retries',[]).append({'stage':stage,'detector_max_side':limit})
                    _free_gpu_memory()
                    time.sleep(3)
            report[stage+'_seconds'] = round(time.monotonic()-tick, 2)
            if stage == 'detection':
                (out/'diagnostic.json').write_text(json.dumps({'status':'completed', 'device':'gpu:0', 'source':str(source), 'image_pixels':size, 'detector_max_side':detector_limit,'raster':raster}))
            print(f'{stage} completed in {report[stage+"_seconds"]}s', flush=True)
        report['status'] = 'completed'
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        # Surface the nested detector/recognizer error in worker.log so the
        # laptop does not report only a CalledProcessError without its cause.
        for diagnostic in ('detection.log','recognition.log'):
            path=out/diagnostic
            if path.exists():
                print('\n'+diagnostic+' last lines:\n'+'\n'.join(path.read_text(errors='replace').splitlines()[-35:]),flush=True)
        raise
    finally:
        report['total_seconds'] = round(time.monotonic()-started, 2)
        if model_pool is not None:report['model_reuse']=model_pool.metrics()
        (out/'gpu-run.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        if report['status'] == 'completed':
            with zipfile.ZipFile(out.with_suffix('.zip'), 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ['diagnostic.json','gpu-run.json','page-1.png','page-1_res.json','recognition/manifest.json','recognition/lines.jsonl']:
                    archive.write(out/name, name)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-language', default='unknown')
    args = parser.parse_args()
    run(args.source,args.output,args.source_language)


if __name__ == '__main__':
    main()
