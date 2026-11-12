"""Outbound HTTPS worker. Run on the GPU server with the existing engine dependencies.

No automatic replay of a claimed job after a crash. A job whose worker stops
reporting is marked interrupted by the application (after 2 minutes once this
worker claims again, otherwise after 15 minutes); it must be resubmitted.
"""
import argparse, getpass, hashlib, http.client, http.cookiejar, json, os, signal, subprocess, sys, time, uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen

PROJECT=Path(__file__).resolve().parents[2]
MAX=50_000_000
USER_AGENT='TransformX-Worker/1.0'
HEARTBEAT_SECONDS=10
# Extra time beyond the engine's own limit, so the engine can report its own timeout first.
DEADLINE_GRACE_SECONDS=300
MANUAL_LIMIT_SECONDS=600
# Total stays well under the API's 15-minute stale-job threshold.
UPLOAD_RETRY_DELAYS=(5,15,30,60,120)
# Must match FAILURE_CODES in cloud/src/index.mjs. Engine free text is never uploaded.
FAILURE_CODES={'ENGINE_FAILED','ENGINE_TIMEOUT','INPUT_REJECTED','OUTPUT_INVALID','RESULT_UPLOAD_FAILED',
               'REDACTION_REVIEW_REQUIRED','PRIVACY_CHECK_BLOCKED','MANUAL_REDACTION_FAILED'}


class JobFailure(Exception):
    """A job failure with a reason code safe to report to the hosted API."""
    def __init__(self,code,message=None):
        super().__init__(message or code)
        self.code=code if code in FAILURE_CODES or code=='LEASE_LOST' else 'ENGINE_FAILED'


def spawn(command,**kwargs):
    """Start a process in its own group so a timeout also stops OCR/BabelDOC children."""
    if os.name=='nt':kwargs['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP
    else:kwargs['start_new_session']=True
    return subprocess.Popen(command,**kwargs)


def stop_process(process,grace=10):
    """Stop a spawned process and its children; returns once the parent exited."""
    if process.poll() is not None:return
    try:
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=30)
        else:
            os.killpg(process.pid,signal.SIGTERM)
            try:
                process.wait(timeout=grace);return
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
    except (OSError,subprocess.SubprocessError):
        process.kill()
    process.wait()


def supervise(process,limit,tick,interval=HEARTBEAT_SECONDS,clock=time.monotonic):
    """Wait for a process, calling tick() every interval; stop it on timeout or any error."""
    started=clock();next_tick=started+interval
    try:
        while process.poll() is None:
            now=clock()
            if now-started>limit:raise JobFailure('ENGINE_TIMEOUT','Processing time limit reached.')
            if now>=next_tick:
                tick(now-started);next_tick=now+interval
            try:process.wait(timeout=.5)
            except subprocess.TimeoutExpired:pass
    except BaseException:
        stop_process(process);raise
    return process.returncode


def run_limit(profile):
    limit=profile.get('timeout_seconds',3600)
    if type(limit) not in (int,float) or not 60<=limit<=86400:
        raise ValueError('timeout_seconds must be between 60 and 86400.')
    return float(limit)


def engine_python(cfg):
    """Engine interpreter is distinct from the BabelDOC interpreter (python)."""
    return cfg.get('engine_profile',{}).get('engine_python') or sys.executable


def cuda_ready(profile):
    python=profile.get('ocr_python') or sys.executable
    if python==sys.executable:
        import paddle
        return paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count()>0
    # Execute only the trusted server-configured interpreter, without a shell.
    result=subprocess.run([python,'-c',
        'import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count()>0'],
        capture_output=True,timeout=30)
    return result.returncode==0

def origin(value):
    p=urlparse(value)
    if p.scheme!='https' and not (p.scheme=='http' and p.hostname in ('localhost','127.0.0.1')):
        raise ValueError('Use an HTTPS application address.')
    if p.username or p.password or p.path not in ('','/') or p.query or p.fragment:
        raise ValueError('Use the application origin without a path or credentials.')
    return value.rstrip('/')

