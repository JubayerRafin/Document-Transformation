(()=>{
'use strict';
const root=document.getElementById('tx-v1'),q=s=>root.querySelector(s),qa=s=>[...root.querySelectorAll(s)];
let user=null,csrf='',mode='translation',jobs=[],selected=null,screen='jobs',experience='product',pending='product',worker=null,eventsAfter=0,eventsJob=null;
const studioRoute=()=>/^\/studio(?:\/|\.html)?$/.test(location.pathname);
const staff=()=>user&&['admin','reviewer','viewer'].includes(user.role);
const escape=t=>String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const seconds=x=>typeof x==='number'?x.toFixed(1)+' s':'Not measured';
function notice(t){q('#notice').textContent=t;}
async function api(url,options={}){
 const headers={...(options.headers||{})};if(options.method&&options.method!=='GET')headers['X-CSRF-Token']=csrf;
 const res=await fetch(url,{...options,headers,credentials:'same-origin'});
 const body=await res.json().catch(()=>({error:'REQUEST_FAILED'}));
 if(!res.ok){if(res.status===401){user=null;account();}throw Error(body.error||'REQUEST_FAILED');}return body;
}
function account(){q('#account-name').textContent=user?user.name+' · '+user.role:'';q('#login-button').hidden=!!user;q('#logout-button').hidden=!user;qa('[data-upload]').forEach(e=>e.hidden=user&&!['admin','customer'].includes(user.role));q('#review-controls').hidden=!user||!['admin','reviewer'].includes(user.role);q('#baseline-tools').hidden=user?.role!=='admin';}
function go(v){
 if(!user){pending=experience;q('#signin').showModal();return;}
 if(['evaluation','system'].includes(v)&&!staff()){notice('This section requires a Studio role.');return;}
 if(v==='new'&&!['admin','customer'].includes(user.role)){notice('This account is read-only for processing.');return;}
 root.dataset.site=experience;q('#application').hidden=false;q('#tx-product-home').hidden=true;
 q('#experience').hidden=false;q('#experience').textContent=experience==='studio'?'Studio':'Product';
 screen=v;qa('[data-page]').forEach(e=>e.hidden=e.dataset.page!==v);qa('.tx-nav button').forEach(b=>b.classList.toggle('selected',b.dataset.view===v));notice('');
 if(v==='review')renderReview();if(v==='status')renderStatus();if(v==='evaluation')renderEvaluation();if(v==='system')renderSystem();
}
function home(){root.dataset.site='landing';q('#application').hidden=true;q('#tx-product-home').hidden=false;q('#experience').hidden=true;}
function enter(which){experience=which;if(which==='studio'&&user&&!staff()){notice('Studio access requires an invited team account.');return;}if(!user){pending=which;q('#signin').showModal();}else go(which==='studio'?'jobs':'new');}
function rows(){const search=q('#search').value.toLowerCase(),filter=q('#filter').value;
 q('#job-rows').innerHTML=jobs.filter(j=>j.filename.toLowerCase().includes(search)&&(filter==='all'||(filter==='baseline'?j.baseline:j.status===filter))).map(j=>`<tr><td><b>${escape(j.filename)}</b><small>${j.pages} pages · ${j.baseline?'Saved baseline':'Live application run'}</small></td><td>${escape(j.options.operation)}</td><td>${escape(j.status)}</td><td><span class="tx-warning">${escape(j.quality)}</span></td><td>${seconds(j.metrics.server_seconds)}</td><td><button data-job="${j.id}">${j.status==='succeeded'?'Review':'Status'}</button></td></tr>`).join('')||'<tr><td colspan="6">No documents yet. Upload a PDF or ask the administrator to share a baseline.</td></tr>';}
function updateSelected(){if(selected)selected=jobs.find(j=>j.id===selected.id)||selected;}
async function refresh(){if(!user)return;try{[jobs,worker]=await Promise.all([api('/v1/jobs'),api('/v1/system')]);rows();updateSelected();q('#worker-label').textContent=worker.worker==='offline'?'Processing worker offline':worker.worker==='busy'?'Processing worker busy':'Processing worker available';if(screen==='status')await renderStatus();if(screen==='system')renderSystem();}catch(e){notice(e.message);}}
function choose(id){selected=jobs.find(j=>j.id===id);eventsAfter=0;eventsJob=null;go(selected?.status==='succeeded'?'review':'status');}
async function renderStatus(){q('#cancel-job').disabled=!selected||selected.status!=='queued'||selected.baseline;if(!selected){q('#status-name').textContent='Select a job from Documents.';return;}q('#status-name').textContent=selected.filename;q('#job-state').textContent=selected.status;q('#status-stage').textContent=selected.stage;q('#status-error').textContent=selected.error_code||'';q('#cancel-job').disabled=selected.status!=='queued'||selected.baseline;
 try{const es=await api('/v1/jobs/'+selected.id+'/events');q('#events').innerHTML=es.map(e=>`<li>${escape(e.stage)} <small>${seconds(e.elapsed_seconds)}</small></li>`).join('');}catch(e){notice(e.message);}}
function artifact(kind){return selected?.artifacts.find(a=>a.kind===kind);}
function imageUrl(a,p){return '/v1/jobs/'+selected.id+'/artifacts/'+a.id+'/pages/'+p;}
function renderPages(){if(!selected)return;const p=Number(q('#page').value)||1,kind=q('#compare').value,left=artifact(kind)||artifact('source'),right=artifact('result');if(!left||!right)return;const referencePage={1:4,2:7,3:10,4:16,5:13};q('#left-preview').src=imageUrl(left,kind==='gemini_reference'?referencePage[p]:p);q('#right-preview').src=imageUrl(right,p);q('#left-label').textContent=kind==='gemini_reference'?'Supplied Gemini reference':'Original';}
async function renderReview(){if(!selected||selected.status!=='succeeded'){q('#review-name').textContent='Select a completed result from Documents.';return;}
 q('#review-name').textContent=selected.filename;q('#review-meta').textContent=selected.options.source_language+' to '+selected.options.target_language+' · '+selected.pages+' pages';q('#review-quality').textContent=selected.quality;
 q('#page').innerHTML=Array.from({length:selected.pages},(_,i)=>`<option value="${i+1}">Page ${i+1}</option>`).join('');q('#compare option[value="gemini_reference"]').disabled=!artifact('gemini_reference');q('#compare').value='source';
 const a=artifact('result');q('#download-output').href='/v1/jobs/'+selected.id+'/artifacts/'+a.id;q('#download-report').href='/v1/jobs/'+selected.id+'/report';
 q('#review-metrics').innerHTML=[['Server processing',seconds(selected.metrics.server_seconds)],['Total including transport',seconds(selected.metrics.total_seconds)],['Recorded blockers',selected.metrics.blockers??'Pending validation'],['Evaluation',selected.metrics.evaluation_status||'pending'],['Source SHA-256',selected.source_sha256],['Output SHA-256',a.sha256]].map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('');renderPages();
 try{const rs=await api('/v1/jobs/'+selected.id+'/reviews');q('#reviews').innerHTML=rs.map(r=>`<div class="tx-panel"><b>${escape(r.reviewer)} · ${escape(r.decision)}</b><p>${escape(r.note)}</p><small>Applies to output ${escape(r.output_sha256.slice(0,16))}…</small></div>`).join('');}catch(e){notice(e.message);}}
async function renderEvaluation(){q('#evaluation-rows').innerHTML=jobs.map(j=>`<tr><td><input type="checkbox" data-baseline-job="${j.id}" ${j.status==='succeeded'?'':'disabled'} aria-label="Select ${escape(j.filename)}"></td><td>${escape(j.filename)}</td><td>${j.source_sha256.slice(0,12)}…</td><td>${seconds(j.metrics.server_seconds)}</td><td>${seconds(j.metrics.total_seconds)}</td><td>${escape(j.metrics.evaluation_status||'pending')}</td></tr>`).join('');try{const bs=await api('/v1/baselines');q('#baselines').innerHTML=bs.map(b=>`<div class="tx-panel"><b>${escape(b.title)}</b><p>${b.job_ids.length} frozen output records</p></div>`).join('')||'<p>No named baseline yet. Imported historical evidence remains in Documents.</p>';}catch(e){notice(e.message);}}
function renderSystem(){if(!worker)return;q('#system-info').innerHTML=[['Database',worker.database],['API version',worker.software_version],['Worker',worker.worker],['GPU readiness','Verified per run; not inferred from heartbeat'],['Access',user.role]].map(([k,v])=>`<dt>${k}</dt><dd>${escape(v)}</dd>`).join('');}
root.addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;try{
 if(b.dataset.view){go(b.dataset.view);return;}if(b.dataset.job){choose(b.dataset.job);return;}if(b.dataset.mode){mode=b.dataset.mode;qa('[data-mode]').forEach(x=>x.classList.toggle('selected',x.dataset.mode===mode));q('#language-options').hidden=mode==='pii';q('#pii-options').hidden=mode==='translation';return;}
 const act=b.dataset.action||b.dataset.act;
 if(b.dataset.productMode){mode=b.dataset.productMode;enter('product');q('[data-mode="'+mode+'"]').click();return;}
 if(['home','site-home'].includes(act))home();
 if(['new','start-product'].includes(act))enter('product');
 if(['studio','enter-studio'].includes(act))enter('studio');
 if(['login','sign-in'].includes(act)){pending='product';q('#signin').showModal();}
 if(['how','how-scroll'].includes(act))q('#tx-how').scrollIntoView({behavior:'smooth'});
 if(act==='feature-scroll')q('#tx-product-features').scrollIntoView({behavior:'smooth'});
 if(act==='product-example'){experience='product';if(user){selected=jobs.find(j=>j.status==='succeeded');go('review');}else{pending='studio';q('#signin').showModal();}}
 if(act==='logout'){await api('/v1/session',{method:'DELETE'});user=null;csrf='';jobs=[];selected=null;account();home();}
 }catch(err){notice(err.message);}});
