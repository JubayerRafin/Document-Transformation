import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createHandler} from '../src/index.mjs';

const base=new URL('../public/',import.meta.url);
test('built page references real deployed CSS, JavaScript and image files',()=>{
 const html=readFileSync(new URL('studio.html',base),'utf8');
 const paths=[...html.matchAll(/(?:src|href)="(\/[^"#?]+)"/g)].map(x=>x[1]);
 assert.ok(paths.includes('/app.css'));assert.ok(paths.includes('/app.js'));
 assert.ok(paths.includes('/cover-original.jpg'));assert.ok(paths.includes('/cover-translated.jpg'));
 assert.ok(paths.includes('/studio.css'));assert.ok(paths.includes('/transformx-mark.svg'));
 for(const path of paths.filter(p=>/\.(css|js|jpg|svg)$/.test(p)))assert.ok(existsSync(new URL('.'+path,base)),`Missing published file: ${path}`);
 assert.doesNotMatch(html,/\/assets\//);
});
test('Studio redesign retains all existing control IDs and has a dark welcome screen',()=>{
 const original=readFileSync(new URL('../../backend/static/index.html',import.meta.url),'utf8');
 const html=readFileSync(new URL('studio.html',base),'utf8');
 const ids=s=>[...s.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
 const actual=ids(html);assert.equal(new Set(actual).size,actual.length,'Control IDs must remain unique');
 for(const id of ids(original))assert.ok(actual.includes(id),`Existing control removed: ${id}`);
 assert.match(html,/TransformX Studio welcome/);assert.match(html,/href="\/workspace"/);
 const css=readFileSync(new URL('studio.css',base),'utf8');assert.match(css,/color-scheme:dark/);assert.match(css,/prefers-reduced-motion/);
});
test('Worker forwards asset paths unchanged to the static binding',async()=>{
 const handle=createHandler(()=>{throw Error('Static assets must not need Supabase');});
 const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',
  ASSETS:{fetch:r=>new Response(new URL(r.url).pathname)}};
 for(const path of ['/app.css','/app.js','/cover-original.jpg','/cover-translated.jpg']){
  const r=await handle(new Request('https://example.com'+path),env);assert.equal(await r.text(),path);
 }
 const studio=await handle(new Request('https://example.com/studio'),env);assert.equal(await studio.text(),'/studio.html');
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
 for(const [route,file] of [['/','index.html'],['/index.html','index.html'],['/workspace','workspace.html'],['/workspace/','workspace.html'],['/workspace.html','workspace.html'],['/studio','studio.html'],['/studio/','studio.html'],['/studio.html','studio.html']]){
  const result=await handle(new Request('https://example.com'+route),env);
  assert.equal(result.status,200,route);assert.equal(result.headers.get('Location'),null,route);
  assert.equal(await result.text(),readFileSync(new URL(file,base),'utf8'),route);
 }
});