def register(args):
    if Path(args.config).exists():raise ValueError('Worker configuration already exists; choose a new --config path.')
    base=origin(args.url);jar=http.cookiejar.CookieJar();client=build_opener(HTTPCookieProcessor(jar))
    def post(path,payload,csrf=None):
        headers={'Content-Type':'application/json','Origin':base,'User-Agent':USER_AGENT}
        if csrf:headers['X-CSRF-Token']=csrf
        with client.open(Request(base+path,json.dumps(payload).encode(),headers,method='POST'),timeout=30) as r:return json.load(r)
    account=post('/v1/session',{'name':args.email,'password':getpass.getpass('Supabase account password: ')})
    worker=post('/v1/workers',{'name':args.name},account['csrf'])
    config={'url':base,'worker_id':worker['id'],'token':worker['token'],
        'supabase_url':worker['supabase_url'],
        # A persistent GPU OCR session loads Paddle models once per job instead of
        # once per page/figure (Linux servers; ignored elsewhere).
        'engine_profile':{'python':sys.executable,'ocr_python':sys.executable,
          'ocr_device':'gpu:0','persistent_ocr':os.name=='posix',
          'translation_endpoint':'http://127.0.0.1:11434/v1','pii_endpoint':'http://127.0.0.1:11434/v1',
          'timeout_seconds':3600,'engine_options':{'table_strategy':'auto','pdf_page_mode':'document',
          'http_keepalive':True,'nativeized_ir_mode':'reuse','babeldoc_dual_output':False,
          'figure_engine':'ocr-slots','light_pdf_io':True,'ocr_cache_mode':'reuse','translation_qps':8}}}
    path=Path(args.config);path.parent.mkdir(parents=True,exist_ok=True)
    # Refuse accidental overwrites and avoid printing the worker credential.
    with path.open('x',encoding='utf8') as f:json.dump(config,f,indent=2)
    try:path.chmod(0o600)
    except OSError:pass
    print('Worker registered. Private configuration saved to',path)

def rpc(cfg,path,payload=None,raw=None,content_type='application/json',lease=None):
    headers={'Authorization':'Bearer '+cfg['token'],'X-Worker-ID':cfg['worker_id'],'Content-Type':content_type,'User-Agent':USER_AGENT}
    if lease:headers['X-Job-Lease']=lease
    data=raw if raw is not None else json.dumps(payload or {}).encode()
    with urlopen(Request(origin(cfg['url'])+path,data,headers,method='POST'),timeout=120) as r:return json.load(r)

def multipart(data,metrics):
    boundary='tx'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="metrics"\r\n\r\n'+json.dumps(metrics)+
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="result.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode()
    return body+data+f'\r\n--{boundary}--\r\n'.encode(),'multipart/form-data; boundary='+boundary

def heartbeat(cfg,j,stage,elapsed):
    """Report progress. A lost lease (job interrupted or finished elsewhere) stops the job."""
    try:rpc(cfg,'/worker/jobs/'+j['id']+'/heartbeat',{'stage':stage,'elapsed_seconds':elapsed},lease=j['lease'])
    except HTTPError as exc:
        if exc.code==409:raise JobFailure('LEASE_LOST','The application no longer assigns this job to this worker.') from exc
        print('Progress upload delayed; processing continues. No automatic replay.',flush=True)
    except Exception:
        print('Progress upload delayed; processing continues. No automatic replay.',flush=True)

def transient(exc):
    if isinstance(exc,HTTPError):return exc.code in (408,425,429) or exc.code>=500
    return isinstance(exc,(URLError,TimeoutError,ConnectionError,http.client.HTTPException))

def deliver(cfg,j,data,metrics,sleep=time.sleep):
    """Upload a result, retrying transient failures. The API acknowledges a
    repeated upload of the same bytes, so a lost response is safe to retry."""
    payload,mime=multipart(data,metrics)
    for delay in (*UPLOAD_RETRY_DELAYS,None):
        try:return rpc(cfg,'/worker/jobs/'+j['id']+'/result',raw=payload,content_type=mime,lease=j['lease'])
        except Exception as exc:
            if delay is None or not transient(exc):
                if isinstance(exc,HTTPError) and exc.code==409:raise JobFailure('LEASE_LOST') from exc
                raise JobFailure('RESULT_UPLOAD_FAILED','Result upload failed; the output remains in the job folder.') from exc
            print(f'Result upload failed; retrying in {delay} s. The output is kept locally.',flush=True)
            sleep(delay)
            heartbeat(cfg,j,'uploading_result',0)

