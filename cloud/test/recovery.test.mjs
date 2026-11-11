import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler,sha,checks,FAILURE_CODES} from '../src/index.mjs';
const owner='11111111-1111-4111-8111-111111111111',wid='44444444-4444-4444-8444-444444444444',jid='33333333-3333-4333-8333-333333333333';
const token='t'.repeat(64),lease='lease-1';
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-test',SUPABASE_SECRET_KEY:'private-test'};
const json=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'Content-Type':'application/json'}});
function matches(row,params){
 for(const [k,v] of params){
  if(['select','order','limit'].includes(k))continue;
  if(v.startsWith('eq.')&&String(row[k])!==v.slice(3))return false;
  if(v.startsWith('neq.')&&String(row[k])===v.slice(4))return false;
 }
 return true;
}
async function fixture({jobs=[],artifacts=[],claim=[],stored=null,uploadStatus=200,sweepStatus=200}={}){
 const state={jobs,artifacts,calls:[],events:[]};const tokenHash=await sha(new TextEncoder().encode(token));
 const handle=createHandler(async(url,init={})=>{
  state.calls.push([url,init]);const u=new URL(url),path=u.pathname,method=init.method||'GET';
  if(path==='/auth/v1/user')return json({id:owner,email:'owner@example.com'});
  if(path.startsWith('/storage/v1/object/sign/'))return method==='POST'?json({signedURL:'/object/sign/stored?token=x'}):(stored?new Response(stored):new Response('missing',{status:404}));
  if(path.startsWith('/storage/v1/object/'))return new Response('{}',{status:uploadStatus});
  if(path==='/rest/v1/rpc/tx_claim')return json(claim);
  if(path==='/rest/v1/rpc/tx_finish')return new Response(null,{status:204});
  if(path==='/rest/v1/rpc/tx_interrupt_stale')return sweepStatus===200?json(0):json({message:'missing function'},sweepStatus);
  const table=path.split('/').at(-1),params=[...u.searchParams];
  if(table==='tx_workers')return json(method==='PATCH'?[]:[{id:wid,owner,enabled:true,token_hash:tokenHash}]);
  if(table==='tx_profiles')return json([{id:owner,role:'customer',active:true}]);
  if(table==='tx_events'){state.events.push(JSON.parse(init.body));return json([{}]);}
  if(table==='tx_artifacts')return json(state.artifacts.filter(r=>matches(r,params)));
  if(table==='tx_jobs'){
   const rows=state.jobs.filter(r=>matches(r,params));
   if(method==='PATCH'){const body=JSON.parse(init.body);rows.forEach(r=>Object.assign(r,body));}
   return json(rows);
  }
  throw Error('Unexpected request '+url);
 });
 const worker=(p,init={})=>handle(new Request('https://app.example'+p,{method:'POST',...init,headers:{'X-Worker-ID':wid,Authorization:'Bearer '+token,'X-Job-Lease':lease,...init.headers}}),env);
 const user=(p,init={})=>handle(new Request('https://app.example'+p,{...init,headers:{Cookie:'tx_access=a; tx_csrf=c','X-CSRF-Token':'c',...init.headers}}),env);
 return {state,worker,user};
}
const running=()=>({id:jid,owner,worker_id:wid,status:'running',stage:'translation',lease,source_sha256:'a'.repeat(64),filename:'doc.pdf',options:{operation:'pii'},metrics:{}});
const resultForm=async(bytes,metrics)=>{const f=new FormData();f.append('metrics',JSON.stringify(metrics));f.append('file',new Blob([bytes],{type:'application/pdf'}),'result.pdf');return f;};

