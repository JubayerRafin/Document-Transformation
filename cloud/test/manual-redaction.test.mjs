import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler,options,Fault,sha} from '../src/index.mjs';
import {regions} from '../src/manual-redaction.mjs';
const me='11111111-1111-4111-8111-111111111111',parent='22222222-2222-4222-8222-222222222222',aid='33333333-3333-4333-8333-333333333333',wid='44444444-4444-4444-8444-444444444444';
const other='55555555-5555-4555-8555-555555555555',region={page:1,x:.1,y:.2,width:.3,height:.1};
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',SUPABASE_SECRET_KEY:'secret'};
const response=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
async function fixture({role='customer',owner=me,capable=true,online=true}={}){
 const bytes=new TextEncoder().encode('%PDF-synthetic'),hash=await sha(bytes),calls=[];
 const tables={tx_jobs:[{id:parent,owner,status:'succeeded',filename:'synthetic.pdf',source_sha256:'f'.repeat(64),options:{operation:'pii'},metrics:{},created:new Date().toISOString(),updated:new Date().toISOString()}],tx_artifacts:[{id:aid,job_id:parent,kind:'result',storage_key:owner+'/'+parent+'/result.pdf',filename:'result.pdf',sha256:hash,size:bytes.length}],tx_workers:[{id:wid,owner:me,enabled:true,heartbeat:new Date(Date.now()-(online?0:120000)).toISOString(),capabilities:{ready:true,operations:capable?['pii','manual_redaction']:['pii']}}]};
 const handle=createHandler(async(url,init={})=>{
  calls.push([url,init]);const u=new URL(url);
  if(u.pathname==='/auth/v1/user')return response({id:me,email:'synthetic@example.invalid'});
  if(u.pathname.endsWith('/tx_profiles'))return response([{id:me,role,active:true}]);
  if(u.pathname.startsWith('/storage/')){
   if(u.pathname.includes('/sign/'))return response({signedURL:'/object/signed/private'});
   if(u.pathname.endsWith('/copy'))return response({Key:'copied'});
   return new Response(bytes,{headers:{'Content-Type':'application/pdf'}});
  }
  const name=u.pathname.split('/').at(-1),table=tables[name];if(!table)throw Error('Unexpected '+u.pathname);
  const matches=row=>[...u.searchParams].every(([k,v])=>!v.startsWith('eq.')||String(row[k])===v.slice(3));
  if(init.method==='POST'){const body=JSON.parse(init.body);const row={id:crypto.randomUUID(),status:'uploading',metrics:{},created:new Date().toISOString(),updated:new Date().toISOString(),...body};table.push(row);return response([row]);}
  const rows=table.filter(matches);
  if(init.method==='PATCH'){rows.forEach(r=>Object.assign(r,JSON.parse(init.body)));}
  return response(rows);
 });
 const req=(path,init={})=>handle(new Request('https://app.example'+path,{...init,headers:{Cookie:'tx_access=a; tx_csrf=c','X-CSRF-Token':'c',...init.headers}}),env);
 const create=()=>req('/v1/jobs/'+parent+'/redactions',{method:'POST',headers:{'Idempotency-Key':'synthetic-key-123'},body:JSON.stringify({artifact_id:aid,source_sha256:hash})});
 return {req,create,tables,calls,hash};
}
test('manual drafts are private non-runnable child versions, never updates to the original job',async()=>{
 const f=await fixture(),original=structuredClone(f.tables.tx_jobs[0]);const r=await f.create();assert.equal(r.status,201);const {job_id}=await r.json();
 assert.deepEqual(f.tables.tx_jobs[0],original);const d=f.tables.tx_jobs.find(j=>j.id===job_id);assert.equal(d.status,'uploading');assert.equal(d.options.operation,'manual_redaction');assert.equal(d.source_sha256,f.hash);
 assert.equal(f.calls.some(c=>c[0].includes('/copy')),false);
 const read=await f.req('/v1/jobs/'+job_id+'/redaction-draft');assert.equal((await read.json()).parent_artifact_id,aid);
});
test('create retry with the same key returns the same draft',async()=>{
 const f=await fixture(),a=await (await f.create()).json(),b=await (await f.create()).json();assert.equal(a.job_id,b.job_id);assert.equal(f.tables.tx_jobs.length,2);
});
test('viewer and even admin cannot edit another owners result',async()=>{
 for(const settings of [{role:'viewer'},{role:'admin',owner:other},{owner:other}]){const f=await fixture(settings),r=await f.create();assert.ok([403,404].includes(r.status));assert.equal(f.tables.tx_jobs.length,1);}
});
test('create requires exact result artifact and hash, plus idempotency key',async()=>{
 const f=await fixture();for(const headers of [{}, {'Idempotency-Key':'okay-key-123'}]){const r=await f.req('/v1/jobs/'+parent+'/redactions',{method:'POST',headers,body:JSON.stringify({artifact_id:aid,source_sha256:'0'.repeat(64)})});assert.ok([400,409].includes(r.status));}assert.equal(f.tables.tx_jobs.length,1);
});
test('draft saves use optimistic revision and immutable version binding',async()=>{
 const f=await fixture(),{job_id}=await (await f.create()).json(),d=f.tables.tx_jobs[1],revision=d.request_hash;
 const r=await f.req('/v1/jobs/'+job_id+'/redaction-draft',{method:'PUT',body:JSON.stringify({revision,regions:[region]})});assert.equal(r.status,200);assert.notEqual((await r.json()).revision,revision);assert.deepEqual(d.options.regions,[region]);
 const stale=await f.req('/v1/jobs/'+job_id+'/redaction-draft',{method:'PUT',body:JSON.stringify({revision,regions:[]})});assert.equal(stale.status,409);assert.deepEqual(d.options.regions,[region]);
});
test('save can clear regions but finalize cannot run an empty plan',async()=>{
 const f=await fixture(),{job_id}=await (await f.create()).json(),d=f.tables.tx_jobs[1];const r=await f.req('/v1/jobs/'+job_id+'/redaction-finalize',{method:'POST',body:JSON.stringify({revision:d.request_hash,worker_id:wid})});assert.equal(r.status,400);assert.equal(d.status,'uploading');
});
test('offline and older workers cannot receive manual jobs',async()=>{
 for(const settings of [{capable:false},{online:false}]){const f=await fixture(settings),{job_id}=await (await f.create()).json(),d=f.tables.tx_jobs[1];d.options.regions=[region];const r=await f.req('/v1/jobs/'+job_id+'/redaction-finalize',{method:'POST',body:JSON.stringify({revision:d.request_hash,worker_id:wid})});assert.equal(r.status,409);assert.equal(d.status,'uploading');}
});
test('finalize copies the exact version then queues only the assigned capable worker; retry does not duplicate',async()=>{
 const f=await fixture(),{job_id}=await (await f.create()).json(),d=f.tables.tx_jobs[1];d.options.regions=[region];const request={method:'POST',body:JSON.stringify({revision:d.request_hash,worker_id:wid})};
 assert.equal((await f.req('/v1/jobs/'+job_id+'/redaction-finalize',request)).status,202);
 assert.equal(d.status,'queued');assert.equal(d.assigned_worker_id,wid);
 const copied=JSON.parse(f.calls.find(c=>c[0].endsWith('/copy'))[1].body);assert.equal(copied.sourceKey,f.tables.tx_artifacts[0].storage_key);assert.equal(copied.destinationKey,me+'/'+job_id+'/source.pdf');
 assert.equal((await f.req('/v1/jobs/'+job_id+'/redaction-finalize',request)).status,200);
 assert.equal(f.calls.filter(c=>c[0].endsWith('/copy')).length,1);
 assert.equal((await f.req('/v1/jobs/'+job_id+'/redaction-draft',{method:'PUT',body:JSON.stringify({revision:d.request_hash,regions:[]})})).status,409);
});
test('content proxy is session-authorized and serves no-store PDF bytes',async()=>{
 const f=await fixture(),r=await f.req('/v1/jobs/'+parent+'/artifacts/'+aid+'/content');assert.equal(r.status,200);assert.equal(r.headers.get('Cache-Control'),'no-store');assert.equal(r.headers.get('Content-Type'),'application/pdf');assert.match(await r.text(),/^%PDF/);
 const denied=await fixture({owner:other});assert.equal((await denied.req('/v1/jobs/'+parent+'/artifacts/'+aid+'/content')).status,404);
});
test('region schema rejects floats for page numbers, NaN, overflow, unknown fields and excessive plans',()=>{
 for(const bad of [[{...region,page:1.5}],[{...region,x:NaN}],[{...region,x:.9}],[{...region,width:0}],[{...region,text:'secret'}],Array(501).fill(region)])assert.throws(()=>regions(bad,Fault),/INVALID_REDACTION/);
 assert.deepEqual(regions([region],Fault),[region]);assert.throws(()=>options({operation:'manual_redaction'}),/INVALID_OPERATION/);
});