def failure_code(result):
    """Reason code from the engine's terminal record, never its free-text error."""
    if result.get('redaction_review_required') is True:return 'REDACTION_REVIEW_REQUIRED'
    code=result.get('failure_code')
    return code if code in FAILURE_CODES else 'ENGINE_FAILED'

def summarize_checks(result):
    """Counts of automated-check findings; contents stay on the private worker."""
    validation=result.get('validation') if isinstance(result.get('validation'),dict) else {}
    independent=validation.get('independent_checks') if isinstance(validation.get('independent_checks'),dict) else {}
    count=lambda value:len(value) if isinstance(value,list) else 0
    privacy=independent.get('rendered_privacy')
    errors=result.get('translation_errors')
    return {'acceptable':independent.get('acceptable') is True,
            'known_sensitive_residuals':count(independent.get('known_sensitive_residuals')),
            'partial_name_residuals':count(independent.get('partial_name_residuals')),
            'geometry_blockers':count(independent.get('geometry_blockers')),
            'layout_review_candidates':count(independent.get('layout_review_candidates')),
            'uninspected_content':count(independent.get('uninspected_content')),
            'scan_acceptance_blockers':count(result.get('scan_acceptance_blockers')),
            'translation_errors':errors if type(errors) is int and errors>=0 else 0,
            'privacy_verification':'not_run' if not isinstance(privacy,dict) else 'passed' if privacy.get('passed') is True else 'failed'}

def preflight(cfg):
    """Advertise installed prerequisites, not a claim that inference was GPU-verified."""
    capabilities={'ready':False,'operations':[],'gpu':'','models':[],'note':''}
    try:
        gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],timeout=10,text=True).strip()
        if not gpu:raise RuntimeError('No allocated NVIDIA GPU visible.')
        capabilities['gpu']=gpu[:150]
        import pymupdf
        if not cuda_ready(cfg['engine_profile']):
            raise RuntimeError('GPU-enabled Paddle and an allocated CUDA device are required.')
        endpoint=cfg['engine_profile'].get('translation_endpoint','http://127.0.0.1:11434/v1').rstrip('/')
        endpoint=endpoint[:-3] if endpoint.endswith('/v1') else endpoint
        with urlopen(endpoint+'/api/tags',timeout=15) as response:
            names=[m['name'] for m in json.load(response).get('models',[])]
        capabilities['models']=names[:20]
        if 'translategemma:12b' in names:capabilities['operations'].append('translation')
        if 'qwen3.5:9b' in names:capabilities['operations'].append('pii')
        if len(capabilities['operations'])==2:capabilities['operations'].append('combined')
        if capabilities['operations']:capabilities['operations'].append('manual_redaction')
        capabilities['ready']=bool(capabilities['operations'])
        capabilities['note']='Prerequisites detected; actual GPU inference is verified per job.' if capabilities['ready'] else 'Install a selected model in Ollama.'
    except Exception:
        capabilities['note']='Not ready: check allocated NVIDIA GPU, CUDA PaddleOCR, engine dependencies and Ollama models.'
    return capabilities

