"""Create the backend teammate snapshot without altering the processing engine."""
import hashlib
import html
import json
from pathlib import Path
import shutil
import textwrap
import zipfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Preformatted, Table, TableStyle, Spacer, PageBreak
from reportlab.graphics.shapes import Drawing, Rect, String, Line

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/backend-handoff-20261017'
BUNDLE = OUT / 'TransformX_Backend_Handoff'
PROJECT = BUNDLE / 'project'

PAGES = [
('TransformX Backend Development Handoff', [
('p','Purpose: give the backend teammate the current processing engine and a concrete integration contract so TransformX can become a hosted web application. This is a source code handoff dated 17 October 2026. An HTTP backend, database, durable queue and hosted frontend are not implemented in this snapshot.'),
('h','Keep the engine and replace the presentation layer'),
('p','Streamlit currently collects uploads, sets options and consumes a synchronous generator. The reusable entry point is transformation.workflows.run_workflow, with run_translation and run_pii convenience functions. The new service should call that entry point from an isolated worker, not import or execute transform_app.py or ui/transformx.py.'),
('diagram',''),
('p','Proposed stack: FastAPI for the HTTP API, a durable Redis backed queue such as Celery or RQ, PostgreSQL for job and artifact metadata, and private filesystem or object storage for documents. These are proposed backend choices, not existing project dependencies. Start with one document worker and serialize GPU requests until concurrency has been measured.'),
('h','Current verification and limitations'),
('p','Both the fallback and corrected Deep Doc translation batches generated all six PDFs. Table geometry F1 was 1.000 and image placement F1 was 0.966 at IoU 0.50 and 0.75. Layout quality did not materially improve; Sample01 still has uncertain OCR and Sample04 has unresolved image or layout findings. The six document PII GPU evaluation is pending. A new service must preserve these limitations and must not present generated output as approved.'),
('p','The PII teammate owns detector and redaction quality. The backend teammate owns upload and job APIs, isolation, persistence, access control, progress, downloads and deployment. Both reuse the same engine and IR contracts. Do not fork separate extraction implementations for the API and PII work.')]),
('Code map and engine boundary', [
('table',[['Path','Use in the new service'],['backend_examples/worker_adapter.py','Framework neutral reference adapter with tests; not an API server'],['transformation/workflows.py','Independent operations and model defaults'],['transformation/pipeline.py','Synchronous coordinator; emits progress and final manifest'],['extraction/coordinator.py','Extraction routing and shared content IR'],['extraction/deep_doc_adapter.py and deep_doc_stage1/','Prior extraction integration with coverage and geometry guards'],['extraction/figures.py','Render and mask figure candidates and assets'],['transformation/ocr/ and tools/ocr/','Paddle OCR transport, workers, caches and evidence'],['transformation/llm/','TranslateGemma and Qwen transports, prompts and protection'],['transformation/pdf/ and layout/','BabelDOC, scan reconstruction and text fitting'],['transformation/validation/','PII detection, redaction validation and output checks'],['transformation/office/','Native Office extraction and package reconstruction'],['tools/evaluation/ and tests/','Offline evaluations and regression coverage'],['ui/ and transform_app.py','Current local UI; use only to understand existing controls']]),
('h','The worker integration example'),
('code','from backend_examples.worker_adapter import EngineSettings, execute\n\nsettings = EngineSettings(\n    run_root=SERVER_JOB_DIRECTORY,\n    babeldoc_python=SERVER_BABELDOC_PYTHON,\n    ocr_remote=SERVER_OCR_SETTINGS,\n)\nresult = execute(upload_bytes, display_filename, validated_options,\n                 settings, emit=persist_progress, publish=store_artifact)'),
('p','SERVER_JOB_DIRECTORY must be allocated by the backend per job. persist_progress must durably store safe public events. store_artifact must copy or register a private artifact and return an opaque ID. The example does not implement these callbacks, authentication, scheduling, cancellation or database transactions. Validation in the example is a starting boundary, not complete upload security.')]),
('Proposed API and client options', [
('p','backend_examples/openapi.proposed.json defines an initial contract. It is a proposal to implement, not an already deployed endpoint. Use multipart upload so binary documents do not have to be base64 encoded.'),
('table',[['Endpoint','Behavior to implement'],['POST /v1/jobs','Validate file and JSON options; persist job; return 202 and job ID'],['GET /v1/jobs/{job_id}','Owner scoped status, current stage, elapsed time and artifact IDs'],['GET /v1/jobs/{job_id}/events','Authenticated SSE progress stream with persisted event sequence IDs'],['GET /v1/jobs/{job_id}/artifacts/{artifact_id}','Authorized download of a registered artifact'],['POST /v1/jobs/{job_id}/cancel','Record cancellation request; worker confirms terminal cancellation'],['GET /health/live','Process liveness only; never claim model readiness']]),
('h','Multipart options JSON example'),
('code','{\n  "operation": "translation",\n  "source_language": "en",\n  "target_language": "ko",\n  "output_format": "pdf",\n  "protected_values": [],\n  "document_context": "",\n  "pii_categories": []\n}'),
('p','operation is translation, pii or combined. Translation requires target_language. PII and combined require selected categories. The six exact category strings are Email addresses, Phone numbers, Names, Addresses, Identifiers and Bank accounts. Combined mode currently redacts before translation. Preserve manual names or values and document context fields for translation. They are not PII exemptions.'),
('p','The API must not accept engine paths, private keys, SSH hosts, endpoints, model download commands or arbitrary root directories from clients. Keep model selection and model service addresses under server configuration. Verify the actual file content and size, not just its filename. The example proposes a 50 MB upload bound and same format output; confirm those product choices before releasing the API.'),
('p','Begin acceptance testing with PDF to PDF, then enable DOCX to DOCX and PPTX to PPTX after their own tests. Existing cross format export is a prototype route and should not be advertised as fidelity equivalent. Public errors should use stable codes and a job ID; detailed traces and document text belong in private diagnostics.')]),
('Jobs storage and progress', [
('p','A document can take several minutes and spawn OCR and BabelDOC subprocesses. HTTP requests should return quickly after durable enqueueing. Use a separate worker process; do not run the engine inline in an async route. FastAPI documents a heavier task queue approach for substantial background computation. Reference: https://fastapi.tiangolo.com/tutorial/background-tasks/'),
('table',[['Entity','Minimum fields'],['Job','id, owner_id, operation, options snapshot, source hash, state, timestamps, engine run ID, attempt number'],['Artifact','id, job_id, private storage key, type, size, hash, review status'],['Job event','job_id, monotonic sequence, stage, elapsed seconds, public status'],['Job attempt','job_id, attempt ID, worker, model digest, settings hash, private diagnostic location'],['Review record','job_id, reviewer, independent findings, timestamp and acceptance decision']]),
('p','Store PDFs, image assets and large IR files in private artifact storage. Store searchable metadata in PostgreSQL. Existing JSON manifests remain scientific and diagnostic evidence, not a replacement for a concurrent multiuser job database. Never serve the whole runs directory as static files.'),
('code','Service states:\nqueued -> running -> succeeded | failed\nqueued or running -> cancelling -> cancelled\n\nQuality state is separate:\nnot_reviewed -> requires_review -> accepted | requires_revision'),
('p','Map the engine generated status to service succeeded only when the final artifact exists and has been registered. The engine can finish with unresolved validation findings. The worker example therefore always returns requires_review on success. A failed final engine event is a failure even when the generator returned normally; caught engine exceptions are represented by failed events.'),
('p','Add retries as new attempts with immutable input and settings provenance. Use idempotency keys for job submission and handle queue redelivery without publishing duplicate artifacts. Resume in the offline evaluation runner is not a durable web job retry protocol. A cancellation request is not proof that child processes or GPU inference stopped; implement scoped process ownership, timeouts and cleanup before exposing confirmed cancellation.'),
('p','Keep SSE and polling based on persisted events so browser refreshes and API restarts do not lose status. The engine does not report a reliable percent complete; display actual stage and elapsed time. Account for worker restarts and stale job leases.')]),
('Runtime setup and deployment', [
('p','Install the engine first in a local Python 3.11 environment. Keep BabelDOC 0.6.4 isolated in its own environment. The API and worker dependencies should be pinned separately by the backend teammate; do not mix a web framework upgrade into the established BabelDOC environment.'),
('code','py -3.11 -m venv .venv-engine\n.\\.venv-engine\\Scripts\\python.exe -m pip install -r requirements.txt -r requirements-deep-doc.txt\n\npy -3.11 -m venv .venv-babeldoc\n.\\.venv-babeldoc\\Scripts\\python.exe -m pip install -r requirements-transformation.txt\n\n.\\.venv-engine\\Scripts\\python.exe -m unittest tests.test_backend_adapter tests.test_separate_workflows -v'),
('p','requirements.txt currently contains Streamlit for the local UI. It can remain during engine reproduction. Split API and engine requirements only after import and regression tests demonstrate the correct dependencies. The API process should not import transformation.pipeline at startup if it does not need models or heavy extraction dependencies; let the worker own the engine environment.'),
('h','Models and OCR'),
('p','Translation uses translategemma:12b; PII uses qwen3.5:9b. tools/gpu/setup_transformx.sh installs Ollama, GPU PaddleOCR and model caches on a Linux pod. Translation and PII can share the GPU serially; GPU memory and throughput limits need measurement. The present engine validates loopback endpoints or SSH tunnels. It does not accept an arbitrary public remote Ollama URL. Hosting must preserve that boundary with colocated access or a managed private tunnel.'),
('p','The existing remote OCR worker executes via SSH and uses /workspace/paddle-env/bin/python. Configure these service owned credentials and worker paths. No SSH key or live pod address is in this package. A container deployment must install system PDF and image dependencies, supply the correct BabelDOC interpreter, mount persistent job storage and test on Linux; the current Windows workflow is not proof that Linux container deployment is complete.'),
('h','First hosted milestone'),
('p','Deploy one API service, one queue and one engine worker with persistent storage and one test user. Reproduce one PDF job through upload, progress and download before adding concurrent workers or autoscaling. Add TLS, owner checks, retention and deletion policies, request limits and worker isolation before accepting other users documents. Keep original documents and outputs out of public logs and source control.')]),
('Implementation plan and handover checks', [
('table',[['Step','Deliverable'],['1','Reproduce engine tests and read the worker adapter and core workflows'],['2','Build FastAPI routes and strict request models from the proposed contract'],['3','Persist owner scoped jobs and artifacts; enqueue jobs durably'],['4','Implement worker callbacks and preserve private manifests and IR evidence'],['5','Add polling, SSE replay and authorized artifact streaming'],['6','Implement failure recovery, idempotency and scoped cancellation'],['7','Integrate a separate frontend using the same user operation controls'],['8','Run all six PDFs and compare artifacts with the frozen baseline'],['9','Integrate the PII teammate improvements and run reviewed privacy tests'],['10','Document deployment, environment variables, recovery and operational limits']]),
('h','Backend acceptance tests'),
('p','Test upload rejection for invalid PDF and Office packages, unauthorized job access, cross user downloads, forged artifact IDs and attempts to inject server options. Test a normal translation job, a PII only job with no translation call, a combined job and an engine failed event. Confirm public events contain no absolute server paths or document text.'),
('p','Test broker or worker restart, duplicate delivery, client disconnect, cancellation during OCR and BabelDOC, retention cleanup and concurrent jobs. Ensure one job cannot read or delete another job directory. Document jobs that fail before model inference separately from GPU placement checks. Store package versions and model digests with each attempt.'),
('h','Quality and evaluation continuity'),
('p','The backend should preserve source-ir.json, transformed-ir.json, extraction evidence, validation reports and output hashes privately, so tools/evaluation can score saved runs. Human reviews are specific to source and output hashes. Do not mark new output accepted because a previous run was reviewed. The PII teammate owns exact span precision, recall, F1 and residual review; the backend owns retaining and retrieving the evidence needed for those evaluations.'),
('p','Sample01 OCR and Sample04 image or layout findings remain unresolved. Native Office and cross format routes need their own dataset evaluation. No automatic check currently certifies complete PII removal. Hosting the engine will not fix document quality defects; those should stay visible in review status and warnings.'),
('h','Contents of this package'),
('p','project contains the reusable source, local UI for reference, engine requirements, tests, a proposed OpenAPI contract and the worker example. START_HERE.txt gives the order of work. Samples are provided for reproduction. The backend handoff has no live credentials, environments, model weights, database or running API. The previous PII ZIP remains a separate deliverable for the PII teammate.')])
]


