"""Outbound HTTPS worker. Run on the GPU server with the existing engine dependencies.

No automatic replay of a claimed job after a crash: check the server process first.
"""
import argparse, getpass, hashlib, http.cookiejar, json, os, subprocess, sys, time, uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen

PROJECT=Path(__file__).resolve().parents[2]
MAX=50_000_000
USER_AGENT='TransformX-Worker/1.0'


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
        'engine_profile':{'python':sys.executable,'ocr_python':sys.executable,
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
    if input_url.scheme!='https' or input_url.netloc!=expected.netloc:raise ValueError('Unexpected input storage host.')
    with urlopen(j['input_url'],timeout=120) as r:data=r.read(MAX+1)
    if len(data)>MAX or hashlib.sha256(data).hexdigest()!=j['source_sha256']:raise ValueError('Input size/hash mismatch.')
    with pymupdf.open(stream=data,filetype='pdf') as pdf:
        if pdf.needs_pass or not 1<=len(pdf)<=200:raise ValueError('Unsupported encrypted PDF or page count.')
    source=folder/'source.pdf';source.write_bytes(data)
    request=folder/'request.json';events=folder/'engine-events.jsonl';terminal=folder/'terminal.json'
    request.write_text(json.dumps({'source':str(source),'filename':j['filename'],'options':j['options'],
        'profile':cfg['engine_profile']}),encoding='utf8')
    started=time.monotonic();stage='starting';elapsed=0
    # All parsing/reconstruction and inference execute on this server.
    with (folder/'process.log').open('w',encoding='utf8') as log:
        process=subprocess.Popen([engine_python(cfg),'-u','-m','backend.engine_job',str(request),str(events),str(terminal)],cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        while process.poll() is None:
            if events.exists():
                for line in reversed(events.read_text(encoding='utf8').splitlines()):
                    try:ev=json.loads(line);stage=ev['stage'];elapsed=float(ev['elapsed_seconds']);break
                    except (ValueError,KeyError):continue
            try:rpc(cfg,'/worker/jobs/'+j['id']+'/heartbeat',{'stage':stage,'elapsed_seconds':elapsed},lease=j['lease'])
            except Exception:print('Progress upload delayed; processing continues. No automatic replay.',flush=True)
            time.sleep(10)
    result=json.loads(terminal.read_text(encoding='utf8')) if terminal.exists() else {}
    if process.returncode!=0 or result.get('status')!='generated':raise RuntimeError('Engine did not generate an output.')
    output=Path(result.get('output_file') or result.get('output_pdf') or '').resolve()
    if not output.is_relative_to(folder.resolve()) or not output.is_file():raise ValueError('Output outside this job directory.')
    if result.get('source_sha256')!=j['source_sha256']:raise ValueError('Engine source hash mismatch.')
    if output.stat().st_size>MAX:raise ValueError('Output exceeds cloud upload limit.')
    with pymupdf.open(output) as pdf:
        if pdf.needs_pass or not len(pdf):raise ValueError('Invalid generated PDF.')
        pages=len(pdf)
    metrics={'source_sha256':j['source_sha256'],'total_seconds':time.monotonic()-started,'pages':pages,
        'gpu_verified':result.get('gpu_verified') is True}
    if isinstance(result.get('seconds'),(int,float)):metrics['server_seconds']=result['seconds']
    data,mime=multipart(output.read_bytes(),metrics)
    rpc(cfg,'/worker/jobs/'+j['id']+'/result',raw=data,content_type=mime,lease=j['lease'])

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
            if ocr_session:
                ocr_session.ensure();os.environ['TRANSFORMX_OCR_SERVICE_PID']=str(ocr_session.process.pid)
            execute(cfg,j,root);print('Result uploaded',j['id'],flush=True)
        except Exception:
            import traceback
            with (root/'worker-errors.log').open('a',encoding='utf8') as f:traceback.print_exc(file=f)
            # Output/transport failures require inspection; never replay inference automatically.
            try:rpc(cfg,'/worker/jobs/'+j['id']+'/fail',lease=j['lease'])
            except Exception:print('Could not record failure. Administrator must inspect the running lease.',flush=True)
            print('Job requires inspection; see private worker-errors.log.',flush=True)

def run(args):
    cfg=json.loads(Path(args.config).read_text(encoding='utf8'));origin(cfg['url'])
    profile=cfg.setdefault('engine_profile',{})
    for key in ('python','ocr_python'):profile.setdefault(key,sys.executable)
    enabled=profile.get('persistent_ocr',False)
    if type(enabled) is not bool:raise ValueError('persistent_ocr must be boolean.')
    if enabled and profile.get('ocr_device')!='gpu:0':raise ValueError('Persistent OCR requires explicit GPU placement.')
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    from transformation.ocr.service import managed_session
    with managed_session(profile['ocr_python'],root,enabled) as session:
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
