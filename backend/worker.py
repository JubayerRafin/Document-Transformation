"""Single durable worker; no CPU fallback when a remote profile is selected."""
import json, subprocess, time, traceback
from .config import Settings, PROJECT
from .db import database, Job, Document, Artifact, JobEvent, WorkerState
from . import service as svc
from sqlalchemy import select

def heartbeat(sessions,status):
    with sessions.begin() as s:
        row=s.get(WorkerState,'main')
        if row is None:s.add(WorkerState(id='main',heartbeat=time.time(),status=status))
        else:row.heartbeat=time.time();row.status=status

def run_one(sessions,settings,job_id):
    profile=settings.worker_profile
    with sessions() as s:
        j=s.get(Job,job_id);doc=s.get(Document,j.document_id)
        source=s.scalar(select(Artifact).where(Artifact.job_id==j.id,Artifact.kind=='source'))
        request={'profile':profile,'source':str(svc.artifact_path(settings,source)),
                 'filename':doc.filename,'options':j.options}
    folder=settings.data/'jobs'/job_id
    (folder/'request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf8')
    events=folder/'engine-events.jsonl';terminal=folder/'terminal.json';offset=0;seen=0;started=time.monotonic()
    # The transport's own timeout/finally owns remote cleanup. Do not kill a
    # remote process blindly and claim that billed GPU work has stopped.
    with (folder/'process.log').open('w',encoding='utf8') as log:
        process=subprocess.Popen([profile['engine_python'],'-u','-m','backend.engine_job',str(folder/'request.json'),str(events),str(terminal)],
            cwd=PROJECT,stdout=log,stderr=subprocess.STDOUT)
        while process.poll() is None:
            heartbeat(sessions,'busy')
            if events.exists():
                lines=events.read_text(encoding='utf8').splitlines()
                for line in lines[seen:]:
                    try:e=json.loads(line)
                    except ValueError:break
                    with sessions.begin() as s:
                        j=s.get(Job,job_id);j.stage=str(e['stage'])[:80];j.updated=time.time();j.heartbeat=time.time()
                        s.add(JobEvent(job_id=job_id,stage=j.stage,elapsed=float(e['elapsed_seconds'])))
                    seen+=1
            time.sleep(1)
    if not terminal.exists():raise RuntimeError('Engine exited without a final result.')
    svc.complete(sessions,settings,job_id,json.loads(terminal.read_text(encoding='utf8')))

def main():
    settings=Settings.load();engine,sessions=database(settings)
    # A process-level lock prevents multiple GPU schedulers on this host.
    lock=(settings.data/'worker.lock').open('a+b')
    lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if __import__('os').name=='nt':
            import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl;fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:raise SystemExit('A worker is already running for this storage directory.')
    try:
        while True:
            enabled=bool(settings.worker_profile.get('enabled'))
            heartbeat(sessions,'ready' if enabled else 'disabled')
            if enabled:
                job_id=svc.claim(sessions)
                if job_id:
                    try:run_one(sessions,settings,job_id)
                    except Exception:
                        (settings.data/'jobs'/job_id/'worker-error.log').write_text(traceback.format_exc(),encoding='utf8')
                        with sessions.begin() as s:
                            j=s.get(Job,job_id);j.status='failed';j.stage='failed';j.error_code='WORKER_ERROR';j.updated=time.time()
                    continue
            time.sleep(2)
    except KeyboardInterrupt:pass
    finally:heartbeat(sessions,'offline');lock.close();engine.dispose()
if __name__=='__main__':main()
