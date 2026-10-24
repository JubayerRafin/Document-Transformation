"""Full-pipeline SSH transport; no local processing fallback or public service."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path,PurePosixPath
import shlex
import subprocess
import tarfile
import time
from uuid import uuid4
import zipfile

from transformation.remote_worker import OPTIONS

ROOT=Path(__file__).resolve().parents[1]


def connection(remote):
    host=str(ipaddress.ip_address(remote['host']))
    port=int(remote['port']);key=Path(remote['key']).expanduser()
    if not 1<=port<=65535 or not key.is_file():raise ValueError('Invalid server SSH port or key file.')
    # Brackets disambiguate IPv6 SCP destinations.
    target='root@'+('['+host+']' if ':' in host else host)
    ssh=['ssh','-n','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=30','-i',str(key),'-p',str(port),target]
    scp=['scp','-o','BatchMode=yes','-o','ConnectTimeout=10','-i',str(key),'-P',str(port)]
    return ssh,scp,target


def bundle_code(archive):
    """Upload code only: never datasets, outputs, keys, venvs or local configs."""
    with tarfile.open(archive,'w:gz') as tar:
        for directory in ('transformation','extraction','tools/ocr'):
            for path in sorted((ROOT/directory).rglob('*.py')):
                if not path.is_symlink():tar.add(path,arcname=path.relative_to(ROOT).as_posix())
        for name in ('requirements-deep-doc.txt','requirements-transformation.txt','tools/gpu/setup_full_pipeline.sh'):
            tar.add(ROOT/name,arcname=name)


def unpack(archive,folder,remote_job,expected_source=None):
    folder=Path(folder).resolve()
    with zipfile.ZipFile(archive) as zip:
        names=zip.namelist()
        if len(names)!=len(set(names)) or 'terminal.json' not in names:raise ValueError('Incomplete or duplicated server results.')
        if sum(i.file_size for i in zip.infolist())>2_000_000_000:raise ValueError('Server results exceed 2 GB.')
        for info in zip.infolist():
            path=PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename or (info.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe server result path.')
            if info.is_dir():continue
            target=folder.joinpath(*path.parts)
            if not target.resolve().is_relative_to(folder):raise ValueError('Server result escaped its job folder.')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(zip.read(info))
    def relocate(value):
        if isinstance(value,dict):return {k:relocate(v) for k,v in value.items()}
        if isinstance(value,list):return [relocate(v) for v in value]
        if isinstance(value,str) and (value==remote_job or value.startswith(remote_job+'/')):
            return str(folder / value[len(remote_job):].lstrip('/'))
        return value
    original=json.loads((folder/'terminal.json').read_text(encoding='utf-8'))
    if expected_source is not None and original.get('status')=='generated' and original.get('source_sha256')!=expected_source:
        raise ValueError('Server result does not match the uploaded document.')
    (folder/'server-terminal.json').write_text(json.dumps(original,indent=2),encoding='utf-8')
    for path in folder.rglob('*.json'):
        if path.name=='server-terminal.json':continue
        try:value=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,UnicodeError):continue
        path.write_text(json.dumps(relocate(value),ensure_ascii=False),encoding='utf-8')
    result=relocate(original)
    if result['status']=='generated':
        output=Path(result.get('output_file') or result.get('output_pdf') or '')
        if not output.resolve().is_relative_to(folder) or not output.is_file():
            raise ValueError('Server output is missing or outside this job.')
    (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def run_remote_pipeline(data,filename,workflow,remote,**options):
    if Path(filename).name!=filename or '\\' in filename or Path(filename).suffix.lower() not in {'.pdf','.docx','.pptx'}:
        raise ValueError('Use a supported document display filename.')
    ssh,scp,target=connection(remote)
    timeout=int(options.get('timeout',3600))
    if not 60<=timeout<=10800:raise ValueError('Server run time must be 60–10800 seconds.')
    folder=Path(options.get('root',ROOT/'runs/transformation')).resolve()/('server-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8])
    folder.mkdir(parents=True)
    job='/workspace/transformx-full-jobs/'+uuid4().hex
    started=time.monotonic();launched=False;process=None;timings={}
    def event(stage):return {'run_dir':str(folder),'stage':stage,'status':'processing','seconds':round(time.monotonic()-started,1),'source_name':filename,'execution':{'execution_host':'server','remote_job':job}}
    def wait(command,stage,stdin=None):
        nonlocal process
        tick=time.monotonic()
        with (folder/(stage+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,stdin=stdin,stdout=log,stderr=subprocess.STDOUT)
            while process.poll() is None:
                if time.monotonic()-started>timeout:raise TimeoutError('Server job exceeded its deadline.')
                yield event(stage);time.sleep(1)
            if process.returncode:raise RuntimeError('Server '+stage+' failed; see '+str(folder/(stage+'.log')))
        timings[stage+'_seconds']=round(time.monotonic()-tick,6)
    try:
        # Check runtimes in the upload connection instead of a separate SSH session.
        bundle_code(folder/'engine.tar.gz')
        (folder/'source').write_bytes(bytes(data))
        (folder/'request.json').write_text(json.dumps({'filename':filename,'workflow':workflow,'options':{k:v for k,v in options.items() if k in OPTIONS}},default=str),encoding='utf-8')
        with tarfile.open(folder/'job.tar','w') as tar:
            for name in ('engine.tar.gz','source','request.json'):tar.add(folder/name,arcname=name)
        from transformation.ocr.remote_ocr import background_command
        body=f'cd {job}/code && timeout --foreground --kill-after=10s {timeout}s /workspace/transformx-engine/bin/python -u -m transformation.remote_worker {job}'
        preflight='test -x /workspace/transformx-engine/bin/python && test -x /workspace/transformx-babeldoc/bin/python && test -x /workspace/paddle-env/bin/python'
        launch=f'if ! {{ {preflight}; }}; then echo "Server runtimes missing; run tools.gpu.deploy_full_pipeline. No local fallback." >&2; exit 1; fi; mkdir -p {job}/code && tar -xf - -C {job} && tar -xzf {job}/engine.tar.gz -C {job}/code && {{ '+background_command(job,body)+' }'
        launched=True
        with (folder/'job.tar').open('rb') as upload:
            yield from wait([arg for arg in ssh if arg!='-n']+[launch],'uploading',stdin=upload)
        processing_started=time.monotonic()
        while True:
            if time.monotonic()-started>timeout:raise TimeoutError('Server document job exceeded its deadline.')
            command=f'if test -f {job}/exit.status; then printf "DONE\\n"; cat {job}/exit.status; else printf "RUNNING\\n"; fi; test ! -f {job}/progress.json || cat {job}/progress.json'
            state=subprocess.check_output([*ssh,command],text=True,stderr=subprocess.STDOUT,timeout=20)
            lines=state.splitlines()
            if lines[0]=='DONE':
                if len(lines)<2 or lines[1]!='0':
                    log=subprocess.check_output([*ssh,f'tail -n 60 {job}/worker.log'],text=True,timeout=20)
                    (folder/'server-worker.log').write_text(log,encoding='utf-8')
                    raise RuntimeError('Server worker failed; see '+str(folder/'server-worker.log'))
                break
            update=event('server_starting')
            if len(lines)>1:
                update.update(json.loads('\n'.join(lines[1:])))
                # Completion is not published until artifact retrieval succeeds.
                if update['status'] in {'generated','failed'}:update.update(stage='downloading',status='processing')
            update['server_processing_seconds']=update.get('seconds',0)
            update['seconds']=round(time.monotonic()-started,1)
            yield update;time.sleep(2)
        timings['processing_and_polling_seconds']=round(time.monotonic()-processing_started,6)
        yield from wait([*scp,target+':'+job+'/result.zip',str(folder/'result.zip')],'downloading')
        unpack_started=time.monotonic()
        result=unpack(folder/'result.zip',folder,job,hashlib.sha256(bytes(data)).hexdigest())
        timings['unpack_seconds']=round(time.monotonic()-unpack_started,6)
        timings['archive_bytes']=(folder/'result.zip').stat().st_size
        result['transport_timing']=timings
        result['remote_job']=job;result['transport_total_seconds']=round(time.monotonic()-started,1)
        (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        launched=False
        yield result
    except Exception as error:
        import traceback
        (folder/'transport-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        failed=event('failed');failed.update(status='failed',error=str(error))
        (folder/'manifest.json').write_text(json.dumps(failed,indent=2),encoding='utf-8')
        yield failed
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if launched:
            try:subprocess.run([*ssh,f'if test -f {job}/pid && ! test -f {job}/exit.status; then kill -TERM -- -$(cat {job}/pid) 2>/dev/null || true; fi'],timeout=15,capture_output=True)
            except (OSError,subprocess.TimeoutExpired):pass
