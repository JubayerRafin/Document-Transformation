"""Streamlit interface for local or full-server document execution."""
import json
import os
from pathlib import Path
import sys
import logging
import pymupdf
import streamlit as st
from transformation.pdf.runner import LANGUAGES
from transformation.office.package_ir import inspect_package
from transformation.validation.privacy import CATEGORIES
from transformation.workflows import run_workflow,check_models

ROOT=Path(__file__).resolve().parents[1]
MIMES={'pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
       'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def main():
    st.set_page_config(page_title='TransformX | Document workspace',page_icon='✦',layout='wide')
    st.markdown('''<style>
    .stApp{background:#f5f7fb;color:#17243b}.block-container{max-width:1400px;padding-top:2rem}
    [data-testid=stSidebar]{background:#eaf0f8}h1,h2,h3{letter-spacing:-.035em}
    .eyebrow{color:#526bd4;font-weight:700;font-size:12px;letter-spacing:.16em}
    .flow{padding:16px 20px;background:#172c4b;color:#e9f2ff;border-radius:12px;margin:20px 0 28px}
    div.stButton>button[kind=primary]{background:#3757d5;border:0;border-radius:10px}
    </style>''',unsafe_allow_html=True)
    st.markdown('<div class="eyebrow">TRANSFORMX / DOCUMENT WORKSPACE</div>',unsafe_allow_html=True)
    st.title('Transform documents. Keep their meaning.')
    st.write('PDF, Word and PowerPoint · Translation · Optional sensitive-information masking')
    st.markdown('<div class="flow">Analyze → Extract → Shared IR → Transform → Reconstruct → Validate</div>',unsafe_allow_html=True)
    setup_path=ROOT/'configs/evaluation.local.json'
    setup=json.loads(setup_path.read_text(encoding='utf-8')) if setup_path.is_file() else {}
    server_path=ROOT/'configs/server.local.json'
    server_setup=json.loads(server_path.read_text(encoding='utf-8')) if server_path.is_file() else {}
    remote_defaults=server_setup.get('ssh') or setup.get('ocr_remote') or {}
    with st.sidebar:
        st.subheader('Processing setup')
        st.caption('Local Streamlit prototype')
        translation_model=st.text_input('Translation model',value='translategemma:12b')
        pii_model=st.text_input('PII model',value='qwen3.5:9b')
        server=st.checkbox('Run entire pipeline on GPU server',value=bool(remote_defaults.get('host')))
        execution_remote=None
        if server:
            host=st.text_input('Server SSH IP address',value=remote_defaults.get('host',''))
            port=st.number_input('Server SSH port',1,65535,int(remote_defaults.get('port',22)))
            key=st.text_input('SSH private-key file',value=remote_defaults.get('key',str(Path.home()/'.ssh/id_ed25519_nopass')))
            execution_remote={'host':host,'port':port,'key':key}
            endpoint=pii_endpoint='http://127.0.0.1:11434/v1'
            python=''
            st.caption('Extraction, OCR, models, reconstruction and validation run on the server. This laptop handles uploads, progress and downloads. No Ollama tunnel is needed.')
        else:
            endpoint=st.text_input('Model endpoint',value='http://127.0.0.1:11434/v1',key='local-endpoint')
            pii_endpoint=st.text_input('PII endpoint',value=endpoint)
            candidates=[ROOT/'.venv-babeldoc/Scripts/python.exe',*ROOT.parent.glob('outputs/*/.venv-babeldoc/Scripts/python.exe')]
            python=st.text_input('BabelDOC Python executable',value=os.environ.get('TRANSFORMX_BABELDOC_PYTHON',next((str(p) for p in candidates if p.is_file()),sys.executable)))
        minutes=st.number_input('Maximum run time (minutes)',10,180,60,10)
        fresh=st.checkbox('Fresh translation',value=False)
        with st.expander('Extraction options'):
            strategy=st.selectbox('PDF extraction strategy',['auto','native','docling'],help='Auto runs Deep Doc text and render-and-mask extraction, with Docling first on pages with table evidence and recorded fallbacks. Docling explicitly requires successful structural table recognition; missing dependencies or conversion errors stop the run. Native is the reference route.')
            visual=st.checkbox('Visual document understanding',value=False,key='explicit-visual-analysis',help='Translation workflows use the selected translation model for page-image analysis. PII-only uses the PII model. Image and structured-output support must be validated. Office previews require LibreOffice.')
            if visual:
                st.caption(f'For translation, visual analysis uses {translation_model}. For PII-only, it uses {pii_model}. Outputs are provisional layout hints and must pass structural validation.')
            else:
                st.caption('Structural document analysis and PaddleOCR remain active. Translation uses the selected translation model; no separate visual-model request is made.')
            ocr_remote=ocr_python=ocr_recognition_dir=None
            if server:
                st.caption('PaddleOCR runs on the server GPU when OCR is required. Native text uses direct extraction.')
            else:
                mode=st.selectbox('Scanned PDF OCR',['No OCR','PaddleOCR locally','Saved single-page recognition'])
                if mode=='PaddleOCR locally': ocr_python=st.text_input('PaddleOCR Python executable',value=str(ROOT/'.venv-paddleocr/Scripts/python.exe'))
                elif mode=='Saved single-page recognition': ocr_recognition_dir=st.text_input('Completed recognition folder')
        with st.expander('Saved runs'):
            paths=sorted((ROOT/'runs/transformation').glob('*/manifest.json'),reverse=True)
            if paths:
                chosen=st.selectbox('Run',paths,format_func=lambda p:p.parent.name)
                if st.button('Open run'): st.session_state.result=json.loads(chosen.read_text(encoding='utf-8'))
    left,right=st.columns([1,1.1],gap='large')
    with left:
        st.subheader('01 / Your document')
        uploaded=st.file_uploader('Upload PDF, DOCX or PPTX',type=['pdf','docx','pptx'],key='document-upload-v2')
        sample=st.checkbox('Use Sample02',value=False)
        data=uploaded.getvalue() if uploaded else ((ROOT/'samples/Sample02.pdf').read_bytes() if sample else None)
        filename=uploaded.name if uploaded else 'Sample02.pdf'; kind=Path(filename).suffix.lower().lstrip('.')
        if data:
            try:
                if kind=='pdf':
                    with pymupdf.open(stream=data,filetype='pdf') as pdf: st.caption(f'{filename} · {len(pdf)} pages · {len(data)/1024:.0f} KB')
                else: st.json(inspect_package(data,kind),expanded=False)
            except Exception as exc: st.error(str(exc)); data=None
        output=st.selectbox('Output format',['pdf','docx','pptx'],index=['pdf','docx','pptx'].index(kind))
        if output!=kind: st.info('Cross-format Office export retains page artwork as a background and editable text at page coordinates. Drawing objects become images; inspect fitting and table editing.')
        else: st.caption('Native Office output retains original parts, shapes and media. Styled paragraphs use validated run-aligned translation.')
    with right:
        st.subheader('02 / Your changes')
        workflow=st.radio('Workflow',['Translation only','PII only','Translation + PII'],horizontal=True)
        translate=workflow!='PII only'; redact=workflow!='Translation only'
        a,b=st.columns(2)
        source=a.selectbox('Source language',list(LANGUAGES))
        target=b.selectbox('Target language',list(LANGUAGES),index=1,disabled=not translate)
        categories=[]; assist=True
        if redact:
            categories=st.multiselect('PII categories',CATEGORIES,default=['Email addresses','Phone numbers'])
            assist=st.checkbox('Model-assisted detection for semantic categories',value=True)
            st.caption('Selected redaction takes precedence over translation protection. Scanned redaction may mask an entire OCR line. Images, embedded objects and detector omissions require review.')
        protected=st.text_area('Protected names and values — one per line',help='Keep exact values during translation; selected PII redaction takes precedence.')
        context=st.text_area('Document context (optional)',max_chars=2000)
        terms=st.text_area('Preferred terminology (JSON)',value='{}')
        if st.button('Transform document →',type='primary',use_container_width=True,disabled=not data or not (translate or redact) or (translate and source==target) or (redact and not categories)):
            st.session_state.pop('result',None)
            try:
                if not isinstance(data,(bytes,bytearray,memoryview)) or not len(data):
                    raise ValueError('The uploaded document is no longer available. Remove it and upload it again.')
                request_data=bytes(data)
                if not server:check_models(workflow,translation_model,pii_model,endpoint,pii_endpoint,visual_analysis=visual)
                terminology=json.loads(terms)
                if not isinstance(terminology,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in terminology.items()): raise ValueError('Terminology must map source strings to target strings.')
                with st.status('Processing document…',expanded=True) as status:
                    message=st.empty(); counts=st.empty()
                    for event in run_workflow(request_data,filename,workflow,execution_remote=execution_remote,output_kind=output,pii_categories=categories,pii_assist=assist,visual_analysis=visual,table_strategy=strategy,
                        python=python,translation_model=translation_model,pii_model=pii_model,translation_endpoint=endpoint,pii_endpoint=pii_endpoint,source=LANGUAGES[source],target=LANGUAGES[target],root=ROOT/'runs/transformation',timeout=int(minutes)*60,ignore_cache=fresh,
                        protected_values=[v.strip() for v in protected.splitlines() if v.strip()],document_context=context,terminology=terminology,ocr_remote=ocr_remote,ocr_python=ocr_python,ocr_recognition_dir=ocr_recognition_dir,ocr_cache_root=ROOT/'runs/shared-ocr'):
                        message.write(f"{event['stage'].replace('_',' ').capitalize()} · {event['seconds']:.0f}s")
                        if event.get('stage')=='image_ocr':
                            detail=event.get('image_ocr_progress',{}).get('stage') or event.get('image_stage') or 'processing'
                            message.write(f"Image {event.get('image_progress','')} · {detail.replace('_',' ')} · {event['seconds']:.0f}s")
                        counts.caption(f"Completed requests: {event.get('translation_progress',{}).get('requests_completed',0)}")
                        st.session_state.result=event
                    status.update(label='Generated — review required' if event['status']=='generated' else 'Processing failed',state='complete' if event['status']=='generated' else 'error')
            except Exception as exc:
                logging.getLogger(__name__).exception('Document transformation failed')
                st.error(str(exc))
    result=st.session_state.get('result')
    if not result: return
    st.divider(); st.subheader('03 / Review and download')
    if result['status']=='failed': st.error(result.get('error','Processing failed.'))
    elif result['status']=='generated':
        st.warning('Generated output requires review. Structural checks do not establish translation accuracy or complete PII removal.')
        checks=result.get('validation',{}).get('independent_checks',{})
        if checks:
            counts={'Known sensitive residuals':len(checks.get('known_sensitive_residuals',[])),
                    'Layout blockers':len(checks.get('geometry_blockers',[]))+len(checks.get('office_overflow',[])),
                    'Content awaiting inspection':len(checks.get('uninspected_content',[])),
                    'Uncertain OCR regions':len(result.get('scan_acceptance_blockers',[]))}
            st.caption(' · '.join(f'{name}: {count}' for name,count in counts.items()))
            if checks.get('office_render_error'): st.error('Office preview could not be rendered. See Validation for the renderer error.')
        path=Path(result.get('output_file') or result.get('output_pdf') or '')
        if path.is_file(): st.download_button('Download transformed document',path.read_bytes(),file_name=Path(result['source_name']).stem+'-transformed'+path.suffix,mime=MIMES[path.suffix.lstrip('.')])
        if path.suffix=='.pdf' and path.is_file():
            with pymupdf.open(path) as pdf:
                page=st.number_input('Preview page',1,len(pdf),1)
                st.image(pdf[page-1].get_pixmap(matrix=pymupdf.Matrix(1,1)).tobytes('png'))
        elif (Path(result['run_dir'])/'output/office-preview.pdf').is_file():
            preview_path=Path(result['run_dir'])/'output/office-preview.pdf'
            st.download_button('Download rendered Office preview',preview_path.read_bytes(),file_name='office-preview.pdf',mime='application/pdf')
            with pymupdf.open(preview_path) as preview:
                page=st.number_input('Preview page',1,len(preview),1)
                st.image(preview[page-1].get_pixmap(alpha=False).tobytes('png'))
        warnings=result.get('warnings',[])
        if result.get('validation',{}).get('translation_errors'):
            st.error(f"Incomplete translation: {result['validation']['translation_errors']} regions failed. Review the reports before accepting the output.")
        for warning in warnings[:10]: st.warning(warning)
        if len(warnings)>10: st.caption(f'{len(warnings)-10} additional review notes are in the run record.')
    tabs=st.tabs(['Document model','Validation','Run record']); folder=Path(result['run_dir'])
    with tabs[0]:
        st.caption('IR and run records include original content and detected PII. They are not sanitized sharing copies.')
        for name in ['document-analysis.json','deep-doc/extraction-evidence.json','source-ir.json','transformed-ir.json','ir-translations.json','ocr-nativeized-ir.json','pii-findings.json']:
            path=folder/name
            if path.exists():
                with st.expander(name):
                    st.json(json.loads(path.read_text(encoding='utf-8')),expanded=False)
                    st.download_button('Download '+name,path.read_bytes(),file_name=Path(name).name,mime='application/json',key=str(path))
    with tabs[1]:
        st.json(result.get('validation',{}))
        for name in ['region-errors.json','redaction-audit.json','independent-validation.json','office-layout.json','privacy-verification.json','scan-nativeization.json']:
            if (folder/name).exists(): st.json(json.loads((folder/name).read_text(encoding='utf-8')))
        if result.get('engine_run'):
            engine=Path(result['engine_run']['run_dir'])
            st.json(result['engine_run'],expanded=False)
            for name in ['quality-review.json','scan-reconstruction.json','region-errors.json']:
                if (engine/name).exists():
                    with st.expander(name): st.json(json.loads((engine/name).read_text(encoding='utf-8')),expanded=False)
            for part in [engine,*[Path(p['run_dir']) for p in result['engine_run'].get('page_runs',[]) if p.get('run_dir')]]:
                for name in ['babeldoc-source-ir.json','babeldoc-transformed-ir.json','ir-mapping.json']:
                    if (part/name).exists():
                        st.download_button('Download '+part.name+' / '+name,(part/name).read_bytes(),file_name=name,key=str(part/name))
    with tabs[2]: st.json(result,expanded=False)
