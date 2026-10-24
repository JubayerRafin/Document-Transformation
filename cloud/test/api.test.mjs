import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler,options,sha} from '../src/index.mjs';
const owner='11111111-1111-4111-8111-111111111111',other='22222222-2222-4222-8222-222222222222',jobId='33333333-3333-4333-8333-333333333333';
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-test',SUPABASE_SECRET_KEY:'private-test'};
const resp=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'Content-Type':'application/json'}});
const req=(path,init={})=>new Request('https://transformx.example'+path,init);
function fixture({role='customer',jobs=[],active=true}={}){
 const calls=[];
 const handle=createHandler(async(url,init={})=>{
  calls.push([url,init]);const u=new URL(url),table=u.pathname.split('/').at(-1);
  if(u.pathname==='/auth/v1/user')return resp({id:owner,email:'test@example.com'});
  if(table==='tx_profiles')return resp([{id:owner,role,active}]);
  if(table==='tx_jobs')return resp(jobs);
  if(table==='tx_artifacts'||table==='tx_workers'||table==='tx_reviews')return resp([]);
  throw Error('Unexpected database request '+u.pathname);
 });
 const auth=(path,init={})=>handle(req(path,{...init,headers:{Cookie:'tx_access=session; tx_csrf=csrf','X-CSRF-Token':'csrf',...init.headers}}),env);
 return {handle,auth,calls};
}
test('public health works without a privileged key; reports configuration honestly',async()=>{
 const h=createHandler(()=>{throw Error('Network must not run');});const r=await h(req('/health/live'),{...env,SUPABASE_SECRET_KEY:undefined});assert.equal(r.status,200);assert.equal((await r.json()).configured,false);
});
test('private API rejects missing session before accessing database',async()=>{
 const f=fixture();assert.equal((await f.handle(req('/v1/jobs'),env)).status,401);assert.equal(f.calls.length,0);
});
test('forged cross-origin mutation is denied before authentication',async()=>{
 const f=fixture();assert.equal((await f.auth('/v1/jobs',{method:'POST',headers:{Origin:'https://attacker.example'}})).status,403);assert.equal(f.calls.length,0);
});
test('missing CSRF token cannot cancel a job',async()=>{
 const f=fixture();const r=await f.handle(req('/v1/jobs/'+jobId+'/cancel',{method:'POST',headers:{Cookie:'tx_access=a; tx_csrf=csrf'}}),env);assert.equal(r.status,403);assert.equal(f.calls.length,0);
});
test('JSON login errors remain controlled',async()=>{
 const r=await fixture().handle(req('/v1/session',{method:'POST',body:'invalid'}),env);assert.equal(r.status,400);assert.equal((await r.json()).error,'INVALID_JSON');
});
test('inactive account has no access',async()=>assert.equal((await fixture({active:false}).auth('/v1/me')).status,403));
test('viewer cannot register a GPU worker',async()=>{
 const f=fixture({role:'viewer'});const r=await f.auth('/v1/workers',{method:'POST',body:JSON.stringify({name:'gpu'})});assert.equal(r.status,403);assert.equal(f.calls.filter(c=>c[1].method==='POST').length,0);
});
test('customer cannot read another owner job',async()=>{
 const f=fixture({jobs:[{id:jobId,owner:other,baseline:false}]});assert.equal((await f.auth('/v1/jobs/'+jobId)).status,404);
});
test('mentor shared access requires a baseline',async()=>{
 const f=fixture({role:'mentor',jobs:[{id:jobId,owner:other,baseline:false}]});assert.equal((await f.auth('/v1/jobs/'+jobId)).status,404);
});
test('job output hides lease, request digest and idempotency key',async()=>{
 const f=fixture({jobs:[{id:jobId,owner,metrics:{},lease:'private',request_hash:'private',idempotency_key:'private',created:new Date().toISOString(),updated:new Date().toISOString()}]});
 const r=await f.auth('/v1/jobs/'+jobId);assert.equal(r.status,200);const body=await r.json();for(const k of ['lease','request_hash','idempotency_key'])assert.equal(k in body,false);
});
test('uploads reject injected execution settings',()=>{
 assert.throws(()=>options({execution_remote:{host:'attacker'}}),/UNKNOWN_OPTION/);assert.throws(()=>options({engine_python:'/tmp/evil'}),/UNKNOWN_OPTION/);
});
test('PII must select supported categories; context/protection bounded',()=>{
 assert.throws(()=>options({operation:'pii'}),/INVALID_PII/);assert.throws(()=>options({pii_categories:['unsupported']}),/INVALID_PII/);
 assert.throws(()=>options({document_context:'x'.repeat(8001)}),/INVALID_CONTEXT/);assert.throws(()=>options({protected_values:['x'.repeat(501)]}),/INVALID_PROTECTED/);
});
test('PDF-only operation and language constraints are enforced',()=>{
 assert.throws(()=>options({output_format:'docx'}),/INVALID_OPERATION/);assert.throws(()=>options({source_language:'en&owner=eq.bad'}),/INVALID_LANGUAGE/);
 assert.equal(options({}).target_language,'ko');
});
test('viewer cannot upload',async()=>{
 const f=fixture({role:'viewer'});assert.equal((await f.auth('/v1/jobs',{method:'POST'})).status,403);
});
test('job submission requires a safe idempotency key',async()=>{
 const f=fixture();assert.equal((await f.auth('/v1/jobs',{method:'POST'})).status,400);
});
test('cancel uses conditional queued-state update so a claimed job cannot be overwritten',async()=>{
 const f=fixture({jobs:[{id:jobId,owner,status:'queued'}]});await f.auth('/v1/jobs/'+jobId+'/cancel',{method:'POST'});
 const call=f.calls.find(c=>c[1].method==='PATCH');assert.match(call[0],/status=eq.queued/);assert.match(call[0],/baseline=eq.false/);
});
test('worker claims require worker credentials rather than a user session',async()=>{
 const f=fixture();assert.equal((await f.auth('/worker/claim',{method:'POST'})).status,401);
});
test('result credentials cannot appear in error messages',async()=>{
 const f=fixture();const r=await f.handle(req('/v1/jobs',{headers:{Cookie:'tx_access=a'}}),{...env,SUPABASE_SECRET_KEY:undefined});const text=await r.text();assert.equal(r.status,503);assert.doesNotMatch(text,/private-test|session/);
});
test('content-length rejects large request before contacting Supabase',async()=>{
 const f=fixture();assert.equal((await f.handle(req('/worker/claim',{method:'POST',headers:{'Content-Length':'51000000'}}),env)).status,413);assert.equal(f.calls.length,0);
});
test('SHA256 uses actual bytes',async()=>assert.equal(await sha(new TextEncoder().encode('abc')),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'));
