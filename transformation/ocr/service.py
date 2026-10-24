"""Private Unix-socket OCR session owned by the cloud worker, opt-in on Linux."""
from contextlib import contextmanager
import json,os,socket,subprocess,time
from pathlib import Path
from uuid import uuid4

ENV='TRANSFORMX_OCR_SOCKET'
LIMIT=65536


def encode(value):
    raw=json.dumps(value).encode()+b'\n'
    if len(raw)>LIMIT:raise ValueError('OCR control message exceeds its limit.')
    return raw


def receive(connection):
    raw=bytearray()
    while b'\n' not in raw:
        part=connection.recv(min(4096,LIMIT+1-len(raw)))
        if not part:raise RuntimeError('OCR session disconnected; no automatic replay.')
        raw.extend(part)
        if len(raw)>LIMIT:raise ValueError('OCR control message exceeds its limit.')
    return json.loads(bytes(raw).split(b'\n',1)[0])


def health(path):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(1);connection.connect(str(path));connection.sendall(encode({'action':'health'}))
        return receive(connection).get('ready') is True


class Session:
    def __init__(self,python,root,idle_seconds=300):
        self.python=str(python);self.root=Path(root).resolve();self.idle_seconds=idle_seconds
        self.folder=self.root/('.ocr-service-'+uuid4().hex[:8]);self.folder.mkdir(mode=0o700)
        self.path=self.folder/'worker.sock';self.process=None

    def ensure(self):
        if self.process and self.process.poll() is None:
            try:
                if health(self.path):return
            except (OSError,RuntimeError,ValueError):pass
            raise RuntimeError('OCR session unresponsive; inspect it before restarting a job.')
        self.path.unlink(missing_ok=True)
        with (self.folder/'service.log').open('ab') as log:
            self.process=subprocess.Popen([self.python,'-u','-m','tools.ocr.persistent_worker',
                '--socket',str(self.path),'--root',str(self.root),'--parent',str(os.getpid()),
                '--idle-seconds',str(self.idle_seconds)],cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                env=dict(os.environ,PYTHONUTF8='1',PYTHONUNBUFFERED='1'))
        started=time.monotonic()
        while time.monotonic()-started<30:
            if self.process.poll() is not None:raise RuntimeError('OCR session startup failed; see private service.log.')
            try:
                if health(self.path):return
            except (OSError,RuntimeError,ValueError):pass
            time.sleep(.1)
        self.close();raise TimeoutError('OCR session did not become ready.')

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.path.unlink(missing_ok=True)


@contextmanager
def managed_session(python,root,enabled=False):
    if not enabled:yield None;return
    if os.name!='posix':raise ValueError('Persistent GPU OCR requires a Unix server.')
    previous=os.environ.get(ENV);previous_pid=os.environ.get('TRANSFORMX_OCR_SERVICE_PID');session=Session(python,root)
    try:
        session.ensure();os.environ[ENV]=str(session.path)
        os.environ['TRANSFORMX_OCR_SERVICE_PID']=str(session.process.pid);yield session
    finally:
        session.close()
        if previous is None:os.environ.pop(ENV,None)
        else:os.environ[ENV]=previous
        if previous_pid is None:os.environ.pop('TRANSFORMX_OCR_SERVICE_PID',None)
        else:os.environ['TRANSFORMX_OCR_SERVICE_PID']=previous_pid


def request(source,output,language,timeout):
    """Yield progress while one region runs; predictors are never used concurrently."""
    path=os.environ[ENV];started=time.monotonic();raw=bytearray()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(1);connection.connect(path)
        connection.sendall(encode({'action':'ocr','source':str(Path(source).resolve()),
            'output':str(Path(output).resolve()),'language':language,'timeout':timeout}))
        while b'\n' not in raw:
            if time.monotonic()-started>timeout:raise TimeoutError('Persistent OCR deadline exceeded; no automatic replay.')
            try:
                part=connection.recv(min(4096,LIMIT+1-len(raw)))
                if not part:raise RuntimeError('OCR session disconnected; no automatic replay.')
                raw.extend(part)
                if len(raw)>LIMIT:raise ValueError('OCR response exceeds its limit.')
            except socket.timeout:
                yield {'stage':'gpu_ocr','device':'gpu:0','execution_host':'server','persistent':True}
        response=json.loads(bytes(raw).split(b'\n',1)[0])
        if response.get('status')!='completed':raise RuntimeError('Persistent GPU OCR failed; inspect private OCR stage logs. Error type: '+str(response.get('error_type')))
        yield {'stage':'service_completed','service_metrics':response['metrics'],
               'client_seconds':time.monotonic()-started}
