import {getDocument,GlobalWorkerOptions} from '/pdfjs/pdf.mjs';
GlobalWorkerOptions.workerSrc='/pdfjs/pdf.worker.mjs';
const $=s=>document.querySelector(s),MAX=500;
let csrf='',draft,doc,originalDoc,originalRenderTask,page=1,marks=[],selected=-1,undo=[],redo=[],busy=false,dirty=false,renderTask,renderGeneration=0,pointer=null,timer,abort=new AbortController(),polling=false,armed=0;
// Final confirmation stays in the page: browsers and embedded viewers may suppress
// window.confirm, which silently cancelled finalization.
const ARM_MS=15000,FINALIZE_LABEL='Apply redactions to a new copy';
function disarm(){armed=0;$('#finalize').textContent=FINALIZE_LABEL;}
// Review queue from the automatic redaction: boxes only (no text), as fractions of the page.
let queue=[],queueFocus=-1;
const REASONS={unverified_answer:'Masked whole: the detector could not verify its answer for this text.',
 whole_line:'Masked as a whole line: the value could not be separated from its label.',
 rule_overruled_model:'Not masked: the model flagged this, but its column label says it is not personal data.',
 unreadable_text:'Not masked: this scanned text could not be read automatically.',
 final_check:'Masked by the final privacy check: the detector missed it, so check the whole area.'};
const sameBox=(a,b)=>a.page===b.page&&['x','y','width','height'].every(k=>Math.abs(a[k]-b[k])<.002);
const added=q=>marks.some(m=>sameBox(m,q));
function addSuggestions(list){
 const fresh=list.filter(q=>q.state==='suggested'&&!added(q)).slice(0,MAX-marks.length);
 if(!editable()||!fresh.length)return;
 remember();for(const q of fresh)marks.push({page:q.page,x:q.x,y:q.y,width:q.width,height:q.height});changed();
}
function drawQueue(){
 if(!queue.length)return;
 const misses=queue.filter(q=>q.state==='suggested'),open=misses.filter(q=>!added(q)).length;
 $('#queue-summary').textContent=`${misses.length} possible miss${misses.length===1?'':'es'} (${open} not added yet) · ${queue.length-misses.length} masked as a precaution`;
 $('#add-suggestions').hidden=!misses.length;$('#add-suggestions').disabled=!open||!editable();
 const list=$('#queue-items');list.replaceChildren();
 queue.forEach((q,i)=>{
  const li=document.createElement('li'),title=document.createElement('strong'),why=document.createElement('span'),row=document.createElement('span');
  title.textContent=`Page ${q.page} · ${q.state==='suggested'?'Possible miss':'Masked as a precaution'} · ${q.category==='Unknown'?'unknown type':q.category}`;
  why.className='why';why.textContent=REASONS[q.reason]||'';row.className='row';
  const show=document.createElement('button');show.type='button';show.textContent='Show';
  show.onclick=()=>{queueFocus=i;if(page!==q.page){page=q.page;selected=-1;void renderPage().catch(error);}else draw();};row.append(show);
  if(q.state==='suggested'){const add=document.createElement('button');add.type='button';const done=added(q);add.textContent=done?'Added':'Add as area';add.disabled=done||!editable();add.onclick=()=>addSuggestions([q]);row.append(add);}
  li.append(title,why,row);list.append(li);
  if(q.page!==page)return;
  const box=document.createElement('div');box.className=`queue-box ${q.state}${i===queueFocus?' current':''}`;box.setAttribute('aria-hidden','true');
  Object.assign(box.style,{left:q.x*100+'%',top:q.y*100+'%',width:q.width*100+'%',height:q.height*100+'%'});$('#overlay').append(box);
 });
}
async function loadQueue(){
 for(const id of [draft.parent_job_id,draft.original_job_id]){
  if(!id)continue;
  try{const j=await api('/v1/jobs/'+id),items=j.metrics?.review_items;if(!whyMap.length)setWhy(j.metrics?.redaction_map);if(Array.isArray(items)&&items.length){queue=items.filter(q=>q.page<=doc.numPages);break;}}catch{}
 }
 $('#queue').hidden=!queue.length;
}
$('#add-suggestions').onclick=()=>addSuggestions(queue);
// Why each automatic box was drawn (category and method; never the text).
let whyMap=[];
const METHOD_TEXT={field_label:'its field label says it is personal data',pattern:'it has the format of this data type',
 credential_label_pattern:'it looks like a password, key or token',address_block:'it is part of a postal address block',model_proposal:'the language model identified it',
 document_name_sweep:'it is on the document’s list of person names',name_variant:'it is part of a person name found elsewhere',
 document_consistency:'the same value was found elsewhere in the document',unverifiable_model_answer:'precaution: the model answer could not be verified',
 final_check:'the final privacy check found it after redaction'};
