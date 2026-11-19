import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createHandler} from '../src/index.mjs';

const base=new URL('../public/',import.meta.url);
test('the manual redaction editor and pdf.js are published next to the customer app',()=>{
 for(const file of ['redaction-review.html','redaction-review.css','redaction-review.mjs','pdfjs/pdf.mjs','pdfjs/pdf.worker.mjs','transformx-mark.svg'])
  assert.ok(existsSync(new URL(file,base)),`Missing published file: ${file}`);
 for(const stale of ['studio.html','studio.css','app.js','app.css'])assert.ok(!existsSync(new URL(stale,base)),`Removed Studio file still published: ${stale}`);
});
test('Worker forwards asset paths unchanged to the static binding',async()=>{
 const handle=createHandler(()=>{throw Error('Static assets must not need Supabase');});
 const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',
  ASSETS:{fetch:r=>new Response(new URL(r.url).pathname)}};
 for(const path of ['/redaction-review.mjs','/pdfjs/pdf.mjs','/transformx-mark.svg']){
  const r=await handle(new Request('https://example.com'+path),env);assert.equal(await r.text(),path);
 }
});
test('old Studio links open the workspace',async()=>{
 const handle=createHandler(()=>{throw Error('Redirects must not need Supabase');});
 const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',ASSETS:{fetch:()=>new Response('asset')}};
 for(const route of ['/studio','/studio/','/studio.html']){
  const r=await handle(new Request('https://example.com'+route),env);
  assert.equal(r.status,301,route);assert.equal(r.headers.get('Location'),'https://example.com/workspace',route);
 }
});

// A plain echo mock missed the live HTML canonicalization loop.
test('all document URLs serve files without an HTML canonicalization redirect',async()=>{
 const config=JSON.parse(readFileSync(new URL('../wrangler.jsonc',import.meta.url),'utf8'));
 assert.equal(config.assets.html_handling,'none');
 const handle=createHandler(()=>{throw Error('Static documents must not require authentication');});
 const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',ASSETS:{fetch:r=>{
  const path=new URL(r.url).pathname;
  if(config.assets.html_handling!=='none'&&path.endsWith('.html'))return Response.redirect(new URL(path.slice(0,-5),r.url),307);
  const file=new URL('.'+path,base);
  return existsSync(file)?new Response(readFileSync(file),{headers:{'Content-Type':'text/html'}}):new Response('Missing',{status:404});
 }}};
 for(const [route,file] of [['/','index.html'],['/index.html','index.html'],['/workspace','workspace.html'],['/workspace/','workspace.html'],['/workspace.html','workspace.html']]){
  const result=await handle(new Request('https://example.com'+route),env);
  assert.equal(result.status,200,route);assert.equal(result.headers.get('Location'),null,route);
  assert.equal(await result.text(),readFileSync(new URL(file,base),'utf8'),route);
 }
});