def render_report(path):
    styles = {
        'p': ParagraphStyle('body',fontName='Helvetica',fontSize=10,leading=13,spaceAfter=8),
        'title': ParagraphStyle('title',fontName='Helvetica-Bold',fontSize=21,leading=25,spaceAfter=15),
        'h': ParagraphStyle('heading',fontName='Helvetica-Bold',fontSize=13,leading=16,spaceBefore=5,spaceAfter=8),
        'code': ParagraphStyle('code',fontName='Courier',fontSize=8,leading=10,spaceAfter=10),
        'cell': ParagraphStyle('cell',fontName='Helvetica',fontSize=9,leading=11)}
    flow=[]; width=A4[0]-90
    for n,(title,blocks) in enumerate(PAGES):
        if n: flow.append(PageBreak())
        flow.append(Paragraph(html.escape(title),styles['title']))
        for kind,content in blocks:
            if kind in ('p','h'): flow.append(Paragraph(html.escape(content),styles[kind]))
            elif kind=='code':
                lines=[]
                for line in content.splitlines(): lines.extend(textwrap.wrap(line,92,replace_whitespace=False,drop_whitespace=False) or [''])
                flow.append(Preformatted('\n'.join(lines),styles['code']))
            elif kind=='table':
                rows=[[Paragraph(html.escape(c),styles['cell']) for c in row] for row in content]
                table=Table(rows,colWidths=[width*.38,width*.62],repeatRows=1)
                table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#D9D9D9')),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE6F1')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
                flow.extend([table,Spacer(1,8)])
            elif kind=='diagram':
                drawing=Drawing(width,115)
                for i,label in enumerate(['Web frontend','HTTP API','Queue','Engine worker','Private storage']):
                    x=i*102; drawing.add(Rect(x,65,94,35,fillColor=colors.HexColor('#DCE6F1'),strokeColor=colors.HexColor('#7592AD')))
                    drawing.add(String(x+47,79,label,fontName='Helvetica-Bold',fontSize=8,textAnchor='middle'))
                    if i<4:
                        drawing.add(Line(x+94,82,x+102,82,strokeColor=colors.black))
                drawing.add(Rect(300,10,196,32,fillColor=colors.HexColor('#EFF3F6'),strokeColor=colors.HexColor('#7592AD')))
                drawing.add(String(398,23,'Ollama and PaddleOCR GPU services',fontSize=9,textAnchor='middle'))
                drawing.add(Line(350,65,350,42,strokeColor=colors.black))
                flow.append(drawing)
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.drawString(45,25,'TransformX | Backend teammate handoff | 17 October 2026')
        canvas.drawRightString(A4[0]-45,25,str(doc.page))
    SimpleDocTemplate(str(path),pagesize=A4,leftMargin=45,rightMargin=45,topMargin=40,bottomMargin=42,
        title='TransformX Backend Development Handoff',author='TransformX').build(flow,onFirstPage=footer,onLaterPages=footer)


