import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
import worker from '../src/index.mjs';
import policies from '../generated/frontend-csp.json' with {type:'json'};
const base=new URL('../public/',import.meta.url);
for(const [route,file] of [['/','index.html'],['/workspace','workspace.html']]){
 test(`customer ${route} has real static chunks and exact hydration hashes`,async()=>{
  const html=readFileSync(new URL(file,base),'utf8');assert.match(html,/TransformX/);
  const assets=[...html.matchAll(/(?:src|href)="(\/_next\/[^"?#]+)"/g)].map(m=>m[1]);
  assert.ok(assets.length>2);for(const p of assets)assert.ok(existsSync(new URL('.'+p,base)),p);
  const hashes=[...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)].filter(m=>! /\bsrc\s*=/.test(m[1])&&m[2]).map(m=>`'sha256-${createHash('sha256').update(m[2]).digest('base64')}'`);
  assert.deepEqual([...new Set(hashes)],policies[route]);
  const response=await worker.fetch(new Request('https://example.com'+route),{SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',ASSETS:{fetch:()=>new Response(html)}});
  const csp=response.headers.get('content-security-policy');for(const h of hashes)assert.ok(csp.includes(h));
  assert.doesNotMatch(csp,/script-src[^;]*(unsafe-inline|unsafe-eval)/);
  assert.match(csp,/style-src-attr 'unsafe-inline'/);
 });
}
test('Studio keeps its existing restrictive script and style policy',async()=>{
 const r=await worker.fetch(new Request('https://example.com/studio'),{SUPABASE_URL:'https://test.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public',ASSETS:{fetch:()=>new Response('studio')}});
 const csp=r.headers.get('content-security-policy');assert.match(csp,/script-src 'self';/);assert.doesNotMatch(csp,/sha256|unsafe-inline|unsafe-eval/);
});

test('customer document aliases retain their hydration CSP',async()=>{
 for(const [route,policy] of [['/index.html','/'],['/workspace/','/workspace'],['/workspace.html','/workspace']]){
  const response=await worker.fetch(new Request('https://example.com'+route),{ASSETS:{fetch:()=>new Response('page')}});
  const csp=response.headers.get('content-security-policy');
  for(const hash of policies[policy])assert.ok(csp.includes(hash),route);
 }
});