test('failure reports record a known reason code and an event',async()=>{
 const f=await fixture({jobs:[running()]});
 const r=await f.worker('/worker/jobs/'+jid+'/fail',{body:JSON.stringify({code:'REDACTION_REVIEW_REQUIRED'})});
 assert.equal(r.status,200);assert.equal(f.state.jobs[0].status,'failed');assert.equal(f.state.jobs[0].error_code,'REDACTION_REVIEW_REQUIRED');
 assert.deepEqual(f.state.events,[{job_id:jid,stage:'failed'}]);
});
test('unknown or missing failure codes fall back to ENGINE_FAILED (older workers send {})',async()=>{
 for(const body of ['{}',JSON.stringify({code:'DROP TABLE'}),JSON.stringify({code:'Selected value jane@example.com remains'})]){
  const f=await fixture({jobs:[running()]});assert.equal((await f.worker('/worker/jobs/'+jid+'/fail',{body})).status,200);
  assert.equal(f.state.jobs[0].error_code,'ENGINE_FAILED');
 }
});
test('a repeated result upload after a lost response is acknowledged, not rejected',async()=>{
 const bytes=new TextEncoder().encode('%PDF-result'),hash=await sha(bytes);
 const done={...running(),status:'succeeded'};
 const f=await fixture({jobs:[done],artifacts:[{job_id:jid,kind:'result',sha256:hash}]});
 const r=await f.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(bytes,{source_sha256:done.source_sha256})});
 assert.equal(r.status,200);assert.equal((await r.json()).replayed,true);
 assert.equal(f.state.calls.some(c=>c[0].includes('/rpc/tx_finish')),false);
});
test('a replay with different bytes or another lease is still a conflict',async()=>{
 const f=await fixture({jobs:[{...running(),status:'succeeded'}],artifacts:[{job_id:jid,kind:'result',sha256:'b'.repeat(64)}]});
 const r=await f.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(new TextEncoder().encode('%PDF-other'),{})});
 assert.equal(r.status,409);
 const g=await fixture({jobs:[{...running(),status:'succeeded',lease:'other-lease'}]});
 assert.equal((await g.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(new TextEncoder().encode('%PDF-x'),{})})).status,409);
});
test('an interrupted job rejects heartbeats so the worker stops processing',async()=>{
 const f=await fixture({jobs:[{...running(),status:'interrupted'}]});
 assert.equal((await f.worker('/worker/jobs/'+jid+'/heartbeat',{body:JSON.stringify({stage:'x'})})).status,409);
});
test('result retry accepts an existing storage object only when its bytes match',async()=>{
 const bytes=new TextEncoder().encode('%PDF-result');
 const ok=await fixture({jobs:[running()],uploadStatus:400,stored:bytes});
 const r=await ok.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(bytes,{source_sha256:'a'.repeat(64)})});
 assert.equal(r.status,200);assert.ok(ok.state.calls.some(c=>c[0].includes('/rpc/tx_finish')));
 const bad=await fixture({jobs:[running()],uploadStatus:400,stored:new TextEncoder().encode('%PDF-tampered')});
 const s=await bad.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(bytes,{source_sha256:'a'.repeat(64)})});
 assert.equal(s.status,502);assert.equal(bad.state.calls.some(c=>c[0].includes('/rpc/tx_finish')),false);
});
test('result metrics keep only bounded check counts',async()=>{
 const bytes=new TextEncoder().encode('%PDF-result');
 const f=await fixture({jobs:[running()]});
 const metrics={source_sha256:'a'.repeat(64),blockers:3,checks:{acceptable:true,geometry_blockers:2,uninspected_content:1,privacy_verification:'passed',text:'jane@example.com',known_sensitive_residuals:-4}};
 assert.equal((await f.worker('/worker/jobs/'+jid+'/result',{body:await resultForm(bytes,metrics)})).status,200);
 const finish=JSON.parse(f.state.calls.find(c=>c[0].includes('/rpc/tx_finish'))[1].body);
 assert.equal(finish.p_metrics.blockers,3);
 assert.deepEqual(finish.p_metrics.checks,{acceptable:true,privacy_verification:'passed',known_sensitive_residuals:0,geometry_blockers:2,
  layout_review_candidates:0,uninspected_content:1,scan_acceptance_blockers:0,translation_errors:0});
 assert.doesNotMatch(JSON.stringify(finish),/jane@example/);
});
test('a claimed job without a stored source fails instead of blocking the worker',async()=>{
 const f=await fixture({jobs:[running()],claim:[running()]});
 const r=await f.worker('/worker/claim');assert.deepEqual(await r.json(),{job:null});
 assert.equal(f.state.jobs[0].status,'failed');assert.equal(f.state.jobs[0].error_code,'INPUT_REJECTED');
});
test('history marks this account\'s silent jobs interrupted before listing',async()=>{
 const f=await fixture();assert.equal((await f.user('/v1/jobs')).status,200);
 const sweep=f.state.calls.find(c=>c[0].includes('/rpc/tx_interrupt_stale'));
 assert.deepEqual(JSON.parse(sweep[1].body),{p_owner:owner,p_worker:null,p_grace_seconds:900});
});
test('history still loads before the recovery migration is applied',async()=>{
 const f=await fixture({sweepStatus:404});assert.equal((await f.user('/v1/jobs')).status,200);
});
test('check sanitizer rejects non-objects and invalid values',()=>{
 assert.equal(checks(null),null);assert.equal(checks([1]),null);
 const c=checks({acceptable:'yes',privacy_verification:'maybe',translation_errors:1.5});
 assert.equal(c.acceptable,false);assert.equal(c.privacy_verification,'not_run');assert.equal(c.translation_errors,0);
});
test('worker and API agree on reportable failure codes',async()=>{
 const {readFile}=await import('node:fs/promises');
 const source=await readFile(new URL('../../tools/service/cloud_worker.py',import.meta.url),'utf8');
 const block=source.match(/FAILURE_CODES=\{([^}]*)\}/)[1];
 assert.deepEqual([...block.matchAll(/'([A-Z_]+)'/g)].map(m=>m[1]).sort(),[...FAILURE_CODES].sort());
});