def contract():
    schemas={
        'Options':{'type':'object','additionalProperties':False,'required':['operation'], 'properties':{
            'operation':{'type':'string','enum':['translation','pii','combined']},
            'source_language':{'type':'string','default':'en'},'target_language':{'type':'string'},
            'output_format':{'type':'string','enum':['pdf','docx','pptx']},
            'protected_values':{'type':'array','maxItems':200,'items':{'type':'string','maxLength':500}},
            'document_context':{'type':'string','maxLength':8000},
            'pii_categories':{'type':'array','items':{'type':'string','enum':['Email addresses','Phone numbers','Names','Addresses','Identifiers','Bank accounts']}}}},
        'Job':{'type':'object','required':['job_id','status','review_status'],'properties':{
            'job_id':{'type':'string'},'status':{'type':'string','enum':['queued','running','cancelling','cancelled','succeeded','failed']},
            'review_status':{'type':'string','enum':['not_reviewed','requires_review','accepted','requires_revision']},
            'stage':{'type':'string'},'elapsed_seconds':{'type':'number'},
            'artifact_ids':{'type':'array','items':{'type':'string'}},'error_code':{'type':'string'}}}}
    job_parameter={'name':'job_id','in':'path','required':True,'schema':{'type':'string'}}
    public={'description':'Job state; filter paths and private diagnostics.','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Job'}}}}
    paths={'/v1/jobs':{'post':{'operationId':'submitJob','description':'PROPOSED. Parse options JSON using Options and validate operation dependent fields; enforce owner and upload limits.',
        'requestBody':{'required':True,'content':{'multipart/form-data':{'schema':{'type':'object','required':['file','options'],'properties':{'file':{'type':'string','format':'binary'},'options':{'type':'string','description':'JSON serialized Options object.'}}}}}},
        'responses':{'202':public,'400':{'description':'Invalid file or options'},'401':{'description':'Unauthenticated'},'413':{'description':'Upload exceeds configured limit'}}}},
        '/v1/jobs/{job_id}':{'parameters':[job_parameter],'get':{'operationId':'getJob','responses':{'200':public,'404':{'description':'Missing or inaccessible job'}}}},
        '/v1/jobs/{job_id}/events':{'parameters':[job_parameter],'get':{'operationId':'streamJobEvents','description':'Persisted SSE events; authorize access and support Last-Event-ID replay.','responses':{'200':{'description':'Progress stream','content':{'text/event-stream':{'schema':{'type':'string'}}}},'404':{'description':'Missing or inaccessible job'}}}},
        '/v1/jobs/{job_id}/cancel':{'parameters':[job_parameter],'post':{'operationId':'cancelJob','description':'202 acknowledges request, not completed cancellation.','responses':{'202':public,'409':{'description':'Already terminal'},'404':{'description':'Missing or inaccessible job'}}}},
        '/v1/jobs/{job_id}/artifacts/{artifact_id}':{'parameters':[job_parameter,{'name':'artifact_id','in':'path','required':True,'schema':{'type':'string'}}],
            'get':{'operationId':'downloadArtifact','responses':{'200':{'description':'Authorized artifact stream','content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}},'404':{'description':'Missing or inaccessible artifact'}}}},
        '/health/live':{'get':{'operationId':'liveness','security':[],'responses':{'200':{'description':'API process alive; not a model readiness check'}}}}}
    return {'openapi':'3.0.3','info':{'title':'TransformX proposed backend API','version':'0.1.0-proposal','description':'Handoff contract only; no server is implemented.'},
        'servers':[{'url':'http://localhost:8000'}],'security':[{'bearerAuth':[]}], 'paths':paths,
        'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}},'schemas':schemas}}


def main():
    PROJECT.mkdir(parents=True,exist_ok=False)
    for folder in ('transformation','extraction','ui','evaluation','tests','backend_examples'):
        for path in (ROOT/folder).rglob('*.py'):
            if '__pycache__' in path.parts: continue
            target=PROJECT/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for folder in ('tools/evaluation','tools/ocr'):
        shutil.copytree(ROOT/folder,PROJECT/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (PROJECT/'tools').mkdir(exist_ok=True);shutil.copy2(ROOT/'tools/__init__.py',PROJECT/'tools/__init__.py')
    (PROJECT/'tools/gpu').mkdir();shutil.copy2(ROOT/'tools/gpu/setup_transformx.sh',PROJECT/'tools/gpu/setup_transformx.sh')
    for name in ('transform_app.py','app.py','main.py','babeldoc_adapter.py','Start TransformX.cmd','Start Lab.cmd','LICENSE','.gitignore'):
        shutil.copy2(ROOT/name,PROJECT/name)
    for path in ROOT.glob('requirements*.txt'): shutil.copy2(path,PROJECT/path.name)
    shutil.copytree(ROOT/'config',PROJECT/'config')
    (PROJECT/'docs').mkdir();(PROJECT/'configs').mkdir()
    shutil.copy2(ROOT/'docs/CODE_MAP.txt',PROJECT/'docs/CODE_MAP.txt')
    shutil.copy2(ROOT/'configs/gpu-evaluation.example.json',PROJECT/'configs/gpu-evaluation.example.json')
    spec=contract()
    for target in (ROOT/'backend_examples/openapi.proposed.json',PROJECT/'backend_examples/openapi.proposed.json'):
        target.write_text(json.dumps(spec,indent=2),encoding='utf-8')
    text='\n\n'.join(title+'\n'+'\n\n'.join('\n'.join(' | '.join(row) for row in c) if k=='table' else c for k,c in blocks if k!='diagram') for title,blocks in PAGES)
    (PROJECT/'docs/BACKEND_HANDOFF.txt').write_text(text,encoding='utf-8')
    (ROOT/'docs/BACKEND_HANDOFF.txt').write_text(text,encoding='utf-8')
    config=json.loads((ROOT/'configs/evaluation-deep-doc-20261017.local.json').read_text())
    for item in config['documents']:
        (PROJECT/'samples').mkdir(exist_ok=True);shutil.copy2(item['path'],PROJECT/'samples'/(item['id']+'.pdf'))
    comparison=json.loads((ROOT/'runs/dataset-evaluation/translation-deep-doc-20261017-v1/comparison-fallback/report.json').read_text(encoding='utf-8'))
    summary={'scope':'Saved translation metrics; no backend load test or PII accuracy claim.',
             'batches':{key:{'total_seconds':value['total_seconds'],'geometry':value['geometry']} for key,value in comparison['batches'].items()}}
    (PROJECT/'docs/translation-baseline-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (BUNDLE/'START_HERE.txt').write_text('TransformX backend teammate handoff\n\n1. Read TransformX_Backend_Development_Handoff.pdf.\n2. Open project/ in VS Code.\n3. Reproduce engine tests; inspect backend_examples/worker_adapter.py.\n4. Implement HTTP routes from backend_examples/openapi.proposed.json.\n5. Add durable queue, private artifact storage, job metadata, user access and a separate frontend.\n\nNo running backend, database, credentials or model weights are included. The existing Streamlit app is retained as a reference. The processing engine is reused unchanged. The PII teammate works on detection and privacy evaluation independently.\n',encoding='utf-8')
    (PROJECT/'README.txt').write_text((BUNDLE/'START_HERE.txt').read_text(encoding='utf-8')+'\nFull code guide: docs/BACKEND_HANDOFF.txt\n',encoding='utf-8')
    render_report(BUNDLE/'TransformX_Backend_Development_Handoff.pdf')
    print('Built',BUNDLE)


if __name__=='__main__': main()
