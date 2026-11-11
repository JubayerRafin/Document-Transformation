// Explicit local synthetic test server, not imported by the test runner or build.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHandler,sha} from '../src/index.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),dir=path.join(root,'output/manual-redaction-checks/browser');
await mkdir(dir,{recursive:true});
const python=path.join(root,'.venv-backend/Scripts/python.exe'),generator=path.join(root,'cloud/test/manual-redaction-fixture.py');
const run=mode=>{const p=spawnSync(python,[generator,mode,dir],{cwd:root,env:{...process.env,PYTHONPATH:root},encoding:'utf8'});if(p.status!==0)throw Error(p.stderr);};run('create');
const pdf=await readFile(path.join(dir,'source.pdf')),hash=await sha(pdf),me='11111111-1111-4111-8111-111111111111',parent='22222222-2222-4222-8222-222222222222',aid='33333333-3333-4333-8333-333333333333',wid='44444444-4444-4444-8444-444444444444';
const now=()=>new Date().toISOString();
const tables={tx_jobs:[{id:parent,owner:me,status:'succeeded',filename:'Synthetic review.pdf',source_sha256:'0'.repeat(64),options:{operation:'pii'},metrics:{pages:3},created:now(),updated:now()}],tx_artifacts:[{id:aid,job_id:parent,kind:'result',storage_key:'fixture/source.pdf',filename:'synthetic.pdf',sha256:hash,size:pdf.length}],tx_workers:[{id:wid,owner:me,enabled:true,heartbeat:now(),capabilities:{ready:true,operations:['pii','manual_redaction']},name:'Synthetic local CPU worker'}],tx_events:[]};
const json=x=>new Response(JSON.stringify(x),{headers:{'Content-Type':'application/json'}});
const handle=createHandler(async(url,init={})=>{
 const u=new URL(url);if(u.pathname==='/auth/v1/user')return json({id:me,email:'synthetic@example.invalid'});
 if(u.pathname.endsWith('/tx_profiles'))return json([{id:me,role:'customer',active:true}]);
 if(u.pathname.startsWith('/storage/')){
  if(u.pathname.includes('/sign/'))return json({signedURL:'/object/'+(u.pathname.includes('result-')?'result.pdf':'source.pdf')});
  if(u.pathname.endsWith('/copy'))return json({Key:'copy'});
  return new Response(u.pathname.endsWith('/result.pdf')?await readFile(path.join(dir,'result.pdf')):pdf,{headers:{'Content-Type':'application/pdf'}});
 }
 const table=tables[u.pathname.split('/').at(-1)];if(!table)throw Error('Unexpected fixture route');
 const selected=table.filter(row=>[...u.searchParams].every(([k,v])=>!v.startsWith('eq.')||String(row[k])===v.slice(3)));
 if(init.method==='POST'){const data=JSON.parse(init.body);const row={id:crypto.randomUUID(),status:'uploading',metrics:{},created:now(),updated:now(),...data};table.push(row);return json([row]);}
 if(init.method==='PATCH'){
  const data=JSON.parse(init.body);selected.forEach(r=>Object.assign(r,data));
  if(data.status==='queued'){
   const j=selected[0];await writeFile(path.join(dir,'request.json'),JSON.stringify({regions:j.options.regions,source_sha256:j.source_sha256}));run('apply');
   const bytes=await readFile(path.join(dir,'result.pdf'));tables.tx_artifacts.push({id:crypto.randomUUID(),job_id:j.id,kind:'result',storage_key:'fixture/result-'+await sha(bytes)+'.pdf',filename:'manual.pdf',sha256:await sha(bytes),size:bytes.length});j.status='succeeded';j.stage='complete';j.metrics={pages:3,manual_redaction_verified:true};
  }
 }
 return json(selected);
});
const env={SUPABASE_URL:'https://fixture.invalid',SUPABASE_PUBLISHABLE_KEY:'synthetic',SUPABASE_SECRET_KEY:'synthetic',ASSETS:{fetch:async request=>{
 const name=new URL(request.url).pathname;const file=path.resolve(root,'cloud/public','.'+name);
 if(!file.startsWith(path.join(root,'cloud/public')+path.sep))return new Response('Forbidden',{status:403});
 try{return new Response(await readFile(file),{headers:{'Content-Type':file.endsWith('.mjs')||file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.wasm')?'application/wasm':'application/octet-stream'}});}catch{return new Response('Not found',{status:404});}
}}};
http.createServer(async(req,res)=>{
 try{const chunks=[];for await(const chunk of req)chunks.push(chunk);const headers={...req.headers,cookie:'tx_access=synthetic; tx_csrf=fixture'};
 const r=await handle(new Request('http://127.0.0.1:8843'+req.url,{method:req.method,headers,...(['GET','HEAD'].includes(req.method)?{}:{body:Buffer.concat(chunks)})}),env);
 res.writeHead(r.status,{...Object.fromEntries(r.headers),'Content-Security-Policy':"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; worker-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"});res.end(Buffer.from(await r.arrayBuffer()));
 }catch(e){res.writeHead(500);res.end('Synthetic fixture failure');console.error(e.message);}
}).listen(8843,'127.0.0.1',()=>console.log('Synthetic fixture ready: http://127.0.0.1:8843/redaction-review.html?job='+parent));
