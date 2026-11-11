// Private drafts reuse non-runnable uploading jobs. Existing SQL claim/finish
// remains unchanged: only explicitly capable, owner-assigned workers claim them.
export function regions(value, Fault, allowEmpty=true){
 if(!Array.isArray(value)||value.length>500||(!allowEmpty&&!value.length))throw new Fault('INVALID_REDACTION_REGIONS');
 return value.map(r=>{
  if(!r||typeof r!=='object'||Object.keys(r).sort().join(',')!=='height,page,width,x,y'||!Number.isInteger(r.page)||r.page<1||r.page>200)throw new Fault('INVALID_REDACTION_REGIONS');
  if(!['x','y','width','height'].every(k=>typeof r[k]==='number'&&Number.isFinite(r[k]))||r.x<0||r.y<0||r.width<=0||r.height<=0||r.x+r.width>1.0000001||r.y+r.height>1.0000001)throw new Fault('INVALID_REDACTION_REGIONS');
  return {page:r.page,x:r.x,y:r.y,width:r.width,height:r.height};
 });
}

export async function manualRoute(c){
 const {sub,method,j,u,body,db,insert,patch,sb,transport,env,BUCKET,MAX,signed,sha,Fault,json,request}=c;
 const own=()=>{if(j.owner!==u.id||!['admin','mentor','customer'].includes(u.role))throw new Fault('OWNER_REQUIRED',403);};
 const draft=()=>{own();if(j.options.operation!=='manual_redaction')throw new Fault('NOT_FOUND',404);};
 const describe=()=>({job_id:j.id,status:j.status,revision:j.request_hash,regions:j.options.regions,
  parent_job_id:j.options.parent_job_id,parent_artifact_id:j.options.parent_artifact_id,
  original_job_id:j.options.original_job_id||j.options.parent_job_id,
  source_sha256:j.source_sha256,filename:j.filename});
 if(sub==='redactions'&&method==='POST'){
  own();if(j.status!=='succeeded')throw new Fault('RESULT_NOT_READY',409);
  const key=request.headers.get('Idempotency-Key');
  if(!key||!/^[A-Za-z0-9_-]{8,120}$/.test(key))throw new Fault('IDEMPOTENCY_KEY_REQUIRED');
  const b=await body();const arts=await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=eq.result');
  const a=arts[0];if(!a||b.artifact_id!==a.id||b.source_sha256!==a.sha256)throw new Fault('PDF_VERSION_CHANGED',409);
  let rows=await db('tx_jobs','?owner=eq.'+u.id+'&idempotency_key=eq.'+key);
  if(!rows.length){
   try{rows=await insert('tx_jobs',{owner:u.id,filename:j.filename.replace(/\.pdf$/i,'')+'-manual.pdf',
    source_sha256:a.sha256,stage:'manual_review',request_hash:crypto.randomUUID(),idempotency_key:key,
    options:{operation:'manual_redaction',parent_job_id:j.id,parent_artifact_id:a.id,original_job_id:j.options.original_job_id||j.id,regions:[]}});}
   catch(e){if(e.message!=='CONFLICT')throw e;rows=await db('tx_jobs','?owner=eq.'+u.id+'&idempotency_key=eq.'+key);}
  }
  const d=rows[0];if(!d||d.options.operation!=='manual_redaction'||d.options.parent_job_id!==j.id||d.source_sha256!==a.sha256)throw new Fault('IDEMPOTENCY_CONFLICT',409);
  return json({job_id:d.id},201);
 }
 if(sub==='redaction-draft'&&method==='GET'){draft();return json(describe());}
 if(sub==='redaction-draft'&&method==='PUT'){
  draft();if(j.status!=='uploading')throw new Fault('DRAFT_ALREADY_FINALIZED',409);
  const b=await body();if(typeof b.revision!=='string'||b.revision!==j.request_hash)throw new Fault('DRAFT_CONFLICT',409);
  const next={...j.options,regions:regions(b.regions,Fault)};
  const rows=await patch('tx_jobs','?id=eq.'+j.id+'&status=eq.uploading&request_hash=eq.'+j.request_hash,
   {options:next,request_hash:crypto.randomUUID(),updated:new Date().toISOString()});
  if(!rows.length)throw new Fault('DRAFT_CONFLICT',409);
  return json({revision:rows[0].request_hash});
 }
 if(sub==='redaction-finalize'&&method==='POST'){
  draft();const b=await body();
  if(j.status!=='uploading')return json({job_id:j.id,status:j.status});
  if(b.revision!==j.request_hash)throw new Fault('DRAFT_CONFLICT',409);
  regions(j.options.regions,Fault,false);
  if(typeof b.worker_id!=='string'||!/^[-a-f0-9]{36}$/i.test(b.worker_id))throw new Fault('SELECT_GPU_WORKER');
  const ws=await db('tx_workers','?id=eq.'+b.worker_id+'&owner=eq.'+u.id+'&enabled=eq.true&select=id,heartbeat,capabilities');
  const w=ws[0];if(!w||Date.now()-Date.parse(w.heartbeat)>=60000||!Number.isFinite(Date.parse(w.heartbeat))||w.capabilities?.ready!==true||!w.capabilities?.operations?.includes('manual_redaction'))throw new Fault('MANUAL_WORKER_REQUIRED',409);
  const parents=await db('tx_jobs','?id=eq.'+j.options.parent_job_id+'&owner=eq.'+u.id+'&status=eq.succeeded');
  const arts=await db('tx_artifacts','?id=eq.'+j.options.parent_artifact_id+'&job_id=eq.'+j.options.parent_job_id+'&kind=eq.result');
  const a=arts[0];if(!parents.length||!a||a.sha256!==j.source_sha256)throw new Fault('PDF_VERSION_CHANGED',409);
  const key=u.id+'/'+j.id+'/source.pdf';
  const existing=await db('tx_artifacts','?job_id=eq.'+j.id+'&kind=eq.source');
  if(!existing.length){
   try{await sb('/storage/v1/object/copy',{method:'POST',body:JSON.stringify({bucketId:BUCKET,sourceKey:a.storage_key,destinationKey:key})});}
   catch(e){
    // Recover a copy completed before a dropped response, verifying its bytes.
    const r=await transport(await signed(key));if(!r.ok)throw e;
    const bytes=await r.arrayBuffer();if(bytes.byteLength>MAX||await sha(bytes)!==a.sha256)throw e;
   }
   try{await insert('tx_artifacts',{job_id:j.id,kind:'source',storage_key:key,filename:j.filename,sha256:a.sha256,size:a.size});}
   catch(e){if(e.message!=='CONFLICT')throw e;}
  }
  const changed=await patch('tx_jobs','?id=eq.'+j.id+'&status=eq.uploading&request_hash=eq.'+j.request_hash,
   {status:'queued',stage:'queued',assigned_worker_id:w.id,updated:new Date().toISOString()});
  if(!changed.length)throw new Fault('DRAFT_CONFLICT',409);
  return json({job_id:j.id,status:'queued'},202);
 }
 return null;
}
