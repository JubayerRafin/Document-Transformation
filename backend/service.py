"""Persistence boundary: all job and artifact access is owner/role scoped."""
import hashlib, json, re, shutil, time
from pathlib import Path
from sqlalchemy import select, update
from .db import Artifact, Document, Job, JobEvent, Review, identifier

class Problem(Exception):
    def __init__(self,code,status=400):self.code=code;self.status=status

def sha(data):return hashlib.sha256(data).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False)
PII={'Email addresses','Phone numbers','Names','Addresses','Identifiers','Bank accounts'}

def options(raw):
    if not isinstance(raw,dict) or set(raw)-{'operation','source_language','target_language','output_format','protected_values','document_context','pii_categories'}:raise Problem('INVALID_OPTIONS')
    result={'operation':'translation','source_language':'en','target_language':'ko','output_format':'pdf',
            'protected_values':[],'document_context':'','pii_categories':[]};result.update(raw)
    if result['operation'] not in {'translation','pii','combined'} or result['output_format']!='pdf':raise Problem('UNSUPPORTED_OPERATION_OR_FORMAT')
    if any(not isinstance(result[x],str) or not re.fullmatch('[a-z]{2,3}(?:-[A-Za-z]{2,8})?',result[x]) for x in ('source_language','target_language')):raise Problem('INVALID_LANGUAGE')
    if result['operation']!='pii' and result['source_language']==result['target_language']:raise Problem('SAME_LANGUAGE')
    cats=result['pii_categories'];vals=result['protected_values'];ctx=result['document_context']
    if not isinstance(cats,list) or any(not isinstance(x,str) or x not in PII for x in cats):raise Problem('INVALID_PII_CATEGORIES')
    if (result['operation']=='translation' and cats) or (result['operation']!='translation' and not cats):raise Problem('SELECT_OPERATION_CATEGORIES')
    if not isinstance(vals,list) or len(vals)>200 or any(not isinstance(x,str) or not x.strip() or len(x)>500 for x in vals):raise Problem('INVALID_PROTECTED_VALUES')
    if not isinstance(ctx,str) or len(ctx)>8000:raise Problem('INVALID_CONTEXT')
    return result

def validate_pdf(data,filename,limit):
    import pymupdf
    if not isinstance(filename,str) or len(filename)>180 or any(x in filename for x in ('/','\\','\r','\n','\0')) or not filename.lower().endswith('.pdf'):raise Problem('INVALID_FILENAME')
    if not 0<len(data)<=limit:raise Problem('UPLOAD_SIZE_LIMIT',413)
    if not data.lstrip().startswith(b'%PDF-'):raise Problem('INVALID_PDF')
    try:
        with pymupdf.open(stream=data,filetype='pdf') as d:
            if d.needs_pass or not 0<d.page_count<=200:raise Problem('PDF_ENCRYPTED_OR_PAGE_LIMIT')
            return d.page_count
    except Problem:raise
    except Exception:raise Problem('INVALID_PDF') from None

def allowed(job,user):
    return job.owner==user.id or user.role=='admin' or (job.baseline and user.role in {'reviewer','viewer'})

def job_for(session,job_id,user):
    job=session.get(Job,job_id)
    if not job or not allowed(job,user):raise Problem('NOT_FOUND',404)
    return job

def artifact_path(settings,artifact):
    path=(settings.data/artifact.storage_key).resolve()
    if not path.is_relative_to(settings.data.resolve()) or not path.is_file() or path.is_symlink():raise Problem('ARTIFACT_UNAVAILABLE',404)
    return path

def register(session,settings,job,path,kind,filename=None):
    path=Path(path).resolve()
    if not path.is_relative_to(settings.data.resolve()) or not path.is_file() or path.is_symlink():raise ValueError('Artifact must be inside private storage.')
    content_hash=sha(path.read_bytes())
    a=Artifact(id=identifier(),job_id=job.id,kind=kind,storage_key=path.relative_to(settings.data).as_posix(),
               filename=filename or path.name,sha256=content_hash,size=path.stat().st_size)
    session.add(a);session.flush();return a