def execute(cfg,j,root):
    import pymupdf
    if str(uuid.UUID(j['id']))!=j['id']:raise ValueError('Invalid job identifier.')
    folder=root/j['id'];folder.mkdir(parents=True,exist_ok=False)
    input_url=urlparse(j['input_url']);expected=urlparse(cfg['supabase_url'])
    if input_url.scheme!='https' or input_url.netloc!=expected.netloc:raise JobFailure('INPUT_REJECTED','Unexpected input storage host.')
    with urlopen(j['input_url'],timeout=120) as r:data=r.read(MAX+1)
    if len(data)>MAX or hashlib.sha256(data).hexdigest()!=j['source_sha256']:raise JobFailure('INPUT_REJECTED','Input size/hash mismatch.')
    try:
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            if pdf.needs_pass or not 1<=len(pdf)<=200:raise JobFailure('INPUT_REJECTED','Unsupported encrypted PDF or page count.')
    except JobFailure:raise
    except Exception as exc:raise JobFailure('INPUT_REJECTED','The input PDF could not be opened.') from exc
    source=folder/'source.pdf';source.write_bytes(data)
    if j['options'].get('operation')=='manual_redaction':
        started=time.monotonic()
        heartbeat(cfg,j,'applying_manual_redactions',0)
        output=folder/'manually-redacted.pdf'
        plan=folder/'manual-regions.json';audit_path=folder/'manual-redaction-audit.json'
        plan.write_text(json.dumps(j['options']['regions']),encoding='utf8')
        with (folder/'process.log').open('w',encoding='utf8') as log:
            process=spawn([engine_python(cfg),'-m','transformation.validation.manual_redaction',
                str(source),str(output),str(plan),j['source_sha256'],str(audit_path)],cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
            supervise(process,MANUAL_LIMIT_SECONDS,lambda elapsed:heartbeat(cfg,j,'applying_manual_redactions',elapsed))
        if process.returncode!=0 or not audit_path.is_file():raise JobFailure('MANUAL_REDACTION_FAILED','Manual redaction verification failed.')
        audit=json.loads(audit_path.read_text(encoding='utf8'))
        if audit['source_sha256']!=j['source_sha256'] or audit['output_sha256']!=hashlib.sha256(output.read_bytes()).hexdigest():raise JobFailure('MANUAL_REDACTION_FAILED','Manual redaction hash mismatch.')
        if output.stat().st_size>MAX:raise JobFailure('OUTPUT_INVALID','Output exceeds cloud upload limit.')
        metrics={'source_sha256':j['source_sha256'],'pages':audit['pages'],
                 'server_seconds':time.monotonic()-started,'gpu_verified':False,
                 'manual_redaction_verified':True,'manual_regions':audit['regions']}
        deliver(cfg,j,output.read_bytes(),metrics)
        return
    request=folder/'request.json';events=folder/'engine-events.jsonl';terminal=folder/'terminal.json'
    request.write_text(json.dumps({'source':str(source),'filename':j['filename'],'options':j['options'],
        'profile':cfg['engine_profile']}),encoding='utf8')
    started=time.monotonic()
    def progress(elapsed):
        stage,seconds='starting',0
        if events.exists():
            for line in reversed(events.read_text(encoding='utf8').splitlines()):
                try:ev=json.loads(line);stage=ev['stage'];seconds=float(ev['elapsed_seconds']);break
                except (ValueError,KeyError,TypeError):continue
        heartbeat(cfg,j,stage,seconds)
    # All parsing/reconstruction and inference execute on this server. The
    # engine enforces timeout_seconds itself; the worker stops it shortly after.
    limit=run_limit(cfg['engine_profile'])+DEADLINE_GRACE_SECONDS
    with (folder/'process.log').open('w',encoding='utf8') as log:
        process=spawn([engine_python(cfg),'-u','-m','backend.engine_job',str(request),str(events),str(terminal)],cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        supervise(process,limit,progress)
    try:result=json.loads(terminal.read_text(encoding='utf8')) if terminal.exists() else {}
    except ValueError:result={}
    if not isinstance(result,dict):result={}
    if process.returncode!=0 or result.get('status')!='generated':raise JobFailure(failure_code(result),'Engine did not generate an output.')
    output=Path(result.get('output_file') or result.get('output_pdf') or '').resolve()
    if not output.is_relative_to(folder.resolve()) or not output.is_file():raise JobFailure('OUTPUT_INVALID','Output outside this job directory.')
    if result.get('source_sha256')!=j['source_sha256']:raise JobFailure('OUTPUT_INVALID','Engine source hash mismatch.')
    if output.stat().st_size>MAX:raise JobFailure('OUTPUT_INVALID','Output exceeds cloud upload limit.')
    try:
        with pymupdf.open(output) as pdf:
            if pdf.needs_pass or not len(pdf):raise ValueError('Invalid generated PDF.')
            pages=len(pdf)
    except Exception as exc:raise JobFailure('OUTPUT_INVALID','Invalid generated PDF.') from exc
    checks=summarize_checks(result)
    metrics={'source_sha256':j['source_sha256'],'total_seconds':time.monotonic()-started,'pages':pages,
        'gpu_verified':result.get('gpu_verified') is True,'checks':checks,
        'blockers':sum(v for k,v in checks.items() if type(v) is int)}
    if isinstance(result.get('seconds'),(int,float)):metrics['server_seconds']=result['seconds']
    deliver(cfg,j,output.read_bytes(),metrics)

def _run(args,cfg,root,ocr_session=None):
    lock=(root/'worker.lock').open('a+b');lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl;fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    print('Connected worker polling. Claimed jobs are never automatically replayed.',flush=True)
    last_check=0
    while True:
        try:
            if time.monotonic()-last_check>45:
                state=preflight(cfg);rpc(cfg,'/worker/status',{'capabilities':state});last_check=time.monotonic()
                print('Worker ready for: '+(', '.join(state['operations']) or 'none; prerequisites missing'),flush=True)
            j=rpc(cfg,'/worker/claim').get('job')
        except Exception:print('Application unavailable; retrying claim later.',flush=True);time.sleep(15);continue
        if not j:time.sleep(5);continue
        print('Starting job',j['id'],flush=True)
        try:
            if ocr_session and j['options'].get('operation')!='manual_redaction':
                ocr_session.ensure();os.environ['TRANSFORMX_OCR_SERVICE_PID']=str(ocr_session.process.pid)
            execute(cfg,j,root);print('Result uploaded',j['id'],flush=True)
        except Exception as exc:
            import traceback
            with (root/'worker-errors.log').open('a',encoding='utf8') as f:traceback.print_exc(file=f)
            code=exc.code if isinstance(exc,JobFailure) else 'ENGINE_FAILED'
            if code=='LEASE_LOST':
                # The application already ended this job (interrupted or finished); nothing to report.
                print('Job',j['id'],'is no longer assigned to this worker; processing stopped.',flush=True)
                continue
            # Output/transport failures require inspection; never replay inference automatically.
            # Only the reason code is reported; details stay in the private worker-errors.log.
            try:rpc(cfg,'/worker/jobs/'+j['id']+'/fail',{'code':code},lease=j['lease'])
            except Exception:print('Could not record failure; the application marks the job interrupted after 15 minutes.',flush=True)
            print('Job failed ('+code+'); see private worker-errors.log.',flush=True)

def run(args):
    cfg=json.loads(Path(args.config).read_text(encoding='utf8'));origin(cfg['url'])
    profile=cfg.setdefault('engine_profile',{})
    for key in ('python','ocr_python'):profile.setdefault(key,sys.executable)
    run_limit(profile)
    enabled=profile.get('persistent_ocr',False)
    if type(enabled) is not bool:raise ValueError('persistent_ocr must be boolean.')
    if enabled and os.name!='posix':
        print('Persistent OCR needs a Linux server; using one OCR process per call.',flush=True);enabled=False
    if enabled and profile.get('ocr_device')!='gpu:0':raise ValueError('Persistent OCR requires explicit GPU placement.')
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    from transformation.ocr.service import managed_session
    with managed_session(profile['ocr_python'],root,enabled) as session:
        if session:
            # Load OCR models once at worker start, so the first job does not wait for them.
            from transformation.ocr.service import prewarm
            prewarm(root/'prewarm')
        _run(args,cfg,root,session)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    reg=sub.add_parser('register');reg.add_argument('--url',required=True);reg.add_argument('--email',required=True);reg.add_argument('--name',required=True)
    for parser in (reg,):parser.add_argument('--config',default='configs/gpu.worker.local.json')
    worker=sub.add_parser('run');worker.add_argument('--config',default='configs/gpu.worker.local.json');worker.add_argument('--root',default='runs/cloud-worker')
    args=p.parse_args()
    if args.action=='register':register(args)
    else:run(args)
if __name__=='__main__':main()