const LEGEND=[['m-field_label','Field label or format'],['m-model_proposal','Language model'],['m-name_variant','Name list or consistency'],['m-unverifiable_model_answer','Precaution or final check']];
function drawWhy(){
 const layer=$('#why-layer');if(!layer)return;layer.replaceChildren();
 if(!$('#why').checked)return;
 for(const b of whyMap){
  if(b.page!==page)continue;
  const el=document.createElement('div');el.className='why-box m-'+b.method;
  el.title=`${b.category}: masked because ${METHOD_TEXT[b.method]||'it was detected'}.`;
  Object.assign(el.style,{left:b.x*100+'%',top:b.y*100+'%',width:b.width*100+'%',height:b.height*100+'%'});layer.append(el);
 }
}
function setWhy(map){
 whyMap=Array.isArray(map)?map:[];$('#why-toggle').hidden=!whyMap.length;
 const legend=$('#why-legend');legend.replaceChildren();
 for(const [cls,text] of LEGEND){const s=document.createElement('span'),i=document.createElement('i');i.className='why-box '+cls;i.style.position='static';s.append(i,text);legend.append(s);}
}
$('#why').onchange=()=>{$('#why-legend').hidden=!$('#why').checked;drawWhy();};
const comparison=new URLSearchParams(location.search).get('compare')==='1';
$('#fullscreen').onclick=async()=>{
 try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.classList.contains('expanded'))document.documentElement.classList.remove('expanded');else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else document.documentElement.classList.add('expanded');}
 catch{document.documentElement.classList.toggle('expanded');}
 $('#fullscreen').textContent=document.fullscreenElement||document.documentElement.classList.contains('expanded')?'Exit full screen':'Full screen';
 if(doc)void renderPage().catch(error);
};
document.addEventListener('fullscreenchange',()=>{$('#fullscreen').textContent=document.fullscreenElement?'Exit full screen':'Full screen';if(doc)void renderPage().catch(error);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.documentElement.classList.remove('expanded');$('#fullscreen').textContent='Full screen';}});
const messages={SIGN_IN_REQUIRED:'Sign in through Your documents, then reopen this review.',OWNER_REQUIRED:'Only the owner can edit this document.',DRAFT_CONFLICT:'This draft changed in another tab. Reload before continuing; your current marks have not been applied.',MANUAL_WORKER_REQUIRED:'Choose an online server updated for manual redaction. Existing translation servers need the new worker package.',DRAFT_ALREADY_FINALIZED:'This draft was already submitted. Reload to see its status.',PDF_VERSION_CHANGED:'The PDF version does not match this draft. Reopen the result.',INVALID_REDACTION_REGIONS:'Select at least one valid area within the page.'};
const failures={WORKER_HEARTBEAT_LOST:'The processing server stopped responding before finishing.',MANUAL_REDACTION_FAILED:'The selected areas could not be removed and verified, so no copy was released.',INPUT_REJECTED:'The server could not open this PDF version.',RESULT_UPLOAD_FAILED:'The redacted copy could not be uploaded after several attempts.',ENGINE_TIMEOUT:'Processing exceeded the time limit and was stopped.',OUTPUT_INVALID:'The produced copy failed basic checks and was not released.'};
function error(e){$('#error').hidden=false;$('#error').textContent=messages[e.code]||e.message||'Request failed. Your previous PDFs are unchanged.';}
function status(s){$('#message').textContent=s;}
async function api(path,init={}){const response=await fetch(path,{...init,signal:abort.signal,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...(init.method?{'X-CSRF-Token':csrf}:{}),...init.headers}});const data=await response.json();if(!response.ok)throw Object.assign(new Error(messages[data.error]||'The document service could not complete this request.'),{code:data.error});return data;}
const base=()=>'/v1/jobs/'+draft.job_id;
const copy=xs=>xs.map(x=>({...x}));
const editable=()=>!busy&&draft?.status==='uploading';
function remember(){undo.push(copy(marks));if(undo.length>100)undo.shift();redo=[];dirty=true;}
function changed(){dirty=true;$('#confirm').checked=false;disarm();status('Unsaved draft changes. Nothing has been removed yet.');draw();}
function draw(){
 $('#overlay').replaceChildren();$('#areas').replaceChildren();
 marks.forEach((r,i)=>{
  const item=document.createElement('li'),b=document.createElement('button');b.textContent=`Page ${r.page} · area ${i+1}`;b.onclick=()=>{selected=i;if(page!==r.page){page=r.page;void renderPage().catch(error);}else draw();};item.append(b);$('#areas').append(item);
  if(r.page!==page)return;
  const area=document.createElement('button');area.className='area';area.type='button';area.dataset.index=i;area.setAttribute('aria-label',`Select area ${i+1} on page ${r.page}`);area.setAttribute('aria-pressed',String(selected===i));
  Object.assign(area.style,{left:r.x*100+'%',top:r.y*100+'%',width:r.width*100+'%',height:r.height*100+'%'});
  const handle=document.createElement('span');handle.className='handle';handle.dataset.resize='true';handle.setAttribute('aria-hidden','true');area.append(handle);area.onclick=()=>{if(!pointer){selected=i;draw();}};$('#overlay').append(area);
 });
 $('#area-count').textContent=marks.length+' additional area'+(marks.length===1?'':'s');
 $('#position').disabled=selected<0||!editable();
 for(const k of ['x','y','width','height'])$('#'+k).value=selected>=0?(marks[selected][k]*100).toFixed(1):'';
 $('#undo').disabled=!undo.length||!editable();$('#redo').disabled=!redo.length||!editable();$('#delete').disabled=selected<0||!editable();$('#add').disabled=marks.length>=MAX||!editable();
 $('#save').disabled=!editable();$('#finalize').disabled=!editable()||!marks.length;
 $('#previous').disabled=page<=1;$('#next').disabled=page>=doc?.numPages;
 $('#pages').querySelectorAll('button').forEach(b=>b.setAttribute('aria-current',Number(b.dataset.page)===page?'page':'false'));
 drawQueue();drawWhy();
}
async function renderPage(){
 const generation=++renderGeneration;if(renderTask){renderTask.cancel();try{await renderTask.promise;}catch{}}if(originalRenderTask){originalRenderTask.cancel();try{await originalRenderTask.promise;}catch{}}
 const p=await doc.getPage(page);if(generation!==renderGeneration)return;
 const fit=Math.max(180,$('#result-scroll').clientWidth-12),v=p.getViewport({scale:1}),scale=fit/v.width*Number($('#zoom').value),viewport=p.getViewport({scale}),ratio=Math.min(devicePixelRatio||1,2),canvas=$('#canvas');
 canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);canvas.style.width=viewport.width+'px';canvas.style.height=viewport.height+'px';$('#sheet').style.width=viewport.width+'px';$('#sheet').style.height=viewport.height+'px';$('#page').value=String(page);
 renderTask=p.render({canvas,viewport,transform:ratio===1?null:[ratio,0,0,ratio,0,0],annotationMode:1});
 try{await renderTask.promise;}catch(e){if(e.name!=='RenderingCancelledException')throw e;}
 if(originalDoc&&generation===renderGeneration){
  $('#original-canvas').hidden=page>originalDoc.numPages;$('#original-note').textContent=page>originalDoc.numPages?'No corresponding original page.':'';
  if(page<=originalDoc.numPages){const op=await originalDoc.getPage(page);if(generation!==renderGeneration)return;const ov=op.getViewport({scale:1}),os=Math.max(180,$('#original-scroll').clientWidth-12)/ov.width*Number($('#zoom').value),vp=op.getViewport({scale:os}),oc=$('#original-canvas');oc.width=Math.ceil(vp.width*ratio);oc.height=Math.ceil(vp.height*ratio);oc.style.width=vp.width+'px';oc.style.height=vp.height+'px';originalRenderTask=op.render({canvas:oc,viewport:vp,transform:ratio===1?null:[ratio,0,0,ratio,0,0],annotationMode:1});try{await originalRenderTask.promise;}catch(e){if(e.name!=='RenderingCancelledException')throw e;}}
 }
 if(generation===renderGeneration)draw();
}
function at(e){const r=$('#overlay').getBoundingClientRect();return {x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};}
$('#overlay').addEventListener('pointerdown',e=>{
 if(!editable()||e.button!==0)return;const target=e.target.closest('.area');const i=target?Number(target.dataset.index):-1;
 if(i<0&&marks.length>=MAX)return;
 pointer={start:at(e),index:i,original:i>=0?{...marks[i]}:null,mode:i<0?'draw':e.target.dataset.resize?'resize':'move',before:copy(marks)};
 selected=i;$('#overlay').setPointerCapture(e.pointerId);e.preventDefault();
});
$('#overlay').addEventListener('pointermove',e=>{
 if(!pointer)return;const pt=at(e),dx=pt.x-pointer.start.x,dy=pt.y-pointer.start.y,r=pointer.original;
 if(pointer.mode==='draw'){
  const el=$('#marquee');el.hidden=false;Object.assign(el.style,{left:Math.min(pt.x,pointer.start.x)*100+'%',top:Math.min(pt.y,pointer.start.y)*100+'%',width:Math.abs(dx)*100+'%',height:Math.abs(dy)*100+'%'});
 }else{
  marks[pointer.index]=pointer.mode==='move'?{...r,x:Math.max(0,Math.min(1-r.width,r.x+dx)),y:Math.max(0,Math.min(1-r.height,r.y+dy))}:{...r,width:Math.max(.005,Math.min(1-r.x,r.width+dx)),height:Math.max(.005,Math.min(1-r.y,r.height+dy))};draw();
 }
});
$('#overlay').addEventListener('pointerup',e=>{
 if(!pointer)return;const p=pointer;pointer=null;$('#marquee').hidden=true;
 if(p.mode==='draw'){const pt=at(e),w=Math.abs(pt.x-p.start.x),h=Math.abs(pt.y-p.start.y);if(w<.005||h<.005)return;undo.push(p.before);redo=[];marks.push({page,x:Math.min(pt.x,p.start.x),y:Math.min(pt.y,p.start.y),width:w,height:h});selected=marks.length-1;}
 else if(JSON.stringify(marks)!==JSON.stringify(p.before)){undo.push(p.before);redo=[];}else{draw();return;}
 changed();
});
$('#overlay').addEventListener('pointercancel',()=>{if(pointer){marks=pointer.before;pointer=null;$('#marquee').hidden=true;draw();}});
$('#add').onclick=()=>{if(!editable()||marks.length>=MAX)return;remember();marks.push({page,x:.15,y:.15,width:.35,height:.08});selected=marks.length-1;changed();};
$('#delete').onclick=()=>{if(selected<0||!editable())return;remember();marks.splice(selected,1);selected=-1;changed();};
$('#undo').onclick=()=>{if(!undo.length||!editable())return;redo.push(copy(marks));marks=undo.pop();selected=-1;changed();};
$('#redo').onclick=()=>{if(!redo.length||!editable())return;undo.push(copy(marks));marks=redo.pop();selected=-1;changed();};
for(const k of ['x','y','width','height'])$('#'+k).onchange=e=>{if(selected<0||!editable())return;const value=Number(e.target.value)/100;if(!Number.isFinite(value)){draw();return;}const r={...marks[selected],[k]:value};if(r.x<0||r.y<0||r.width<.005||r.height<.005||r.x+r.width>1||r.y+r.height>1){status('Keep the entire selection inside the page.');draw();return;}remember();marks[selected]=r;changed();};
$('#page').onchange=()=>{page=Number($('#page').value);selected=-1;void renderPage().catch(error);};
$('#previous').onclick=()=>{if(page>1){page--;selected=-1;void renderPage().catch(error);}};$('#next').onclick=()=>{if(page<doc.numPages){page++;selected=-1;void renderPage().catch(error);}};$('#zoom').onchange=()=>void renderPage().catch(error);
async function save(){const r=await api(base()+'/redaction-draft',{method:'PUT',body:JSON.stringify({revision:draft.revision,regions:marks})});draft.revision=r.revision;dirty=false;status('Draft saved privately. No redactions have been applied.');}
$('#save').onclick=async()=>{if(!editable())return;busy=true;draw();try{await save();$('#error').hidden=true;}catch(e){error(e);}finally{busy=false;draw();}};
async function workers(){const ws=await api('/v1/workers'),select=$('#worker'),prior=select.value;select.replaceChildren(new Option('Choose an updated server',''));ws.filter(w=>w.ready&&w.capabilities?.operations?.includes('manual_redaction')).forEach(w=>select.add(new Option(w.name,w.id)));select.value=prior;}
$('#refresh-workers').onclick=()=>void workers().catch(error);
async function poll(){
 if(polling)return;polling=true;
 try{const j=await api(base());$('#finished-stage').textContent=j.stage.replaceAll('_',' ');
  if(j.status==='succeeded'){$('#finished-title').textContent='New redacted copy generated';const a=j.artifacts.find(a=>a.kind==='result');if(a){$('#download').href=base()+'/artifacts/'+a.id;$('#download').hidden=false;$('#continue-review').href='/redaction-review.html?job='+j.id+'&new=1';$('#continue-review').hidden=false;}status('Review the final PDF before sharing.');return;}
  if(['failed','cancelled','interrupted'].includes(j.status)){$('#finished-title').textContent='Finalization did not complete';$('#finished-stage').textContent=failures[j.error_code]||'Processing did not complete.';$('#retry-draft').hidden=!draft.parent_job_id;status('Your saved selections are retained. Reopen them to review and try again.');return;}
  timer=setTimeout(()=>void poll(),4000);
 }catch(e){error(e);timer=setTimeout(()=>void poll(),10000);}finally{polling=false;}
}
$('#retry-draft').onclick=async()=>{
 const button=$('#retry-draft');button.disabled=true;
 try{
  const keyName='tx-redaction-retry-'+draft.job_id;let key=sessionStorage.getItem(keyName);if(!key){key=crypto.randomUUID();sessionStorage.setItem(keyName,key);}
  const created=await api('/v1/jobs/'+draft.parent_job_id+'/redactions',{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify({artifact_id:draft.parent_artifact_id,source_sha256:draft.source_sha256})});
  const next=await api('/v1/jobs/'+created.job_id+'/redaction-draft');
  if(next.status==='uploading'&&!next.regions.length&&draft.regions.length){
   await api('/v1/jobs/'+created.job_id+'/redaction-draft',{method:'PUT',body:JSON.stringify({revision:next.revision,regions:draft.regions})});
  }
  sessionStorage.removeItem(keyName);location.assign('/redaction-review.html?job='+encodeURIComponent(created.job_id));
 }catch(e){error(e);button.disabled=false;}
};
$('#finalize').onclick=async()=>{
 if(!editable()||!marks.length)return;
 if(!$('#confirm').checked){status('Review all pages and confirm before applying.');return;}
 if(!$('#worker').value){status('Choose an online server with manual-redaction support.');return;}
 if(!armed||Date.now()-armed>ARM_MS){
  armed=Date.now();$('#finalize').textContent='Click again to permanently remove';
  status('Click the button again within 15 seconds to permanently remove the selected areas in a new PDF copy. The original is preserved.');
  setTimeout(()=>{if(armed&&Date.now()-armed>=ARM_MS){disarm();status('Not applied. Click the button again when you are ready.');}},ARM_MS);
  return;
 }
 disarm();busy=true;draw();try{await save();await api(base()+'/redaction-finalize',{method:'POST',body:JSON.stringify({revision:draft.revision,worker_id:$('#worker').value})});draft.status='queued';$('#editor').hidden=true;$('#finished').hidden=false;dirty=false;void poll();}catch(e){error(e);}finally{busy=false;draw();}
};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
window.addEventListener('pagehide',()=>{clearTimeout(timer);abort.abort();renderTask?.cancel();originalRenderTask?.cancel();void doc?.destroy();void originalDoc?.destroy();});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(doc&&!$('#editor').hidden)void renderPage().catch(error);},150);});
async function loadPdf(jobId,artifactId,expectedHash){
 const response=await fetch(`/v1/jobs/${jobId}/artifacts/${artifactId}/content`,{credentials:'same-origin',cache:'no-store',signal:abort.signal});
 if(!response.ok)throw new Error('Unable to load this private PDF. Reopen the result after signing in.');
 const bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>50000000)throw new Error('PDF exceeds the viewer size limit.');
 if(expectedHash){const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');if(hash!==expectedHash)throw {code:'PDF_VERSION_CHANGED'};}
 return getDocument({data:bytes,verbosity:0,isEvalSupported:false,enableXfa:false,cMapUrl:'/pdfjs/cmaps/',cMapPacked:true,standardFontDataUrl:'/pdfjs/standard_fonts/',wasmUrl:'/pdfjs/wasm/',iccUrl:'/pdfjs/iccs/'}).promise;
}
async function loadOriginal(id){
 try{const j=await api('/v1/jobs/'+id),a=j.artifacts.find(a=>a.kind==='source');if(a){originalDoc=await loadPdf(id,a.id,a.sha256);$('#original-panel').hidden=false;$('.spread').classList.add('has-original');}}
 catch{status('Original preview unavailable. The transformed PDF can still be reviewed.');}
}
async function init(){
 const me=await api('/v1/me');csrf=me.csrf;if(me.role==='viewer'&&!comparison)throw {code:'OWNER_REQUIRED'};
 const id=new URLSearchParams(location.search).get('job');if(!/^[a-f0-9-]{36}$/i.test(id||''))throw new Error('Open a completed PDF from Your documents or Studio.');
 const job=await api('/v1/jobs/'+id);
 if(comparison){
  const result=job.artifacts.find(a=>a.kind==='result');if(job.status!=='succeeded'||!result)throw new Error('This job has no completed PDF to compare.');
  draft={job_id:id,status:'readonly',filename:job.filename};doc=await loadPdf(id,result.id,result.sha256);setWhy(job.metrics?.redaction_map);
  await loadOriginal(job.options.original_job_id||id);document.body.classList.add('comparison');$('#review-title').textContent='Document comparison';
  for(const selector of ['#undo','#redo','#delete','#save'])$(selector).hidden=true;
  $('#start-editing').href='/redaction-review.html?job='+id+'&new=1';$('#start-editing').hidden=me.role==='viewer';
 }else{
 if(job.options.operation==='manual_redaction'&&!(job.status==='succeeded'&&new URLSearchParams(location.search).get('new')==='1')){draft=await api('/v1/jobs/'+id+'/redaction-draft');}
 else{const a=job.artifacts.find(a=>a.kind==='result');if(job.status!=='succeeded'||!a)throw new Error('This job has no completed PDF to edit.');
  const keyName='tx-redaction-create-'+job.id;let key=sessionStorage.getItem(keyName);if(!key){key=crypto.randomUUID();sessionStorage.setItem(keyName,key);}
  const created=await api('/v1/jobs/'+id+'/redactions',{method:'POST',headers:{'Idempotency-Key':key},body:JSON.stringify({artifact_id:a.id,source_sha256:a.sha256})});
  draft=await api('/v1/jobs/'+created.job_id+'/redaction-draft');sessionStorage.removeItem(keyName);history.replaceState(null,'','?job='+draft.job_id);
 }
 $('#filename').textContent=draft.filename;
 if(draft.status!=='uploading'){$('#finished').hidden=false;void poll();return;}
 status('Loading the exact PDF version for this draft…');
 doc=await loadPdf(draft.parent_job_id,draft.parent_artifact_id,draft.source_sha256);
 await loadOriginal(draft.original_job_id||draft.parent_job_id);
 marks=copy(draft.regions);
 await loadQueue();
 }
 $('#filename').textContent=draft.filename;
 if(doc.numPages>200)throw new Error('This editor supports up to 200 pages.');
 if(marks.some(r=>r.page>doc.numPages))throw new Error('A saved selection references a missing page.');
 for(let n=1;n<=doc.numPages;n++){$('#page').add(new Option('Page '+n,n));const b=document.createElement('button');b.textContent='Page '+n;b.dataset.page=n;b.onclick=()=>{page=n;selected=-1;void renderPage().catch(error);};$('#pages').append(b);}
 $('#editor').hidden=false;await renderPage();if(!comparison)await workers();status(comparison?'Compare both PDFs, change pages together, or enter full screen.':'Draw additional areas, review all pages, then save or apply.');
}
void init().catch(error);
