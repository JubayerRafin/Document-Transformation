import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {webcrypto} from 'node:crypto';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const html=readFileSync(new URL('../ui/redaction-review.html',import.meta.url),'utf8');
const script=readFileSync(new URL('../ui/redaction-review.mjs',import.meta.url),'utf8').replace("import {getDocument,GlobalWorkerOptions} from '/pdfjs/pdf.mjs';",'const {getDocument,GlobalWorkerOptions}=window.fixturePdf;');
const id='11111111-1111-4111-8111-111111111111';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture({conflict=false,finalized=false,failed=false,wrongHash=false,worker=true,comparison=false,role='customer',queue=null,why=null}={}){
 const dom=new JSDOM(html,{url:'https://test.example/redaction-review.html?job='+id+(comparison?'&compare=1':''),runScripts:'outside-only'}),w=dom.window;
 const data=new TextEncoder().encode('%PDF-synthetic'),hash=Buffer.from(await webcrypto.subtle.digest('SHA-256',data)).toString('hex');
 let draft={job_id:id,status:failed?'failed':finalized?'queued':'uploading',revision:'revision-a',regions:failed?[{page:1,x:.1,y:.2,width:.3,height:.1}]:[],parent_job_id:'parent',parent_artifact_id:'artifact',source_sha256:wrongHash?'0'.repeat(64):hash,filename:'Synthetic.pdf'},calls=[],rendered=[];
 const doc={numPages:3,getPage:async n=>({getViewport:({scale})=>({width:400*scale,height:500*scale}),render:opts=>{rendered.push([n,opts]);return {promise:Promise.resolve(),cancel(){}};}}),destroy:async()=>{}};
 w.fixturePdf={GlobalWorkerOptions:{},getDocument:()=>({promise:Promise.resolve(doc)})};
 Object.defineProperty(w.crypto,'subtle',{value:webcrypto.subtle});w.TextEncoder=TextEncoder;w.TextDecoder=TextDecoder;
 w.HTMLElement.prototype.setPointerCapture=function(){};w.confirm=()=>true;
 w.fetch=async(path,init={})=>{
  calls.push([path,init]);const make=(x,status=200)=>({ok:status<400,status,json:async()=>x});
  if(path==='/v1/me')return make({role,csrf:'fixture'});
  if(path==='/v1/workers')return make(worker?[{id:'worker',name:'Synthetic server',ready:true,capabilities:{operations:['manual_redaction']}}]:[]);
  if(path.endsWith('/content'))return {ok:true,arrayBuffer:async()=>data.buffer};
  if(path.endsWith('/redaction-draft')){
   if(init.method==='PUT'){
    if(conflict)return make({error:'DRAFT_CONFLICT'},409);
    const b=JSON.parse(init.body);draft={...draft,regions:b.regions,revision:'revision-b'};return make({revision:draft.revision});
   }return make(draft);
  }
  if(path.endsWith('/redaction-finalize'))return make({job_id:id,status:'queued'});
  if(queue&&path==='/v1/jobs/parent')return make({id:'parent',status:'succeeded',options:{operation:'pii'},artifacts:[],metrics:{review_items:queue}});
  return make({id,metrics:why?{redaction_map:why}:{},filename:'Synthetic.pdf',options:{operation:'manual_redaction'},status:failed?'failed':finalized?'succeeded':'uploading',stage:'complete',artifacts:finalized?[{id:'result',kind:'result',sha256:hash},...(comparison?[{id:'source',kind:'source',sha256:hash}]:[])]:[]});
 };
 w.eval(script);
 // SHA-256 runs on the crypto thread pool. Waiting a fixed number of event
 // loop turns races that work when the full test suite runs concurrently.
 const deadline=Date.now()+5000;
 while(w.document.querySelector('#editor').hidden&&w.document.querySelector('#finished').hidden&&w.document.querySelector('#error').hidden){
  if(Date.now()>deadline)throw new Error('Editor did not reach a terminal loading state.');
  await new Promise(resolve=>setTimeout(resolve,5));
 }
 for(let n=0;n<3;n++)await flush();
 const q=s=>w.document.querySelector(s),click=s=>q(s).click(),change=(s,v)=>{q(s).value=String(v);q(s).dispatchEvent(new w.Event('change',{bubbles:true}));};
 return {dom,w,q,click,change,calls,rendered,draft:()=>draft};
}
test('editor creates, edits, undoes, redoes and saves page-bound normalized marks',async()=>{
 const f=await fixture();try{
  assert.equal(f.q('#editor').hidden,false);assert.equal(f.rendered[0][1].annotationMode,1);
  f.click('#add');assert.match(f.q('#area-count').textContent,/1 additional area/);
  f.change('#x',22);f.click('#undo');assert.equal(f.q('#x').value,'');f.click('#redo');
  f.change('#page',2);await flush();f.click('#add');assert.match(f.q('#area-count').textContent,/2 additional areas/);
  f.click('#save');for(let n=0;n<4;n++)await flush();
  const save=f.calls.find(([p,i])=>p.endsWith('/redaction-draft')&&i.method==='PUT');assert.ok(save);const b=JSON.parse(save[1].body);assert.equal(b.regions[0].page,1);assert.equal(b.regions[0].x,.22);assert.equal(b.regions[1].page,2);assert.equal(save[1].headers['X-CSRF-Token'],'fixture');
 }finally{f.dom.window.close();}
});
test('pointer drawing, moving and resizing retain fractional page coordinates',async()=>{
 const f=await fixture();try{
  const overlay=f.q('#overlay');overlay.getBoundingClientRect=()=>({left:0,top:0,width:400,height:500});
  const event=(target,type,x,y)=>target.dispatchEvent(new f.w.MouseEvent(type,{clientX:x,clientY:y,button:0,bubbles:true}));
  event(overlay,'pointerdown',40,50);event(overlay,'pointermove',160,150);event(overlay,'pointerup',160,150);
  assert.equal(f.q('#x').value,'10.0');assert.equal(f.q('#width').value,'30.0');
  event(f.q('.area'),'pointerdown',80,75);event(overlay,'pointermove',120,125);event(overlay,'pointerup',120,125);
  assert.equal(f.q('#x').value,'20.0');assert.equal(f.q('#y').value,'20.0');
  event(f.q('.handle'),'pointerdown',200,200);event(overlay,'pointermove',240,250);event(overlay,'pointerup',240,250);
  assert.equal(f.q('#width').value,'40.0');assert.equal(f.q('#height').value,'30.0');
 }finally{f.dom.window.close();}
});
test('stale draft saves show conflict and never queue changes',async()=>{
 const f=await fixture({conflict:true});try{f.click('#add');f.click('#save');for(let n=0;n<4;n++)await flush();assert.equal(f.q('#error').hidden,false);assert.match(f.q('#error').textContent,/another tab/);assert.equal(f.calls.some(([p])=>p.endsWith('/redaction-finalize')),false);}finally{f.dom.window.close();}
});
test('wrong PDF hash blocks the editor before any page is rendered',async()=>{
 const f=await fixture({wrongHash:true});try{assert.equal(f.q('#editor').hidden,true);assert.equal(f.rendered.length,0);assert.match(f.q('#error').textContent,/does not match/);}finally{f.dom.window.close();}
});
test('finalization needs review confirmation, a capable server and a saved draft',async()=>{
 const f=await fixture();try{
  f.click('#add');f.click('#finalize');await flush();assert.equal(f.calls.some(([p])=>p.endsWith('/redaction-finalize')),false);
  f.q('#confirm').checked=true;f.q('#worker').value='worker';
  // Browser dialogs may be suppressed; the page must not depend on them.
  f.w.confirm=()=>false;
  f.click('#finalize');await flush();
  assert.equal(f.calls.some(([p,i])=>p.endsWith('/redaction-finalize')||i?.method==='PUT'),false,'first click only arms');
  assert.match(f.q('#finalize').textContent,/again/);
  f.click('#finalize');for(let n=0;n<6;n++)await flush();
  const mutation=f.calls.find(([p])=>p.endsWith('/redaction-finalize'));assert.ok(mutation);assert.equal(JSON.parse(mutation[1].body).revision,'revision-b');assert.equal(f.q('#editor').hidden,true);
 }finally{f.dom.window.close();}
});
test('completed child jobs offer the new download and another review without modifying their parent',async()=>{
 const f=await fixture({finalized:true});try{assert.equal(f.q('#finished').hidden,false);assert.equal(f.q('#download').hidden,false);assert.match(f.q('#download').href,/artifacts\/result$/);assert.equal(f.calls.some(([,i])=>i.method),false);}finally{f.dom.window.close();}
});
test('comparison renders both PDFs, synchronizes page selection and can enter/exit full-window mode',async()=>{
 const f=await fixture({comparison:true,finalized:true});try{
  assert.equal(f.q('#original-panel').hidden,false);assert.ok(f.w.document.body.classList.contains('comparison'));
  f.change('#page',2);for(let n=0;n<4;n++)await flush();assert.deepEqual(f.rendered.slice(-2).map(r=>r[0]),[2,2]);
  f.click('#fullscreen');await flush();assert.ok(f.w.document.documentElement.classList.contains('expanded'));
  f.click('#fullscreen');await flush();assert.equal(f.w.document.documentElement.classList.contains('expanded'),false);
  assert.equal(f.calls.some(([,i])=>i.method),false);
 }finally{f.dom.window.close();}
});
test('a viewer can compare completed results but cannot enter manual editing',async()=>{
 const f=await fixture({comparison:true,finalized:true,role:'viewer'});try{assert.equal(f.q('#editor').hidden,false);assert.equal(f.q('#start-editing').hidden,true);assert.equal(f.calls.some(([,i])=>i.method),false);}finally{f.dom.window.close();}
});
test('failed finalization keeps the saved plan and offers recovery without rerunning detection',async()=>{
 const f=await fixture({failed:true});try{
  assert.equal(f.q('#finished').hidden,false);
  assert.equal(f.q('#retry-draft').hidden,false);
  assert.match(f.q('#message').textContent,/saved selections are retained/i);
  assert.equal(f.draft().regions.length,1);
  assert.equal(f.calls.some(([,i])=>i.method),false);
 }finally{f.dom.window.close();}
});
test('review queue shows automatic-redaction items and adds possible misses as undoable areas',async()=>{
 const queue=[{page:1,x:.1,y:.2,width:.3,height:.05,category:'Identifiers',reason:'rule_overruled_model',state:'suggested'},
  {page:2,x:.4,y:.5,width:.2,height:.04,category:'Unknown',reason:'unreadable_text',state:'suggested'},
  {page:1,x:.1,y:.6,width:.5,height:.03,category:'Names',reason:'unverified_answer',state:'masked'}];
 const f=await fixture({queue});try{
  assert.equal(f.q('#queue').hidden,false);
  assert.equal(f.q('#queue-items').children.length,3);
  assert.match(f.q('#queue-summary').textContent,/2 possible misses \(2 not added yet\) · 1 masked as a precaution/);
  assert.equal(f.w.document.querySelectorAll('.queue-box').length,2,'page 1 shows its two queue boxes');
  // Precautionary items cannot be added; misses can, one at a time or all together.
  assert.equal([...f.q('#queue-items').children][2].querySelectorAll('button').length,1);
  [...f.q('#queue-items').children][0].querySelectorAll('button')[1].click();await flush();
  assert.equal(f.q('#area-count').textContent,'1 additional area');
  assert.equal([...f.q('#queue-items').children][0].querySelectorAll('button')[1].textContent,'Added');
  f.click('#add-suggestions');await flush();
  assert.equal(f.q('#area-count').textContent,'2 additional areas');
  assert.equal(f.q('#add-suggestions').disabled,true);
  f.click('#undo');await flush();assert.equal(f.q('#area-count').textContent,'1 additional area');
 }finally{f.dom.window.close();}
});
test('review queue stays hidden when the automatic redaction reported nothing',async()=>{
 const f=await fixture();try{assert.equal(f.q('#queue').hidden,true);}finally{f.dom.window.close();}
});
test('comparison can show why each automatic box was drawn',async()=>{
 const why=[{page:1,x:.1,y:.1,width:.2,height:.03,category:'Names',method:'field_label'},
  {page:1,x:.1,y:.3,width:.2,height:.03,category:'Identifiers',method:'unverifiable_model_answer'},
  {page:2,x:.1,y:.3,width:.2,height:.03,category:'Names',method:'model_proposal'}];
 const f=await fixture({comparison:true,finalized:true,why});try{
  assert.equal(f.q('#why-toggle').hidden,false);
  assert.equal(f.w.document.querySelectorAll('.why-box:not([style*="static"])').length,0,'off until asked');
  f.q('#why').checked=true;f.q('#why').dispatchEvent(new f.w.Event('change'));
  const boxes=[...f.w.document.querySelectorAll('#why-layer .why-box')];
  assert.equal(boxes.length,2,'only page 1 boxes');
  assert.match(boxes[0].title,/Names: masked because its field label says it is personal data/);
  assert.match(boxes[1].title,/precaution/);
  assert.equal(f.q('#why-legend').hidden,false);
 }finally{f.dom.window.close();}
});
