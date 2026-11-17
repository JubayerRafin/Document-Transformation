"""SSH GPU OCR transport. Keys stay local; only the selected PDF is uploaded."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from uuid import uuid4
import zipfile
import tarfile

FILES = {'diagnostic.json', 'gpu-run.json', 'page-1.png', 'page-1_res.json',
         'recognition/manifest.json', 'recognition/lines.jsonl'}


def background_command(remote, worker):
    """Detach all SSH descriptors; publish exit status only after worker returns."""
    body=(f'echo $$ > {remote}/pid; {worker}; result=$?; '
          f'printf "%s\\n" "$result" > {remote}/exit.tmp; mv {remote}/exit.tmp {remote}/exit.status')
    return 'nohup setsid sh -c '+shlex.quote(body)+f' > {remote}/worker.log 2>&1 < /dev/null &'


def unpack(archive, folder, source):
    folder, source = Path(folder), Path(source)
    with zipfile.ZipFile(archive) as z:
        if not FILES.issubset(z.namelist()) or any(z.namelist().count(n) != 1 for n in FILES):
            raise ValueError('GPU OCR archive is incomplete or duplicated.')
        if sum(z.getinfo(n).file_size for n in FILES) > 100_000_000:
            raise ValueError('GPU OCR evidence exceeds the size limit.')
        for name in FILES:
            path = folder/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(z.read(name))
    report = json.loads((folder/'gpu-run.json').read_text(encoding='utf-8'))
    if report['status'] != 'completed' or report['source_sha256'] != hashlib.sha256(source.read_bytes()).hexdigest():
        raise ValueError('GPU OCR result does not match this source.')
    for relative, field, value in [('diagnostic.json','source',str(source.resolve())),
                                   ('recognition/manifest.json','detection_run',str(folder.resolve()))]:
        p = folder/relative
        data = json.loads(p.read_text(encoding='utf-8'))
        data['remote_'+field] = data[field]
        data[field] = value
        p.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return report


def run_remote(source, folder, config, timeout=1200, source_language='unknown'):
    host = str(ipaddress.ip_address(config['host']))
    port = int(config['port'])
    key = Path(config['key']).expanduser().resolve()
    if not 1 <= port <= 65535 or not key.is_file():
        raise ValueError('Enter a valid SSH port and existing private-key file.')
    source, folder = Path(source).resolve(), Path(folder).resolve()
    import pymupdf
    with pymupdf.open(source) as pdf:
        if len(pdf) != 1 or pdf.needs_pass or pdf[0].rotation or pdf[0].get_text().strip():
            raise ValueError("GPU OCR currently supports one unrotated image-only PDF page.")
        if pdf[0].rect.width * pdf[0].rect.height > 20_000_000:
            raise ValueError("Scanned page exceeds the prototype raster size limit.")
    folder.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    remote = '/workspace/transformx-jobs/' + uuid4().hex
    common = ['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ConnectTimeout=10',
              '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-i',str(key)]
    ssh = ['ssh.exe' if os.name == 'nt' else 'ssh', '-n', *common, '-p',str(port), 'root@'+host]
    scp = ['scp.exe' if os.name == 'nt' else 'scp', *common, '-P',str(port)]
    started = time.monotonic()
    timings = {}
    process = None
    remote_started = False
    def wait(command, stage, stdin=None):
        nonlocal process
        stage_started=time.monotonic()
        with (folder/(stage+'.log')).open('w', encoding='utf-8') as log:
            process = subprocess.Popen(command, stdin=stdin if stdin is not None else subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None:
                if time.monotonic()-started > timeout:
                    raise TimeoutError('Remote OCR exceeded the time limit.')
                if time.monotonic()-stage_started > 120:
                    raise TimeoutError(f'GPU OCR {stage} SSH transfer stalled for 120 seconds. See {folder/(stage+".log")}')
                yield {'stage':stage, 'device':'gpu:0'}
                time.sleep(1)
            if process.returncode:
                raise RuntimeError(f'GPU OCR {stage} failed. See {folder/(stage+".log")}')
            timings[stage]=round(time.monotonic()-stage_started,6)
    try:
        # One authenticated upload replaces mkdir + worker SCP + document SCP
        # + launch. Only our allowlisted files enter this private job archive.
        archive=folder/'inputs.tar'
        with tarfile.open(archive,'w') as bundle:
            for name in ('diagnose_paddle_detector.py','recognize_paddle_boxes.py','run_gpu_ocr.py','raster_policy.py'):
                bundle.add(repo/'tools/ocr'/name,arcname=name)
            bundle.add(source,arcname='source.pdf')
        body = f'timeout --kill-after=10s {int(timeout)}s /workspace/paddle-env/bin/python {remote}/run_gpu_ocr.py --source {remote}/source.pdf --output {remote}/result --source-language {shlex.quote(source_language)}'
        remote_started = True
        # ssh -n would discard the input archive. Retain it for status calls,
        # but remove it from the upload command only.
        upload_ssh=[arg for arg in ssh if arg!='-n']
        # Group the detached command so shell '&' cannot background extraction.
        launch=f'mkdir -p {remote} && tar -xf - -C {remote} && {{ '+background_command(remote,body)+' }'
        with archive.open('rb') as upload:
            yield from wait([*upload_ssh,launch], 'uploading_and_starting',stdin=upload)
        worker_started=time.monotonic()
        polls=0
        while True:
            if time.monotonic()-started > timeout: raise TimeoutError('Remote OCR exceeded the time limit.')
            # Each status request is independently bounded. No inference stdout
            # pipe is kept attached to a long-lived Windows SSH process.
            state=subprocess.check_output([*ssh,f'if test -f {remote}/exit.status; then cat {remote}/exit.status; else echo RUNNING; fi'],
                                          stderr=subprocess.STDOUT,text=True,timeout=20).strip()
            polls+=1
            (folder/'gpu_ocr.log').write_text(f'remote_job={remote}\nstatus={state}\n',encoding='utf-8')
            if state != 'RUNNING':
                if state != '0':
                    diagnostic=subprocess.check_output([*ssh,f'tail -n 40 {remote}/worker.log'],stderr=subprocess.STDOUT,text=True,timeout=20)
                    (folder/'gpu_ocr.log').write_text(diagnostic,encoding='utf-8')
                    raise RuntimeError(f'GPU OCR worker exited with status {state}; see {folder/"gpu_ocr.log"}')
                break
            yield {'stage':'gpu_ocr','device':'gpu:0','remote_job':remote}
            time.sleep(2)
        remote_started = False
        timings['worker_and_status_polling']=round(time.monotonic()-worker_started,6)
        yield from wait([*scp, 'root@'+host+':'+remote+'/result.zip', str(folder/'result.zip')], 'downloading_results')
        report = unpack(folder/'result.zip', folder, source)
        timings.update(total_seconds=round(time.monotonic()-started,6),gpu_worker_seconds=report['total_seconds'],status_polls=polls)
        (folder/'transport-timing.json').write_text(json.dumps(timings,indent=2),encoding='utf-8')
        yield {'stage':'completed', 'device':'gpu:0', 'recognition_dir':str(folder/'recognition'),
               'gpu_seconds':report['total_seconds'], 'remote_job':remote,'transport_timing':timings}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if remote_started:
            # Scoped process group; never stop Ollama or other users' GPU jobs.
            try:
                subprocess.run([*ssh, f'if test -f {remote}/pid && ! test -f {remote}/exit.status; then kill -TERM -- -$(cat {remote}/pid) 2>/dev/null || true; fi'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                pass  # Server-side timeout remains the fallback.
