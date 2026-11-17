import test from 'node:test';
import assert from 'node:assert/strict';
import {createHandler,glossaryEntries,Fault} from '../src/index.mjs';
const me='11111111-1111-4111-8111-111111111111',wid='44444444-4444-4444-8444-444444444444',jid='55555555-5555-4555-8555-555555555555';
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-test',SUPABASE_SECRET_KEY:'private-test'};
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
const ENTRIES=[{source:'redaction',target:'Schwärzung'},{source:'Title',target:'Position'}];
function fixture({role='customer',glossary=ENTRIES,glossaryFails=false}={}){
 const calls=[];
 const handle=createHandler(async(url,init={})=>{
  calls.push([url,init]);const u=new URL(url),table=u.pathname.split('/').at(-1);
  if(u.pathname==='/auth/v1/user')return json({id:me,email:'user@example.com'});
  if(table==='tx_profiles')return json([{id:me,role,active:true}]);
  if(table==='tx_glossaries'){
   if(glossaryFails)return new Response('{}',{status:404});
   if(init.method==='POST')return json([{...JSON.parse(init.body)}]);
   return json(glossary?[{source_language:'en',target_language:'de',entries:glossary,updated:'2026-11-17T10:00:00Z'}]:[]);
  }
  if(table==='tx_workers')return json([{id:wid}]);
  if(table==='tx_jobs'){
   if(init.method==='POST'){const row=JSON.parse(init.body);return json([{id:jid,status:'queued',...row}]);}
   return json([]);
  }
  throw Error('Unexpected request '+url);
 });
 const auth=(p,init={})=>handle(new Request('https://app.example'+p,{...init,headers:{Cookie:'tx_access=a; tx_csrf=c','X-CSRF-Token':'c',...init.headers}}),env);
 return {calls,auth};
}
const put=(f,b)=>f.auth('/v1/glossary',{method:'PUT',body:JSON.stringify(b)});

test('entries are trimmed, limited and unique regardless of case',()=>{
 assert.deepEqual(glossaryEntries([{source:' redaction ',target:' Schwärzung '}]),[{source:'redaction',target:'Schwärzung'}]);
 for(const bad of [[{source:'',target:'x'}],[{source:'a',target:''}],[{source:'a\nb',target:'x'}],[{source:'---',target:'x'}],
   Array.from({length:101},(_,i)=>({source:'t'+i,target:'x'})),[{source:'x'.repeat(201),target:'y'}],'nope',[['a','b']]])
  assert.throws(()=>glossaryEntries(bad),Fault);
 assert.throws(()=>glossaryEntries([{source:'Title',target:'A'},{source:'title',target:'B'}]),/DUPLICATE_GLOSSARY_TERM/);
});

test('a customer saves a glossary for one language pair, stored only under their account',async()=>{
 const f=fixture(),r=await put(f,{source_language:'en',target_language:'de',entries:ENTRIES});
 assert.equal(r.status,200);
 const [url,init]=f.calls.find(c=>c[0].includes('tx_glossaries')&&c[1].method==='POST');
 assert.ok(url.includes('on_conflict=owner,source_language,target_language'));
 const row=JSON.parse(init.body);assert.equal(row.owner,me);assert.deepEqual(row.entries,ENTRIES);
 assert.equal((await r.json()).entries.length,2);
});

test('invalid pairs, invalid entries and read-only accounts are refused',async()=>{
 assert.equal((await put(fixture(),{source_language:'en',target_language:'en',entries:[]})).status,400);
 assert.equal((await put(fixture(),{source_language:'en',target_language:'de',entries:[{source:'',target:'x'}]})).status,400);
 const viewer=await put(fixture({role:'viewer'}),{source_language:'en',target_language:'de',entries:ENTRIES});
 assert.equal(viewer.status,403);
});

test('reading a glossary is limited to the signed-in account',async()=>{
 const f=fixture(),r=await f.auth('/v1/glossary?source=en&target=de');assert.equal(r.status,200);
 assert.deepEqual((await r.json()).entries,ENTRIES);
 assert.ok(f.calls.find(c=>c[0].includes('tx_glossaries'))[0].includes('owner=eq.'+me));
 assert.equal((await fixture().auth('/v1/glossary?source=en&target=x;drop')).status,400);
 const list=await (await fixture().auth('/v1/glossaries')).json();
 assert.deepEqual(list,[{source_language:'en',target_language:'de',terms:2,updated:Date.parse('2026-11-17T10:00:00Z')/1000}]);
});

function submit(f,options){
 const form=new FormData();form.append('file',new Blob(['%PDF-1.7 test'],{type:'application/pdf'}),'doc.pdf');
 form.append('options',JSON.stringify({worker_id:wid,...options}));
 return f.auth('/v1/jobs',{method:'POST',headers:{'Idempotency-Key':'glossary-key-1'},body:form});
}
const storedJob=f=>JSON.parse(f.calls.find(c=>c[0].includes('tx_jobs')&&c[1].method==='POST')[1].body);

test('a translation job carries the matching glossary, attached by the server',async()=>{
 const f=fixture();assert.equal((await submit(f,{operation:'translation',target_language:'de'})).status,202);
 assert.deepEqual(storedJob(f).options.terminology,{redaction:'Schwärzung',Title:'Position'});
});

test('a browser cannot send terminology itself, and PII-only jobs get none',async()=>{
 assert.equal((await submit(fixture(),{operation:'translation',target_language:'de',terminology:{a:'b'}})).status,400);
 const f=fixture();await submit(f,{operation:'pii',pii_categories:['Names']});
 assert.equal('terminology' in storedJob(f).options,false);
});

test('a job still runs without a glossary or when the glossary table is not there yet',async()=>{
 for(const f of [fixture({glossary:null}),fixture({glossaryFails:true})]){
  assert.equal((await submit(f,{operation:'translation',target_language:'de'})).status,202);
  assert.equal('terminology' in storedJob(f).options,false);
 }
});

test('editing the glossary does not change the request hash of a retried upload',async()=>{
 const a=fixture(),b=fixture({glossary:[{source:'redaction',target:'Anonymisierung'}]});
 await submit(a,{operation:'translation',target_language:'de'});await submit(b,{operation:'translation',target_language:'de'});
 assert.equal(storedJob(a).request_hash,storedJob(b).request_hash);
});
