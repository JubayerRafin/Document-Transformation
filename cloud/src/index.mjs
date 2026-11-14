import frontendCsp from '../generated/frontend-csp.json' with {type:'json'};
import {manualRoute} from './manual-redaction.mjs';
const BUCKET='transformx-private', MAX=50_000_000;
// Reasons a worker may report. Free text from the engine never reaches the API.
export const FAILURE_CODES=['ENGINE_FAILED','ENGINE_TIMEOUT','INPUT_REJECTED','OUTPUT_INVALID','RESULT_UPLOAD_FAILED',
 'REDACTION_REVIEW_REQUIRED','PRIVACY_CHECK_BLOCKED','MANUAL_REDACTION_FAILED'];
// Running jobs heartbeat every ~10 s; silence this long means the worker stopped.
const STALE_SECONDS=900;
const CHECK_COUNTS=['known_sensitive_residuals','partial_name_residuals','geometry_blockers','layout_review_candidates','uninspected_content','scan_acceptance_blockers','translation_errors'];
export function checks(raw){
 if(!raw||typeof raw!=='object'||Array.isArray(raw))return null;
 const c={acceptable:raw.acceptable===true,
  privacy_verification:['passed','failed','not_run'].includes(raw.privacy_verification)?raw.privacy_verification:'not_run'};
 for(const k of CHECK_COUNTS)c[k]=Number.isInteger(raw[k])&&raw[k]>=0&&raw[k]<=1_000_000?raw[k]:0;
 return c;
}
const REVIEW_CATEGORIES=['Names','Email addresses','Phone numbers','Addresses','Identifiers','Bank accounts','Dates of birth','Unknown'];
const REVIEW_REASONS=['unverified_answer','whole_line','rule_overruled_model','unreadable_text','final_check'];
// Review-queue items: page, box (fractions of the displayed page), category, reason, state. Never text.
export function reviewItems(raw,pages){
 if(!Array.isArray(raw))return [];
 const out=[];
 for(const r of raw.slice(0,200)){
  if(!r||typeof r!=='object'||Array.isArray(r))continue;
  const box=['x','y','width','height'].map(k=>r[k]);
  if(!Number.isInteger(r.page)||r.page<1||r.page>Math.max(1,pages||1)||!box.every(v=>typeof v==='number'&&Number.isFinite(v)))continue;
  const [x,y,width,height]=box;
  if(x<0||y<0||width<=0||height<=0||x+width>1.0001||y+height>1.0001)continue;
  if(!REVIEW_CATEGORIES.includes(r.category)||!REVIEW_REASONS.includes(r.reason)||!['masked','suggested'].includes(r.state))continue;
  out.push({page:r.page,x,y,width,height,category:r.category,reason:r.reason,state:r.state});
 }
 return out;
}
const REDACTION_METHODS=['field_label','pattern','credential_label_pattern','address_block','model_proposal','document_name_sweep','name_variant','document_consistency','unverifiable_model_answer','final_check'];
// Why each box was drawn: page, box, category and detection method. Never text.
export function redactionMap(raw,pages){
 if(!Array.isArray(raw))return [];
 const out=[];
 for(const r of raw.slice(0,400)){
  if(!r||typeof r!=='object'||!REDACTION_METHODS.includes(r.method))continue;
  const [box]=reviewItems([{...r,reason:'whole_line',state:'masked'}],pages);
  if(box)out.push({page:box.page,x:box.x,y:box.y,width:box.width,height:box.height,category:box.category,method:r.method});
 }
 return out;
}
export class Fault extends Error { constructor(code,status=400){super(code);this.status=status;} }
const json=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
export const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
export function options(raw){
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new Fault('INVALID_OPTIONS');
 const allowed=['operation','source_language','target_language','output_format','protected_values','document_context','pii_categories','worker_id','pii_style','pii_keep'];
 if(Object.keys(raw).some(k=>!allowed.includes(k)))throw new Fault('UNKNOWN_OPTION');
 const o={operation:'translation',source_language:'en',target_language:'ko',output_format:'pdf',protected_values:[],document_context:'',pii_categories:[],...raw};
 if(!['translation','pii','combined'].includes(o.operation)||o.output_format!=='pdf')throw new Fault('INVALID_OPERATION');
 if(![o.source_language,o.target_language].every(v=>typeof v==='string'&&/^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(v)))throw new Fault('INVALID_LANGUAGE');
 if(typeof o.document_context!=='string'||o.document_context.length>8000)throw new Fault('INVALID_CONTEXT');
 if(!Array.isArray(o.protected_values)||o.protected_values.length>200||o.protected_values.some(v=>typeof v!=='string'||v.length>500))throw new Fault('INVALID_PROTECTED_VALUES');
 const categories=['Names','Email addresses','Phone numbers','Addresses','Identifiers','Bank accounts','Dates of birth'];
 if(!Array.isArray(o.pii_categories)||o.pii_categories.some(v=>!categories.includes(v))||(o.operation!=='translation'&&!o.pii_categories.length))throw new Fault('INVALID_PII_CATEGORIES');
 if(o.pii_style!==undefined&&!['box','label','pseudonym'].includes(o.pii_style))throw new Fault('INVALID_PII_STYLE');
 if(o.pii_keep!==undefined&&(!Array.isArray(o.pii_keep)||o.pii_keep.length>100||o.pii_keep.some(v=>typeof v!=='string'||!v.trim()||v.length>200)))throw new Fault('INVALID_PII_KEEP');
 return o;
}
const cookie=(r,name)=>r.headers.get('cookie')?.split(';').map(v=>v.trim()).find(v=>v.startsWith(name+'='))?.slice(name.length+1)||'';
const safeName=n=>typeof n==='string'&&n.length<=180&&!/[\\/\x00-\x1f]/.test(n)&&/\.pdf$/i.test(n);
const uuid=x=>typeof x==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(x);
const pathKey=k=>k.split('/').map(encodeURIComponent).join('/');
export function createHandler(transport=fetch){
 return async function handle(request,env){
 const url=new URL(request.url), p=url.pathname, method=request.method;
 const publicHeaders={apikey:env.SUPABASE_PUBLISHABLE_KEY};
 const secret=()=>{if(!env.SUPABASE_SECRET_KEY)throw new Fault('HOSTED_BACKEND_NOT_CONFIGURED',503);return env.SUPABASE_SECRET_KEY;};
 async function sb(path,init={},privileged=true){
  const headers={apikey:privileged?secret():env.SUPABASE_PUBLISHABLE_KEY,...init.headers};
  if(init.body&&!(init.body instanceof ArrayBuffer)&&!ArrayBuffer.isView(init.body))headers['Content-Type']='application/json';
  const r=await transport(env.SUPABASE_URL+path,{...init,headers});
  if(!r.ok)throw new Fault(r.status===409?'CONFLICT':'DATABASE_OR_STORAGE_REQUEST_FAILED',r.status===409?409:502);
  if(r.status===204)return null;return r.json();
 }
 const db=(table,query='',init={})=>sb('/rest/v1/'+table+query,init);
 const patch=(table,query,body)=>db(table,query,{method:'PATCH',headers:{Prefer:'return=representation'},body:JSON.stringify(body)});
 const insert=(table,body)=>db(table,'',{method:'POST',headers:{Prefer:'return=representation'},body:JSON.stringify(body)});
 async function profile(){
  const token=cookie(request,'tx_access');if(!token)throw new Fault('SIGN_IN_REQUIRED',401);
  const r=await transport(env.SUPABASE_URL+'/auth/v1/user',{headers:{...publicHeaders,Authorization:'Bearer '+token}});
  if(!r.ok)throw new Fault('SIGN_IN_REQUIRED',401);const u=await r.json();
  const rows=await db('tx_profiles','?id=eq.'+u.id+'&select=id,role,active');
  if(!rows[0]?.active)throw new Fault('ACCOUNT_NOT_INVITED',403);
  return {...rows[0],name:u.email};
 }
 async function worker(){
  const id=request.headers.get('X-Worker-ID'),token=request.headers.get('Authorization')?.replace(/^Bearer /,'');
  if(!uuid(id)||!token||token.length<32)throw new Fault('WORKER_UNAUTHORIZED',401);
  const rows=await db('tx_workers','?id=eq.'+id+'&enabled=eq.true&select=*');
  if(!rows[0]||rows[0].token_hash!==await sha(new TextEncoder().encode(token)))throw new Fault('WORKER_UNAUTHORIZED',401);
  const owners=await db('tx_profiles','?id=eq.'+rows[0].owner+'&active=eq.true&select=id');
  if(!owners.length)throw new Fault('WORKER_UNAUTHORIZED',401);return rows[0];
 }
 async function job(id,u){
  if(!uuid(id))throw new Fault('NOT_FOUND',404);
  const rows=await db('tx_jobs','?id=eq.'+id+'&select=*');const j=rows[0];
  if(!j||!(j.owner===u.id||u.role==='admin'||(j.baseline&&['mentor','viewer'].includes(u.role))))throw new Fault('NOT_FOUND',404);return j;
 }
 async function lease(id,w){
  if(!uuid(id))throw new Fault('NOT_FOUND',404);
  const rows=await db('tx_jobs','?id=eq.'+id+'&worker_id=eq.'+w.id+'&status=eq.running');
  const j=rows[0];if(!j||j.lease!==request.headers.get('X-Job-Lease'))throw new Fault('LEASE_INVALID',409);return j;
 }
 async function signed(key){const s=await sb('/storage/v1/object/sign/'+BUCKET+'/'+pathKey(key),{method:'POST',body:JSON.stringify({expiresIn:120})});return env.SUPABASE_URL+'/storage/v1'+s.signedURL;}
 async function upload(key,bytes,mime){
  const r=await transport(env.SUPABASE_URL+'/storage/v1/object/'+BUCKET+'/'+pathKey(key),{method:'POST',headers:{apikey:secret(),'Content-Type':mime,'x-upsert':'false'},body:bytes});
  if(!r.ok)throw new Fault('STORAGE_UPLOAD_FAILED',502);
 }
 // Retries reuse content-addressed keys. An object left by an earlier attempt
 // is accepted only when its bytes hash to the expected digest.
 async function uploadOnce(key,bytes,mime,hash){
  try{await upload(key,bytes,mime);}catch(e){
   let r;try{r=await transport(await signed(key));}catch{throw e;}
   if(!r.ok||await sha(await r.arrayBuffer())!==hash)throw e;
  }
 }
 async function body(){try{const text=await request.text();if(text.length>120000)throw new Fault('BODY_TOO_LARGE',413);return JSON.parse(text);}catch(e){if(e instanceof Fault)throw e;throw new Fault('INVALID_JSON');}}
 async function output(j,knownArtifacts){
  const arts=knownArtifacts??await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=neq.manifest&select=id,kind,filename,sha256,size');
  const {lease,request_hash,idempotency_key,...visible}=j;
  return {...visible,source_sha256:j.source_sha256,pages:j.metrics.pages||0,artifacts:arts,created:Date.parse(j.created)/1000,updated:Date.parse(j.updated)/1000};
 }
 try{
  if(!env.SUPABASE_URL||!env.SUPABASE_PUBLISHABLE_KEY)throw new Fault('SUPABASE_NOT_CONFIGURED',503);
  if(method==='OPTIONS')return new Response(null,{status:405});
  if(!['GET','HEAD'].includes(method)&&!p.startsWith('/worker/')){
   const origin=request.headers.get('Origin');if(origin&&origin!==url.origin)throw new Fault('ORIGIN_DENIED',403);
   if(cookie(request,'tx_access')&&p!=='/v1/session'&&cookie(request,'tx_csrf')!==request.headers.get('X-CSRF-Token'))throw new Fault('CSRF_INVALID',403);
  }
  const length=Number(request.headers.get('Content-Length')||0);if(length>MAX+500000)throw new Fault('REQUEST_TOO_LARGE',413);
  if(p==='/health/live')return json({status:'alive',version:'cloud-v1',configured:!!env.SUPABASE_SECRET_KEY});
  if(p==='/v1/session'&&method==='POST'){
   const b=await body();if(typeof b.name!=='string'||typeof b.password!=='string'||b.name.length>254||b.password.length>200)throw new Fault('INVALID_LOGIN',401);
   const r=await transport(env.SUPABASE_URL+'/auth/v1/token?grant_type=password',{method:'POST',headers:{...publicHeaders,'Content-Type':'application/json'},body:JSON.stringify({email:b.name,password:b.password})});
   if(!r.ok)throw new Fault('INVALID_LOGIN',401);const data=await r.json();
   const rows=await db('tx_profiles','?id=eq.'+data.user.id+'&select=id,role,active');if(!rows[0]?.active)throw new Fault('ACCOUNT_NOT_INVITED',403);
   const csrf=crypto.randomUUID(),secure=url.protocol==='https:'?'; Secure':'';
   const response=json({name:data.user.email,role:rows[0].role,csrf});
   response.headers.append('Set-Cookie',`tx_access=${data.access_token}; HttpOnly; Path=/; SameSite=Lax; Max-Age=${Math.min(data.expires_in,3600)}${secure}`);
   response.headers.append('Set-Cookie',`tx_csrf=${csrf}; Path=/; SameSite=Lax; Max-Age=3600${secure}`);return response;
  }
  if(p==='/v1/session'&&method==='DELETE'){
   await profile();const r=json({status:'signed_out'});for(const n of ['tx_access','tx_csrf'])r.headers.append('Set-Cookie',n+'=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax');return r;
  }
  if(p.startsWith('/worker/')){
   const w=await worker();await patch('tx_workers','?id=eq.'+w.id,{heartbeat:new Date().toISOString()});
   if(p==='/worker/status'&&method==='POST'){
    const b=await body(),c=b.capabilities;
    if(!c||typeof c!=='object'||!Array.isArray(c.operations)||c.operations.some(o=>!['translation','pii','combined','manual_redaction'].includes(o)))throw new Fault('INVALID_CAPABILITIES');
    const capabilities={ready:c.ready===true,operations:[...new Set(c.operations)],
      gpu:typeof c.gpu==='string'?c.gpu.slice(0,150):'',note:typeof c.note==='string'?c.note.slice(0,300):'',
      models:Array.isArray(c.models)?c.models.filter(x=>typeof x==='string').slice(0,20).map(x=>x.slice(0,100)):[]};
    await patch('tx_workers','?id=eq.'+w.id,{capabilities});return json({status:'recorded'});
   }
   if(p==='/worker/claim'&&method==='POST'){
    const rows=await sb('/rest/v1/rpc/tx_claim',{method:'POST',body:JSON.stringify({p_worker:w.id})});if(!rows.length)return json({job:null});
    const j=rows[0],a=await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=eq.source');
    if(!a[0]){
     // Never leave a claimed job running without input: it would block this worker.
     await patch('tx_jobs','?id=eq.'+j.id+'&status=eq.running&lease=eq.'+j.lease,{status:'failed',stage:'failed',error_code:'INPUT_REJECTED',updated:new Date().toISOString()});
     return json({job:null});
    }
    return json({job:{id:j.id,lease:j.lease,filename:j.filename,source_sha256:j.source_sha256,options:j.options,input_url:await signed(a[0].storage_key)}});
   }
   const m=p.match(/^\/worker\/jobs\/([^/]+)\/(heartbeat|result|fail)$/);if(!m||method!=='POST')throw new Fault('NOT_FOUND',404);
   let j;
   try{j=await lease(m[1],w);}catch(e){
    // A result retry after a lost response: the job already finished under this
    // lease with these exact bytes. Acknowledge instead of reporting a conflict.
    if(!(e instanceof Fault&&e.message==='LEASE_INVALID'&&m[2]==='result'))throw e;
    const done=(await db('tx_jobs','?id=eq.'+m[1]+'&worker_id=eq.'+w.id+'&status=eq.succeeded'))[0];
    if(!done||done.lease!==request.headers.get('X-Job-Lease'))throw e;
    const file=(await request.formData()).get('file');if(!file||typeof file==='string')throw e;
    const arts=await db('tx_artifacts','?job_id=eq.'+done.id+'&kind=eq.result&select=sha256');
    if(!arts[0]||arts[0].sha256!==await sha(await file.arrayBuffer()))throw e;
    return json({status:'recorded',replayed:true});
   }
   const condition='?id=eq.'+j.id+'&status=eq.running&lease=eq.'+j.lease;
   if(m[2]==='heartbeat'){
    const b=await body(),stage=typeof b.stage==='string'?b.stage.slice(0,80):'processing',elapsed=Number(b.elapsed_seconds||0);
    if(!Number.isFinite(elapsed)||elapsed<0)throw new Fault('INVALID_PROGRESS');
    await patch('tx_jobs',condition,{heartbeat:new Date().toISOString(),updated:new Date().toISOString(),stage});
    if(j.stage!==stage)await insert('tx_events',{job_id:j.id,stage,elapsed_seconds:elapsed});return json({status:'recorded'});
   }
   if(m[2]==='fail'){
    // Older workers send {}; unknown codes are recorded as a generic engine failure.
    const b=await body(),code=FAILURE_CODES.includes(b?.code)?b.code:'ENGINE_FAILED';
    const changed=await patch('tx_jobs',condition,{status:'failed',stage:'failed',error_code:code,updated:new Date().toISOString()});
    if(changed?.length)await insert('tx_events',{job_id:j.id,stage:'failed'});
    return json({status:'recorded'});
   }
   const f=await request.formData(),file=f.get('file');if(!file||typeof file==='string'||file.size>MAX)throw new Fault('INVALID_RESULT');
   let metrics;try{metrics=JSON.parse(f.get('metrics'));}catch{throw new Fault('INVALID_METRICS');}
   if(!metrics||typeof metrics!=='object'||Array.isArray(metrics)||metrics.source_sha256!==j.source_sha256)throw new Fault('SOURCE_HASH_MISMATCH');
   if(j.options.operation==='manual_redaction'&&metrics.manual_redaction_verified!==true)throw new Fault('MANUAL_REDACTION_NOT_VERIFIED',409);
   const bytes=await file.arrayBuffer();if(new TextDecoder().decode(bytes.slice(0,5))!=='%PDF-')throw new Fault('INVALID_PDF');
   const hash=await sha(bytes),key=j.owner+'/'+j.id+'/result-'+hash+'.pdf';await uploadOnce(key,bytes,'application/pdf',hash);
   // Store only bounded scalar evidence; no private filesystem paths or credentials.
   const measured={acceptance_checked:false,evaluation_status:'pending',source_sha256:j.source_sha256};
   for(const k of ['server_seconds','total_seconds','pages','blockers'])if(typeof metrics[k]==='number'&&Number.isFinite(metrics[k])&&metrics[k]>=0)measured[k]=metrics[k];
   measured.gpu_verified=metrics.gpu_verified===true;
   const c=checks(metrics.checks);if(c)measured.checks=c;
   const items=reviewItems(metrics.review_items,measured.pages);if(items.length)measured.review_items=items;
   const drawn=redactionMap(metrics.redaction_map,measured.pages);if(drawn.length)measured.redaction_map=drawn;
   if(j.options.operation==='manual_redaction'){
    measured.manual_redaction_verified=true;measured.manual_regions=j.options.regions.length;
   }
   await sb('/rest/v1/rpc/tx_finish',{method:'POST',body:JSON.stringify({p_job:j.id,p_worker:w.id,p_lease:j.lease,p_artifact:{storage_key:key,filename:j.filename.replace(/\.pdf$/i,'')+'-transformed.pdf',sha256:hash,size:bytes.byteLength},p_metrics:measured})});return json({status:'recorded'});
  }
  if(p.startsWith('/v1/')){
   const u=await profile();
   if(p==='/v1/me'&&method==='GET')return json({name:u.name,role:u.role,csrf:cookie(request,'tx_csrf')});
   if(p==='/v1/workers'&&method==='POST'){
    if(!['admin','mentor','customer'].includes(u.role))throw new Fault('WORKER_REGISTRATION_DENIED',403);
    const b=await body();if(typeof b.name!=='string'||b.name.length<1||b.name.length>100)throw new Fault('INVALID_WORKER_NAME');
    const token=crypto.randomUUID()+crypto.randomUUID();
    const rows=await insert('tx_workers',{owner:u.id,name:b.name,token_hash:await sha(new TextEncoder().encode(token))});
    return json({id:rows[0].id,token,name:b.name,supabase_url:env.SUPABASE_URL},201);
   }
   if(p==='/v1/workers'&&method==='GET'){
    const ws=await db('tx_workers','?owner=eq.'+u.id+'&select=id,name,enabled,heartbeat,capabilities&order=created');
    return json(ws.map(w=>({...w,online:w.enabled&&Date.now()-Date.parse(w.heartbeat)<60000,
      ready:w.enabled&&Date.now()-Date.parse(w.heartbeat)<60000&&w.capabilities?.ready===true})));
   }
   const revoke=p.match(/^\/v1\/workers\/([a-f0-9-]+)\/revoke$/);
   if(revoke&&method==='POST'){
    const ws=await db('tx_workers','?id=eq.'+revoke[1]+'&owner=eq.'+u.id+'&select=id');if(!ws[0])throw new Fault('NOT_FOUND',404);
    const revoked=await sb('/rest/v1/rpc/tx_revoke_worker',{method:'POST',body:JSON.stringify({p_worker:revoke[1],p_owner:u.id})});
    if(!revoked)throw new Fault('WORKER_BUSY_STOP_PROCESSING_FIRST',409);return json({status:'revoked'});
   }
   if(p==='/v1/system'&&method==='GET'){
    const ws=await db('tx_workers','?enabled=eq.true&owner=eq.'+u.id+'&select=heartbeat');
    return json({database:'PostgreSQL · Supabase',worker:ws.some(w=>Date.now()-Date.parse(w.heartbeat)<60000)?'connected':'offline',gpu_verified:false,software_version:'cloud-v1'});
   }
   if(p==='/v1/jobs'&&method==='GET'){
    // Mark this account's silent running jobs interrupted so the page and its
    // worker do not wait forever. Best effort: a database without the
    // 202611120001 migration still lists history.
    try{await sb('/rest/v1/rpc/tx_interrupt_stale',{method:'POST',body:JSON.stringify({p_owner:u.id,p_worker:null,p_grace_seconds:STALE_SECONDS})});}catch{}
    const filter=u.role==='admin'?'':u.role==='mentor'||u.role==='viewer'?'&or=(owner.eq.'+u.id+',baseline.eq.true)':'&owner=eq.'+u.id;
    const jobs=await db('tx_jobs','?select=*&order=created.desc&limit=100'+filter);
    // Keep history within the Workers subrequest budget as records accumulate.
    // Authorize/filter jobs first, then fetch only their public artifact metadata.
    const arts=jobs.length?await db('tx_artifacts','?job_id=in.('+jobs.map(j=>j.id).join(',')+')&kind=neq.manifest&select=job_id,id,kind,filename,sha256,size'):[];
    const grouped=new Map(jobs.map(j=>[j.id,[]]));
    for(const {job_id,...a} of arts)grouped.get(job_id)?.push(a);
    return json(await Promise.all(jobs.map(j=>output(j,grouped.get(j.id)))));
   }
   if(p==='/v1/jobs'&&method==='POST'){
    if(!['admin','mentor','customer'].includes(u.role))throw new Fault('PROCESSING_ROLE_REQUIRED',403);
    const key=request.headers.get('Idempotency-Key');if(!key||!/^[A-Za-z0-9_-]{8,120}$/.test(key))throw new Fault('IDEMPOTENCY_KEY_REQUIRED');
    const f=await request.formData(),file=f.get('file');if(!file||typeof file==='string'||!safeName(file.name)||file.size>MAX||file.size<5)throw new Fault('INVALID_PDF');
    let o;try{o=options(JSON.parse(f.get('options')));}catch(e){if(e instanceof Fault)throw e;throw new Fault('INVALID_OPTIONS');}
    const assigned=o.worker_id;if(!uuid(assigned))throw new Fault('SELECT_GPU_WORKER');
    const ws=await db('tx_workers','?id=eq.'+assigned+'&owner=eq.'+u.id+'&enabled=eq.true&select=id');
    if(!ws[0])throw new Fault('WORKER_NOT_OWNED_OR_DISABLED',403);
    delete o.worker_id;
    const bytes=await file.arrayBuffer();if(new TextDecoder().decode(bytes.slice(0,5))!=='%PDF-')throw new Fault('INVALID_PDF');
    const hash=await sha(bytes),requestHash=await sha(new TextEncoder().encode(JSON.stringify([file.name,hash,o,assigned])));
    let prior=await db('tx_jobs','?owner=eq.'+u.id+'&idempotency_key=eq.'+key);
    if(!prior.length){try{prior=await insert('tx_jobs',{owner:u.id,filename:file.name,source_sha256:hash,options:o,assigned_worker_id:assigned,request_hash:requestHash,idempotency_key:key});}catch(e){if(e.message!=='CONFLICT')throw e;prior=await db('tx_jobs','?owner=eq.'+u.id+'&idempotency_key=eq.'+key);}}
    const j=prior[0];if(!j||j.request_hash!==requestHash)throw new Fault('IDEMPOTENCY_CONFLICT',409);
    if(j.status==='uploading'){
     const storageKey=u.id+'/'+j.id+'/source.pdf';
     const existing=await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=eq.source');
     if(!existing.length){
      // Failed uploads remain non-runnable. Retry the same request/key to recover.
      try{await upload(storageKey,bytes,'application/pdf');}catch(e){
       // Do not trust a pre-existing object's bytes without comparing them.
       const r=await transport(await signed(storageKey));if(!r.ok||await sha(await r.arrayBuffer())!==hash)throw e;
      }
      try{await insert('tx_artifacts',{job_id:j.id,kind:'source',storage_key:storageKey,filename:file.name,sha256:hash,size:file.size});}catch(e){if(e.message!=='CONFLICT')throw e;}
     }
     await patch('tx_jobs','?id=eq.'+j.id+'&status=eq.uploading',{status:'queued',stage:'queued',updated:new Date().toISOString()});
    }
    return json({job_id:j.id,status_url:'/v1/jobs/'+j.id},202);
   }
   const match=p.match(/^\/v1\/jobs\/([^/]+)(?:\/(.*))?$/);
   if(match){
    const j=await job(match[1],u),sub=match[2];
    const manual=await manualRoute({sub,method,j,u,body,db,insert,patch,sb,transport,env,BUCKET,MAX,signed,sha,Fault,json,request});
    if(manual)return manual;
    if(!sub&&method==='GET')return json(await output(j));
    if(sub==='events'&&method==='GET')return json((await db('tx_events','?job_id=eq.'+j.id+'&order=id&limit=500')).map(e=>({...e,created:Date.parse(e.created)/1000})));
    if(sub==='cancel'&&method==='POST'){
     if(j.owner!==u.id&&u.role!=='admin')throw new Fault('OWNER_REQUIRED',403);
     const changed=await patch('tx_jobs','?id=eq.'+j.id+'&status=eq.queued&baseline=eq.false',{status:'cancelled',stage:'cancelled'});if(!changed.length)throw new Fault('ONLY_QUEUED_CANCELLATION_SUPPORTED',409);return json({status:'cancelled'});
    }
    if(sub==='reviews'&&method==='GET')return json(await db('tx_reviews','?job_id=eq.'+j.id+'&select=*'));
    if(sub==='reviews'&&method==='POST'){
     if(!['admin','mentor'].includes(u.role)||j.status!=='succeeded')throw new Fault('REVIEW_NOT_ALLOWED',403);
     const b=await body();if(!['reviewed','requires_revision'].includes(b.decision)||typeof b.note!=='string'||b.note.length<3||b.note.length>4000)throw new Fault('INVALID_REVIEW');
     const arts=await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=eq.result');if(!arts[0])throw new Fault('RESULT_MISSING',409);
     await insert('tx_reviews',{job_id:j.id,reviewer:u.id,output_sha256:arts[0].sha256,decision:b.decision,note:b.note});return json({status:'recorded'});
    }
    if(sub==='report'&&method==='GET'){const r=json(await output(j));r.headers.set('Content-Disposition','attachment; filename="run-report.json"');return r;}
    const a=sub?.match(/^artifacts\/([a-f0-9-]+)(?:\/(url|content))?$/);
    if(a&&method==='GET'){
     const arts=await db('tx_artifacts','?id=eq.'+a[1]+'&job_id=eq.'+j.id+'&kind=neq.manifest');if(!arts[0])throw new Fault('NOT_FOUND',404);
     const link=await signed(arts[0].storage_key);
     if(a[2]==='content'){
      const upstream=await transport(link);if(!upstream.ok)throw new Fault('PREVIEW_UNAVAILABLE',502);
      return new Response(upstream.body,{headers:{'Content-Type':'application/pdf','Cache-Control':'no-store','Content-Disposition':'inline'}});
     }
     return sub.endsWith('/url')?json({url:link}):Response.redirect(link,302);
    }
   }
   if(p==='/v1/baselines'&&method==='GET')return json([]);
   throw new Fault('ENDPOINT_NOT_IMPLEMENTED',404);
  }
  if(!env.ASSETS)throw new Fault('FRONTEND_NOT_BUILT',503);
  // Explicit document routes pair with html_handling:none. Automatic HTML
  // canonicalization would redirect /studio.html back into /studio forever.
  const documents={'/':'/index.html','/workspace':'/workspace.html','/workspace/':'/workspace.html','/studio':'/studio.html','/studio/':'/studio.html'};
  return env.ASSETS.fetch(documents[p]?new Request(new URL(documents[p],url),request):request);
 }catch(e){return json({error:e instanceof Fault?e.message:'INTERNAL_ERROR'},e instanceof Fault?e.status:500);}
 };
}
const handle=createHandler();
export default {async fetch(request,env){
 const response=await handle(request,env),r=new Response(response.body,response);
 r.headers.set('X-Content-Type-Options','nosniff');r.headers.set('Referrer-Policy','no-referrer');
 const path=new URL(request.url).pathname.replace(/\/$/,'')||'/';
 const page=path==='/index.html'?'/':path.replace(/\.html$/,'');
 const hashes=frontendCsp[page];
 const editor=path==='/redaction-review.html';
 r.headers.set('Content-Security-Policy',`default-src 'self'; script-src 'self'${editor?" 'wasm-unsafe-eval'":''}${hashes?.length?' '+hashes.join(' '):''}; style-src 'self'; ${hashes||editor?"style-src-attr 'unsafe-inline'; ":''}img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; frame-src ${env.SUPABASE_URL}; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'`);
 if(new URL(request.url).pathname.startsWith('/v1/'))r.headers.set('Cache-Control','no-store');return r;
}};
