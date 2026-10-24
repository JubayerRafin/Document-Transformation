from contextlib import asynccontextmanager
import hashlib, hmac, json, secrets, time, threading
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from .config import Settings
from .db import database, User, Login, Job, Artifact, JobEvent, WorkerState, Review, Baseline, BaselineMember, identifier
from .security import digest, verify, password_hash
from . import service as svc

def create_app(settings=None):
    settings=settings or Settings.load();engine,sessions=database(settings)
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()
    app=FastAPI(title='TransformX V1',version='0.1.0',lifespan=lifespan)
    app.state.sessions=sessions;app.state.settings=settings
    login_attempts={};lock=threading.Lock();dummy=password_hash(secrets.token_urlsafe(20))

    @app.exception_handler(svc.Problem)
    async def problem(request,exc):return JSONResponse({'error':exc.code},status_code=exc.status)

    @app.middleware('http')
    async def boundaries(request,call_next):
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            if origin and origin.rstrip('/') not in settings.origins:return JSONResponse({'error':'ORIGIN_DENIED'},status_code=403)
            size=request.headers.get('content-length')
            if size and (not size.isdigit() or int(size)>settings.max_upload+1_000_000):return JSONResponse({'error':'REQUEST_TOO_LARGE'},status_code=413)
            if request.url.path.startswith('/v1/') and request.url.path!='/v1/session':
                token=request.cookies.get('tx_session','')
                with sessions() as s:login=s.get(Login,digest(token))
                if not login or login.expires<time.time() or not hmac.compare_digest(request.headers.get('x-csrf-token',''),login.csrf):return JSONResponse({'error':'CSRF_OR_SESSION_INVALID'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/v1/') else 'no-cache'
        return response

    def identity(request:Request):
        with sessions() as s:
            session=s.get(Login,digest(request.cookies.get('tx_session','')))
            user=s.get(User,session.user_id) if session and session.expires>time.time() else None
            if not user or not user.active:raise svc.Problem('SIGN_IN_REQUIRED',401)
            return user

    @app.get('/health/live')
    def live():return {'status':'alive','version':'0.1.0'}

    @app.post('/v1/session')
    async def login(request:Request):
        body=await request.json();name=body.get('name','');password=body.get('password','')
        if not isinstance(name,str) or not isinstance(password,str):raise svc.Problem('INVALID_LOGIN',401)
        address=request.client.host if request.client else 'unknown';now=time.monotonic()
        with lock:
            login_attempts[address]=[t for t in login_attempts.get(address,[]) if now-t<300]
            if len(login_attempts[address])>=10:raise svc.Problem('TRY_LOGIN_LATER',429)
            login_attempts[address].append(now)
        with sessions.begin() as s:
            user=s.scalar(select(User).where(User.name==name))
            ok=verify(password,user.password if user else dummy)
            if not user or not ok or not user.active:raise svc.Problem('INVALID_LOGIN',401)
            token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
            s.execute(delete(Login).where(Login.expires<time.time()))
            s.add(Login(token_hash=digest(token),user_id=user.id,csrf=csrf,expires=time.time()+8*3600))
            response=JSONResponse({'name':user.name,'role':user.role,'csrf':csrf})
            response.set_cookie('tx_session',token,httponly=True,secure=settings.secure_cookies,samesite='lax',max_age=8*3600)
            return response

    @app.get('/v1/me')
    def me(request:Request,user=Depends(identity)):
        with sessions() as s:csrf=s.get(Login,digest(request.cookies['tx_session'])).csrf
        return {'name':user.name,'role':user.role,'csrf':csrf}

    @app.delete('/v1/session')
    def logout(request:Request,user=Depends(identity)):
        with sessions.begin() as s:s.execute(delete(Login).where(Login.token_hash==digest(request.cookies.get('tx_session',''))))
        response=JSONResponse({'status':'signed_out'});response.delete_cookie('tx_session');return response

    @app.get('/v1/system')
    def system(user=Depends(identity)):
        with sessions() as s:state=s.get(WorkerState,'main')
        enabled=bool(settings.worker_profile.get('enabled'))
        recent=state is not None and time.time()-state.heartbeat<20
        return {'worker':'offline' if not enabled or not recent else state.status,
                'gpu_verified':False,'note':'Worker heartbeat is not GPU/model readiness. Each run retains its own evidence.',
                'database':engine.dialect.name,'software_version':'0.1.0'}

    @app.get('/v1/jobs')
    def jobs(user=Depends(identity)):
        with sessions() as s:
            rows=s.scalars(select(Job).order_by(Job.created.desc()).limit(500)).all()
            return [svc.serialize(s,j) for j in rows if svc.allowed(j,user)]

    @app.post('/v1/jobs',status_code=202)
    async def submit(request:Request,file:UploadFile=File(...),options:str=Form(...),user=Depends(identity)):
        if len(options)>120_000:raise svc.Problem('OPTIONS_TOO_LARGE',413)
        try:raw=json.loads(options)
        except ValueError:raise svc.Problem('INVALID_OPTIONS') from None
        data=await file.read(settings.max_upload+1)
        job_id=svc.submit(sessions,settings,user,data,file.filename,raw,request.headers.get('idempotency-key',''))
        return {'job_id':job_id,'status_url':'/v1/jobs/'+job_id}

    @app.get('/v1/jobs/{job_id}')
    def job(job_id:str,user=Depends(identity)):
        with sessions() as s:return svc.serialize(s,svc.job_for(s,job_id,user))

    @app.get('/v1/jobs/{job_id}/events')
    def events(job_id:str,after:int=0,user=Depends(identity)):
        with sessions() as s:
            svc.job_for(s,job_id,user)
            rows=s.scalars(select(JobEvent).where(JobEvent.job_id==job_id,JobEvent.id>after).order_by(JobEvent.id).limit(500)).all()
            return [{'id':e.id,'stage':e.stage,'elapsed_seconds':e.elapsed,'created':e.created} for e in rows]

    @app.post('/v1/jobs/{job_id}/cancel')
    def cancel(job_id:str,user=Depends(identity)):
        with sessions.begin() as s:
            j=svc.job_for(s,job_id,user)
            if user.id!=j.owner and user.role!='admin':raise svc.Problem('OWNER_REQUIRED',403)
            if j.baseline or j.status!='queued':raise svc.Problem('ONLY_QUEUED_CANCELLATION_SUPPORTED',409)
            j.status='cancelled';j.stage='cancelled';j.updated=time.time();s.add(JobEvent(job_id=j.id,stage='cancelled',elapsed=0))
        return {'status':'cancelled'}

    @app.post('/v1/jobs/{job_id}/reviews')
    async def review(job_id:str,request:Request,user=Depends(identity)):
        body=await request.json();svc.review(sessions,user,job_id,body.get('decision'),body.get('note'));return {'status':'recorded'}

    @app.get('/v1/jobs/{job_id}/reviews')
    def reviews(job_id:str,user=Depends(identity)):
        with sessions() as s:
            svc.job_for(s,job_id,user)
            return [{'reviewer':s.get(User,r.reviewer).name,'decision':r.decision,'note':r.note,'output_sha256':r.output_sha256,'created':r.created} for r in s.scalars(select(Review).where(Review.job_id==job_id)).all()]

    def artifact(s,job_id,artifact_id,user):
        svc.job_for(s,job_id,user);a=s.get(Artifact,artifact_id)
        if not a or a.job_id!=job_id:raise svc.Problem('NOT_FOUND',404)
        if a.kind not in {'source','result','gemini_reference'} and user.role!='admin':raise svc.Problem('PRIVATE_DIAGNOSTIC',403)
        return a

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}')
    def download(job_id:str,artifact_id:str,user=Depends(identity)):
        with sessions() as s:a=artifact(s,job_id,artifact_id,user)
        return FileResponse(svc.artifact_path(settings,a),filename=a.filename,media_type='application/pdf' if a.filename.endswith('.pdf') else 'application/octet-stream')

    @app.get('/v1/jobs/{job_id}/artifacts/{artifact_id}/pages/{page}')
    def preview(job_id:str,artifact_id:str,page:int,user=Depends(identity)):
        import pymupdf
        with sessions() as s:a=artifact(s,job_id,artifact_id,user)
        if a.kind not in {'source','result','gemini_reference'}:raise svc.Problem('NOT_PREVIEWABLE')
        with pymupdf.open(svc.artifact_path(settings,a)) as d:
            if not 1<=page<=min(200,len(d)):raise svc.Problem('PAGE_NOT_FOUND',404)
            p=d[page-1];scale=min(1.4,1200/max(p.rect.width,p.rect.height))
            return Response(p.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).tobytes('png'),media_type='image/png')

    @app.get('/v1/jobs/{job_id}/report')
    def report(job_id:str,user=Depends(identity)):
        with sessions() as s:data=svc.serialize(s,svc.job_for(s,job_id,user))
        # Export no internal storage locations or remote credentials.
        return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="run-report.json"'})

    @app.post('/v1/baselines',status_code=201)
    async def baseline(request:Request,user=Depends(identity)):
        if user.role!='admin':raise svc.Problem('ADMIN_REQUIRED',403)
        body=await request.json();ids=body.get('job_ids',[]);title=body.get('title','')
        if not isinstance(title,str) or not 3<=len(title)<=180 or not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise svc.Problem('INVALID_BASELINE')
        with sessions.begin() as s:
            selected=[svc.job_for(s,i,user) for i in ids]
            if any(j.status!='succeeded' for j in selected):raise svc.Problem('BASELINE_NEEDS_COMPLETED_RUNS',409)
            b=Baseline(id=identifier(),title=title,owner=user.id);s.add(b);s.flush()
            for j in selected:
                art=s.scalar(select(Artifact).where(Artifact.job_id==j.id,Artifact.kind=='result'))
                if not art:raise svc.Problem('MISSING_BASELINE_OUTPUT',409)
                s.add(BaselineMember(baseline_id=b.id,job_id=j.id,output_sha256=art.sha256));j.baseline=True
            return {'id':b.id,'title':title,'documents':len(ids),'quality':'unchanged; not automatic approval'}

    @app.get('/v1/baselines')
    def baselines(user=Depends(identity)):
        if user.role not in {'admin','reviewer','viewer'}:raise svc.Problem('STUDIO_ROLE_REQUIRED',403)
        with sessions() as s:return [{'id':b.id,'title':b.title,'created':b.created,'job_ids':[m.job_id for m in s.scalars(select(BaselineMember).where(BaselineMember.baseline_id==b.id)).all()]} for b in s.scalars(select(Baseline).order_by(Baseline.created.desc())).all()]

    static=Path(__file__).parent/'static'
    @app.get('/')
    @app.get('/studio')
    def frontend():return FileResponse(static/'index.html')
    app.mount('/assets',StaticFiles(directory=static),name='assets')
    return app
