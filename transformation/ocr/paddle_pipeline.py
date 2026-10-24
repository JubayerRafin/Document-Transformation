"""Run the existing isolated Paddle components with progress and cancellation."""
import json
import os
from pathlib import Path
import subprocess
import time

import pymupdf


def run_ocr(source, folder, python, timeout=1200, source_language='unknown'):
    if os.environ.get('TRANSFORMX_OCR_DEVICE')=='gpu:0':
        yield from run_server_gpu_ocr(source,folder,python,timeout,source_language)
        return
    source, folder = Path(source).resolve(), Path(folder).resolve()
    if not Path(python).is_file():
        raise ValueError('Select the Python executable in the PaddleOCR environment.')
    with pymupdf.open(source) as pdf:
        if len(pdf) != 1 or pdf.needs_pass or pdf[0].rotation or pdf[0].get_text().strip():
            raise ValueError('Fresh OCR currently accepts one unrotated, image-only PDF page.')
        if pdf[0].rect.width * pdf[0].rect.height > 20_000_000:
            raise ValueError('Scanned page exceeds the prototype raster size limit.')
        folder.mkdir(parents=True)
        from tools.ocr.raster_policy import render_page
        pix,raster = render_page(pdf[0])
        pix.save(folder / 'page-1.png')
        size = [pix.width, pix.height]
    repo = Path(__file__).resolve().parents[2]
    commands = [
        ('detecting', [str(python), '-u', str(repo/'tools/ocr/diagnose_paddle_detector.py'), '--worker', str(folder/'page-1.png'), '--output', str(folder), '--max-side', '1536' if max(size)<=1536 else '3072']),
        ('recognizing', [str(python), '-u', str(repo/'tools/ocr/recognize_paddle_boxes.py'), '--detection', str(folder), '--source-language', source_language]),
    ]
    started = time.monotonic()
    for stage, command in commands:
        process = None
        try:
            with (folder/f'{stage}.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                                           env=dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1'))
                while process.poll() is None:
                    if time.monotonic()-started > timeout:
                        raise TimeoutError('PaddleOCR exceeded its time budget. See the OCR stage logs.')
                    count = 0
                    evidence = folder/'recognition/lines.jsonl'
                    if evidence.exists():
                        count = len(evidence.read_text(encoding='utf-8').splitlines())
                    yield {'stage': stage, 'recognized_lines': count, 'device': 'cpu'}
                    time.sleep(1)
            if process.returncode:
                raise RuntimeError(f'PaddleOCR {stage} failed (exit {process.returncode}). See {folder / (stage+".log")}')
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if stage == 'detecting':
            (folder/'diagnostic.json').write_text(json.dumps({'status':'completed', 'image_pixels':size,
                'source':str(source), 'detector_max_side':1536 if max(size)<=1536 else 3072,'raster':raster,'device':'cpu'}), encoding='utf-8')
    yield {'stage':'completed', 'recognition_dir':str(folder/'recognition'), 'device':'cpu'}


def run_server_gpu_ocr(source,folder,python,timeout,source_language):
    """Reuse the GPU worker directly on its host, without an SSH round trip."""
    source,folder=Path(source).resolve(),Path(folder).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    repo=Path(__file__).resolve().parents[2]
    output=folder/'gpu-output'
    if os.environ.get('TRANSFORMX_OCR_SOCKET'):
        from transformation.ocr.service import request
        started=time.monotonic();result=None
        for progress in request(source,output,source_language,timeout):
            if progress['stage']=='service_completed':result=progress
            else:yield progress
        from transformation.ocr.remote_ocr import unpack
        report=unpack(output.with_suffix('.zip'),folder,source)
        (folder/'ocr-timing.json').write_text(json.dumps({'total_seconds':time.monotonic()-started,
            'persistent':True,'service_metrics':result['service_metrics'],
            'detection_seconds':report['detection_seconds'],
            'recognition_seconds':report['recognition_seconds']},indent=2))
        yield {'stage':'completed','recognition_dir':str(folder/'recognition'),'device':'gpu:0',
            'gpu_seconds':report['total_seconds'],'execution_host':'server','persistent':True}
        return
    command=[str(python),'-u',str(repo/'tools/ocr/run_gpu_ocr.py'),'--source',str(source),
        '--output',str(output),'--source-language',source_language]
    started=time.monotonic();process=None
    try:
        with (folder/'gpu-worker.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                env=dict(os.environ,PYTHONUNBUFFERED='1',PYTHONUTF8='1'))
            while process.poll() is None:
                if time.monotonic()-started>timeout:raise TimeoutError('Server GPU OCR exceeded its deadline.')
                yield {'stage':'gpu_ocr','device':'gpu:0'};time.sleep(1)
        if process.returncode:raise RuntimeError('Server GPU OCR failed; see '+str(folder/'gpu-worker.log'))
        from transformation.ocr.remote_ocr import unpack
        report=unpack(output.with_suffix('.zip'),folder,source)
        (folder/'ocr-timing.json').write_text(json.dumps({'total_seconds':time.monotonic()-started,
            'persistent':False,'detection_seconds':report.get('detection_seconds'),
            'recognition_seconds':report.get('recognition_seconds')},indent=2))
        yield {'stage':'completed','recognition_dir':str(folder/'recognition'),'device':'gpu:0',
            'gpu_seconds':report['total_seconds'],'execution_host':'server'}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
