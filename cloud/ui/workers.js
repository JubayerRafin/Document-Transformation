let privateWorkerConfig=null;
async function renderWorkers(){
 if(!user)return;
 try{
  const ws=await api('/v1/workers');
  const select=q('#gpu-worker'),previous=select.value;
  select.innerHTML='<option value="">Select your GPU server</option>'+ws.filter(w=>w.enabled).map(w=>`<option value="${escape(w.id)}">${escape(w.name)} · ${w.ready?'Ready':w.online?'Connected, not ready':'Offline'}</option>`).join('');
  if(ws.some(w=>w.id===previous&&w.enabled))select.value=previous;
  q('#worker-rows').innerHTML=ws.map(w=>`<tr><td>${escape(w.name)}</td><td>${!w.enabled?'Revoked':w.ready?'Ready':w.online?'Connected, not ready':'Offline'}</td><td>${escape(w.capabilities?.gpu||'Not reported')}<small>${escape((w.capabilities?.operations||[]).join(', ')||w.capabilities?.note||'Start the worker agent on your server.')}</small></td><td>${w.enabled?`<button type="button" data-revoke-worker="${escape(w.id)}">Revoke access</button>`:''}</td></tr>`).join('')||'<tr><td colspan="4">No GPU servers registered. Register one above or use the worker registration CLI.</td></tr>';
 }catch(e){notice(e.message);}
}
q('#worker-register-form').addEventListener('submit',async e=>{
 e.preventDefault();q('#register-worker').disabled=true;
 try{
  const w=await api('/v1/workers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:q('#worker-name').value})});
  privateWorkerConfig={url:location.origin,worker_id:w.id,token:w.token,supabase_url:w.supabase_url,
   engine_profile:{translation_endpoint:'http://127.0.0.1:11434/v1',pii_endpoint:'http://127.0.0.1:11434/v1',timeout_seconds:3600,
    engine_options:{table_strategy:'auto',pdf_page_mode:'document',http_keepalive:true,nativeized_ir_mode:'reuse',babeldoc_dual_output:false,figure_engine:'ocr-slots',light_pdf_io:true,ocr_cache_mode:'reuse',translation_qps:8}}};
  q('#worker-onboarding').hidden=false;await renderWorkers();
 }catch(e){notice(e.message);}finally{q('#register-worker').disabled=false;}
});
q('#download-worker-config').addEventListener('click',()=>{
 if(!privateWorkerConfig)return;
 const link=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(privateWorkerConfig,null,2)],{type:'application/json'}));
 link.href=url;link.download='gpu.worker.local.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
root.addEventListener('click',async e=>{
 const b=e.target.closest('[data-revoke-worker]');if(!b)return;
 if(!confirm('Revoke this worker credential? Queued jobs assigned to it will not run. Running work must finish first.'))return;
 try{await api('/v1/workers/'+b.dataset.revokeWorker+'/revoke',{method:'POST'});await renderWorkers();}catch(e){notice(e.message);}
});
