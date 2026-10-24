"""Read-only import of frozen evidence. Never invokes OCR or inference."""
import json, shutil, time
from pathlib import Path
from sqlalchemy import select
from .db import User, Job, Document, JobEvent, identifier
from .service import sha, register, canonical

def import_baseline(sessions,settings,root,owner_name):
    root=Path(root).resolve();report=json.loads((root/'regression/report.json').read_text(encoding='utf8'))
    result=[]
    with sessions() as s:
        user=s.scalar(select(User).where(User.name==owner_name))
        if not user or user.role!='admin':raise ValueError('A local administrator must own the baseline import.')
        owner=user.id
    for row in report['documents']:
        name=row['document'];key=root.name+':'+name
        with sessions() as s:
            old=s.scalar(select(Job).where(Job.baseline_key==key))
            if old:result.append(old.id);continue
        source=root/'regression/pdfs/original'/f'{name}.pdf';output=root/'regression/pdfs/after'/f'{name}.pdf'
        if not source.is_file() or not output.is_file():raise ValueError('Frozen PDF pair is missing: '+name)
        source_hash=sha(source.read_bytes())
        if source_hash!=row['source_sha256']:raise ValueError('Source fingerprint differs from frozen report.')
        after=row['after']
        if after.get('output_sha256') and sha(output.read_bytes())!=after['output_sha256']:raise ValueError('Output fingerprint differs.')
        job_id=identifier();folder=settings.data/'jobs'/job_id;folder.mkdir()
        shutil.copy2(source,folder/'source.pdf');shutil.copy2(output,folder/'result.pdf')
        import pymupdf
        with pymupdf.open(source) as d:pages=d.page_count
        opts={'operation':'translation','source_language':'en','target_language':'ko','output_format':'pdf',
              'protected_values':[],'document_context':'','pii_categories':[]}
        with sessions.begin() as s:
            doc=Document(id=identifier(),owner=owner,filename=name+'.pdf',sha256=source_hash,pages=pages);s.add(doc);s.flush()
            job=Job(id=job_id,owner=owner,document_id=doc.id,status='succeeded',stage='complete',baseline=True,baseline_key=key,
                    quality='requires_revision' if name=='Slide_Test_Samples' else 'requires_review',options=opts,
                    idempotency_key='import-'+job_id,request_hash=sha((source_hash+canonical(opts)).encode()),
                    metrics={'server_seconds':after.get('server_seconds'),'total_seconds':after.get('total_seconds'),
                    'blockers':len(after.get('geometry_blockers',[])), 'layout_flags':len(after.get('layout_flags',[])),
                    'baseline_version':root.name,'evaluation_status':'historical_engineering_review',
                    'acceptance_checked':False,'model':'translategemma:12b',
                    'prior_current_ssim':[p.get('before_after_ssim') for p in row['pages']]})
            s.add(job);s.flush();register(s,settings,job,folder/'source.pdf','source',name+'.pdf');register(s,settings,job,folder/'result.pdf','result',name+'-Korean.pdf')
            if name=='Slide_Test_Samples' and (root/'references/Gemini_NanoBanana_Test.pdf').is_file():
                shutil.copy2(root/'references/Gemini_NanoBanana_Test.pdf',folder/'gemini-reference.pdf');register(s,settings,job,folder/'gemini-reference.pdf','gemini_reference')
            s.add(JobEvent(job_id=job_id,stage='imported_saved_baseline',elapsed=after.get('server_seconds') or 0))
        result.append(job_id)
    return result
