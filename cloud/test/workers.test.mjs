import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler,sha} from '../src/index.mjs';
const me='11111111-1111-4111-8111-111111111111',other='22222222-2222-4222-8222-222222222222',wid='44444444-4444-4444-8444-444444444444';
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-test',SUPABASE_SECRET_KEY:'private-test'};
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
function fixture({role='customer',owned=true,revoked=true}={}){
 const calls=[];
 const handle=createHandler(async(url,init={})=>{
  calls.push([url,init]);const u=new URL(url),table=u.pathname.split('/').at(-1);
  if(u.pathname==='/auth/v1/user')return json({id:me,email:'user@example.com'});
  if(table==='tx_profiles')return json([{id:me,role,active:true}]);
  if(table==='tx_workers'){
   if(init.method==='POST')return json([{id:wid,...JSON.parse(init.body)}]);
   return json(owned?[{id:wid,name:'University GPU',enabled:true,heartbeat:new Date().toISOString(),capabilities:{ready:true,operations:['translation'],gpu:'NVIDIA GPU'}}]:[]);
  }
  if(table==='tx_revoke_worker')return json(revoked);
  if(table==='tx_jobs')return json([]);
  throw Error('Unexpected request '+url);
 });
 const auth=(p,init={})=>handle(new Request('https://app.example'+p,{...init,headers:{Cookie:'tx_access=a; tx_csrf=c','X-CSRF-Token':'c',...init.headers}}),env);
 return {calls,auth};
}
test('customer can register a private worker; only token hash is stored',async()=>{
 const f=fixture();const r=await f.auth('/v1/workers',{method:'POST',body:JSON.stringify({name:'RunPod'})});assert.equal(r.status,201);
 const b=await r.json(),stored=JSON.parse(f.calls.find(c=>c[1].method==='POST')[1].body);
 assert.equal(stored.owner,me);assert.equal(stored.token_hash,await sha(new TextEncoder().encode(b.token)));assert.equal('token' in stored,false);
 assert.equal(b.supabase_url,env.SUPABASE_URL);
});
test('even administrator worker discovery is limited to their own servers',async()=>{
 const f=fixture({role:'admin'});const r=await f.auth('/v1/workers');assert.equal(r.status,200);
 const call=f.calls.find(c=>c[0].includes('/tx_workers'));assert.ok(call[0].includes('owner=eq.'+me));assert.ok(!call[0].includes('token_hash'));
 assert.equal((await r.json())[0].ready,true);
});
test('worker listing does not grant customer access to another account',async()=>{
 const f=fixture({owned:false});assert.deepEqual(await (await f.auth('/v1/workers')).json(),[]);
});
test('worker selection is mandatory before any PDF is stored',async()=>{
 const f=fixture(),form=new FormData();form.append('file',new Blob(['%PDF-test'],{type:'application/pdf'}),'test.pdf');form.append('options',JSON.stringify({}));
 const r=await f.auth('/v1/jobs',{method:'POST',headers:{'Idempotency-Key':'test-key-123'},body:form});assert.equal(r.status,400);assert.equal((await r.json()).error,'SELECT_GPU_WORKER');
 assert.equal(f.calls.some(c=>c[0].includes('/storage/')),false);
});
test('another users worker cannot be selected, including by an administrator',async()=>{
 const f=fixture({role:'admin',owned:false}),form=new FormData();form.append('file',new Blob(['%PDF-test'],{type:'application/pdf'}),'test.pdf');form.append('options',JSON.stringify({worker_id:wid}));
 const r=await f.auth('/v1/jobs',{method:'POST',headers:{'Idempotency-Key':'test-key-123'},body:form});assert.equal(r.status,403);assert.equal((await r.json()).error,'WORKER_NOT_OWNED_OR_DISABLED');
 const call=f.calls.find(c=>c[0].includes('/tx_workers'));assert.ok(call[0].includes('owner=eq.'+me));assert.equal(f.calls.some(c=>c[1].method==='POST'),false);
});
test('worker revocation cannot target another account',async()=>{
 const f=fixture({owned:false});assert.equal((await f.auth('/v1/workers/'+wid+'/revoke',{method:'POST'})).status,404);assert.equal(f.calls.some(c=>c[0].includes('/rpc/')),false);
});
test('busy worker revocation is refused by the atomic RPC',async()=>{
 const f=fixture({revoked:false});assert.equal((await f.auth('/v1/workers/'+wid+'/revoke',{method:'POST'})).status,409);
 const rpc=f.calls.find(c=>c[0].includes('/rpc/tx_revoke_worker'));assert.deepEqual(JSON.parse(rpc[1].body),{p_worker:wid,p_owner:me});
});
test('idle owned worker can be revoked',async()=>{
 const f=fixture();assert.equal((await f.auth('/v1/workers/'+wid+'/revoke',{method:'POST'})).status,200);
});
