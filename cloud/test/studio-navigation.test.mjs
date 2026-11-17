import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const html=readFileSync(new URL('../public/studio.html',import.meta.url),'utf8');
const script=readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
for(const route of ['/studio','/studio/','/studio.html']){
 test(`anonymous ${route} opens sign-in and can reopen after dismissal`,async()=>{
  const dom=new JSDOM(html,{url:'https://example.com'+route,runScripts:'outside-only'});
  try{
   const w=dom.window;
   w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
   w.HTMLDialogElement.prototype.close=function(){this.open=false;};
   w.setInterval=()=>0;
   w.fetch=async()=>({ok:false,status:401,json:async()=>({error:'SIGN_IN_REQUIRED'})});
   w.eval(script);
   await new Promise(resolve=>setImmediate(resolve));
   assert.equal(w.document.querySelector('#signin').open,true);
   w.document.querySelector('#close-login').click();
   assert.equal(w.document.querySelector('#signin').open,false);
   w.document.querySelector('[data-action="studio"]').click();
   assert.equal(w.document.querySelector('#signin').open,true);
  }finally{dom.window.close();}
 });
}
test('cancel without a selected job makes no mutation request',async()=>{
 const dom=new JSDOM(html,{url:'https://example.com/studio',runScripts:'outside-only'});
 try{
  const w=dom.window,mutations=[];
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.setInterval=()=>0;
  w.fetch=async(url,options={})=>{if(options.method&&options.method!=='GET')mutations.push(url);return {ok:false,status:401,json:async()=>({error:'SIGN_IN_REQUIRED'})};};
  w.eval(script);await new Promise(resolve=>setImmediate(resolve));
  w.document.querySelector('#cancel-job').click();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(mutations,[]);
  assert.equal(w.document.querySelector('#notice').textContent,'');
 }finally{dom.window.close();}
});