def submit(sessions,settings,user,data,filename,raw,key):
    if user.role not in {'customer','admin'}:raise Problem('READ_ONLY_ACCOUNT',403)
    if not re.fullmatch('[A-Za-z0-9_-]{8,120}',key):raise Problem('INVALID_IDEMPOTENCY_KEY')
    opts=options(raw);pages=validate_pdf(data,filename,settings.max_upload)
    fingerprint=sha((sha(data)+filename+canonical(opts)).encode())
    from sqlalchemy.exc import IntegrityError
    with sessions() as s:
        prior=s.scalar(select(Job).where(Job.owner==user.id,Job.idempotency_key==key))
        if prior:
            if prior.request_hash!=fingerprint:raise Problem('IDEMPOTENCY_CONFLICT',409)
            return prior.id
    job_id=identifier();folder=settings.data/'jobs'/job_id;folder.mkdir()
    try:
        (folder/'source.pdf').write_bytes(data)
        with sessions.begin() as s:
            doc=Document(id=identifier(),owner=user.id,filename=filename,sha256=sha(data),pages=pages);s.add(doc);s.flush()
            job=Job(id=job_id,owner=user.id,document_id=doc.id,options=opts,idempotency_key=key,request_hash=fingerprint)
            s.add(job);s.flush();register(s,settings,job,folder/'source.pdf','source',filename)
            s.add(JobEvent(job_id=job_id,stage='queued',elapsed=0))
        return job_id
    except IntegrityError:
        # Concurrent identical submissions preserve one durable queue entry.
        with sessions() as s:
            prior=s.scalar(select(Job).where(Job.owner==user.id,Job.idempotency_key==key))
            if prior and prior.request_hash==fingerprint:return prior.id
        raise Problem('IDEMPOTENCY_CONFLICT',409) from None
    finally:
        with sessions() as s:exists=s.get(Job,job_id) is not None
        if not exists:
            for p in folder.iterdir():p.unlink()
            folder.rmdir()

def serialize(s,job):
    doc=s.get(Document,job.document_id)
    arts=s.scalars(select(Artifact).where(Artifact.job_id==job.id)).all()
    return {'id':job.id,'filename':doc.filename,'pages':doc.pages,'status':job.status,'stage':job.stage,
            'quality':job.quality,'baseline':job.baseline,'created':job.created,'options':job.options,
            'metrics':job.metrics,'error_code':job.error_code,'attempt':job.attempt,
            'source_sha256':doc.sha256,'artifacts':[{'id':a.id,'kind':a.kind,'filename':a.filename,'sha256':a.sha256,'size':a.size} for a in arts]}

def claim(sessions):
    # Conditional UPDATE makes claiming safe under SQLite and PostgreSQL.
    with sessions.begin() as s:
        ids=s.scalars(select(Job.id).where(Job.status=='queued',Job.baseline==False).order_by(Job.created).limit(20)).all()
        for job_id in ids:
            now=time.time();attempt=identifier()
            changed=s.execute(update(Job).where(Job.id==job_id,Job.status=='queued').values(status='running',stage='starting',updated=now,heartbeat=now,attempt=attempt))
            if changed.rowcount:
                s.add(JobEvent(job_id=job_id,stage='starting',elapsed=0));return job_id
    return None

def complete(sessions,settings,job_id,terminal):
    with sessions.begin() as s:
        job=s.get(Job,job_id)
        if not job or job.status!='running':return
        output=Path(terminal.get('output_file') or terminal.get('output_pdf') or '')
        folder=(settings.data/'jobs'/job.id).resolve()
        if terminal.get('status')!='generated' or not output.resolve().is_relative_to(folder) or not output.is_file():
            job.status='failed';job.stage='failed';job.error_code='PROCESSING_FAILED';job.updated=time.time();return
        doc=s.get(Document,job.document_id)
        if terminal.get('source_sha256')!=doc.sha256:raise ValueError('Output source hash mismatch.')
        validate_pdf(output.read_bytes(),'result.pdf',500_000_000)
        register(s,settings,job,output,'result','transformed.pdf')
        manifest=folder/'terminal.json'
        manifest.write_text(json.dumps(terminal,ensure_ascii=False,default=str,indent=2),encoding='utf8')
        register(s,settings,job,manifest,'manifest')
        job.status='succeeded';job.stage='complete';job.quality='requires_review';job.updated=time.time()
        job.metrics={'server_seconds':terminal.get('seconds'),'total_seconds':terminal.get('transport_total_seconds'),
                     'blockers':len(terminal.get('scan_acceptance_blockers',[])), 'acceptance_checked':False,
                     'evaluation_status':'pending','engine_run_id':terminal.get('run_id')}
        s.add(JobEvent(job_id=job.id,stage='complete',elapsed=float(terminal.get('transport_total_seconds') or terminal.get('seconds') or 0)))

def review(sessions,user,job_id,decision,note):
    if user.role not in {'admin','reviewer'}:raise Problem('REVIEWER_REQUIRED',403)
    if decision not in {'requires_revision','accepted'} or not isinstance(note,str) or not 3<=len(note.strip())<=4000:raise Problem('INVALID_REVIEW')
    with sessions.begin() as s:
        job=job_for(s,job_id,user)
        if job.status!='succeeded':raise Problem('NO_GENERATED_OUTPUT',409)
        if decision=='accepted' and (job.metrics.get('blockers',0)>0 or not job.metrics.get('acceptance_checked',False)):raise Problem('VALIDATION_REVIEW_REQUIRED',409)
        art=s.scalar(select(Artifact).where(Artifact.job_id==job.id,Artifact.kind=='result'))
        s.add(Review(job_id=job.id,reviewer=user.id,decision=decision,note=note,output_sha256=art.sha256))
        job.quality=decision