q('#close-login').addEventListener('click',()=>q('#signin').close());
q('#signin-form').addEventListener('submit',async e=>{e.preventDefault();try{user=await api('/v1/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:q('#username').value,password:q('#password').value})});csrf=user.csrf;q('#password').value='';q('#signin-error').textContent='';q('#signin').close();account();await refresh();experience=pending==='studio'&&staff()?'studio':'product';go(experience==='studio'?'jobs':'new');}catch(err){q('#signin-error').textContent=err.message;}});
q('#upload-form').addEventListener('submit',async e=>{e.preventDefault();const file=q('#file').files[0];if(!file)return;const opts={operation:mode,source_language:q('#source').value,target_language:q('#target').value,output_format:'pdf',protected_values:q('#protected').value.split('\n').map(v=>v.trim()).filter(Boolean),document_context:q('#context').value,pii_categories:mode==='translation'?[]:qa('#pii-options input:checked').map(x=>x.value)};const data=new FormData();data.append('file',file);data.append('options',JSON.stringify(opts));q('#submit-job').disabled=true;try{const r=await api('/v1/jobs',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:data});await refresh();choose(r.job_id);}catch(err){notice(err.message);}finally{q('#submit-job').disabled=false;}});
q('#cancel-job').addEventListener('click',async()=>{if(!selected||selected.status!=='queued'||selected.baseline)return;try{await api('/v1/jobs/'+selected.id+'/cancel',{method:'POST'});await refresh();}catch(e){notice(e.message);}});
q('#record-revision').addEventListener('click',async()=>{if(!selected)return;try{await api('/v1/jobs/'+selected.id+'/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:'requires_revision',note:q('#review-note').value})});await refresh();await renderReview();notice('Review saved against this exact output hash.');}catch(e){notice(e.message);}});
q('#save-baseline').addEventListener('click',async()=>{try{const body={title:q('#baseline-title').value,job_ids:qa('[data-baseline-job]:checked').map(x=>x.dataset.baselineJob)};await api('/v1/baselines',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});await refresh();await renderEvaluation();notice('Baseline saved. Quality states remain unchanged.');}catch(e){notice(e.message);}});
q('#page').addEventListener('change',renderPages);q('#compare').addEventListener('change',renderPages);q('#search').addEventListener('input',rows);q('#filter').addEventListener('change',rows);
async function init(){try{user=await api('/v1/me');csrf=user.csrf;account();await refresh();if(studioRoute()){if(staff()){experience='studio';go('jobs');}else home();}}catch{account();if(studioRoute()){pending='studio';q('#signin').showModal();}}}
init();setInterval(()=>{if(user&&!document.hidden)refresh();},3000);
})();
