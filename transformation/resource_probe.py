"""Optional sampled server resources; never load a model or change its state."""
from contextlib import contextmanager
import hashlib,json,os,platform,subprocess,threading,time
from pathlib import Path


def snapshot(folder,source,options):
    """Record controlled settings and existing model residency, never prompts."""
    import urllib.request
    from urllib.parse import urlparse
    keys=('source','target','translation_model','pii_model','pdf_page_mode','translation_workers',
          'ollama_parallelism','ignore_cache','http_keepalive','babeldoc_dual_output','figure_engine')
    record={'source_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            'hostname':platform.node(),'cpu_count':os.cpu_count(),
            'settings':{key:options.get(key) for key in keys},
            'cache_note':'Model residency and translation-result caching are separate; coldness is not assumed.'}
    record['persistent_ocr_session']=bool(os.environ.get('TRANSFORMX_OCR_SOCKET'))
    from transformation.llm.ollama_native import translation_policy_digest
    record['translation_policy_sha256']=translation_policy_digest()
    context={key:options.get(key) for key in ('protected_values','document_context','terminology')}
    record['context_and_protection_sha256']=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
    record['ocr_cache_policy']='reuse' if options.get('ocr_cache_root') else 'fresh_or_not_used'
    try:
        record['gpu_inventory']=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version',
            '--format=csv,noheader'],capture_output=True,text=True,timeout=2,check=True).stdout.strip()
    except (OSError,subprocess.SubprocessError):record['gpu_inventory']=None
    endpoint=options.get('translation_endpoint','http://127.0.0.1:11434/v1');parsed=urlparse(endpoint)
    if parsed.hostname in {'127.0.0.1','localhost','::1'} and parsed.scheme in {'http','https'}:
        try:
            with urllib.request.urlopen(f'{parsed.scheme}://{parsed.netloc}/api/ps',timeout=2) as response:
                record['initial_model_residency']=json.load(response).get('models',[])
        except (OSError,ValueError,TypeError,AttributeError):record['initial_model_residency']=None
    root=Path(__file__).resolve().parents[1]
    files=('transformation/validation/output_validation.py','transformation/validation/overlap_index.py',
           'transformation/pdf/translation_concurrency.py','transformation/llm/translategemma.py',
           'tools/ocr/run_gpu_ocr.py','tools/ocr/recognize_paddle_boxes.py','tools/ocr/model_pool.py')
    record['code_sha256']={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                          if (root/name).is_file() else None for name in files}
    try:(Path(folder)/'benchmark-metadata.json').write_text(json.dumps(record,indent=2))
    except OSError:pass


def tree_rss(pid):
    pending=[pid];seen=set();rss=0
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current)
        try:
            status=(Path('/proc')/str(current)/'status').read_text()
            rss+=next(int(line.split()[1])*1024 for line in status.splitlines() if line.startswith('VmRSS:'))
            children=(Path('/proc')/str(current)/'task'/str(current)/'children').read_text()
            pending.extend(map(int,children.split()))
        except (OSError,StopIteration,ValueError):continue
    return rss or None


@contextmanager
def monitor(folder,enabled=False,interval=2):
    if not enabled:yield;return
    stop=threading.Event();started=time.monotonic()
    report={'schema':'transformx-resources-1','platform':platform.platform(),'sample_interval_seconds':interval,
            'rss_scope':'Sampled sum of job process-tree RSS; shared pages may be counted more than once.',
            'gpu_scope':'Sampled total memory used on visible GPUs, including other resident models/jobs.',
            'peak_tree_rss_bytes':None,'peak_visible_gpu_mib':None,'samples':0,
            'peak_ocr_service_rss_bytes':None,
            'ocr_service_rss_scope':'Sampled separate OCR session process-tree RSS; not included in job-tree RSS.'}
    def sample():
        rss=tree_rss(os.getpid())
        if rss:report['peak_tree_rss_bytes']=max(report['peak_tree_rss_bytes'] or 0,rss)
        session_pid=os.environ.get('TRANSFORMX_OCR_SERVICE_PID','')
        if session_pid.isdecimal() and int(session_pid)!=os.getpid():
            ocr_rss=tree_rss(int(session_pid))
            if ocr_rss:report['peak_ocr_service_rss_bytes']=max(report['peak_ocr_service_rss_bytes'] or 0,ocr_rss)
        try:
            result=subprocess.run(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],
                                  capture_output=True,text=True,timeout=2,check=True)
            used=sum(float(line) for line in result.stdout.splitlines() if line.strip())
            report['peak_visible_gpu_mib']=max(report['peak_visible_gpu_mib'] or 0,used)
        except (OSError,ValueError,subprocess.SubprocessError):pass
        report['samples']+=1
    def poll():
        while not stop.is_set():
            sample()
            if stop.wait(interval):break
    thread=threading.Thread(target=poll,name='transformx-resource-probe',daemon=True);thread.start()
    try:yield
    finally:
        stop.set();thread.join(timeout=3)
        report['elapsed_seconds']=round(time.monotonic()-started,6)
        try:(Path(folder)/'resource-metrics.json').write_text(json.dumps(report,indent=2))
        except OSError:pass
