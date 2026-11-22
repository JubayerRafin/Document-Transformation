"""Original Deep-Doc Stage 1 adapters plus a native geometry fallback."""
import hashlib
from pathlib import Path
import pymupdf
from extraction.backends.native import extract as native_extract
from extraction.models import Region,clipped_box


def extract_pdf(path,folder,strategy='auto',analysis=None,*,defer_figures=False):
    path,folder=Path(path),Path(folder)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    result=native_extract(path,digest,folder,{},lambda _:None)
    if strategy not in {'auto','native','docling'}: raise ValueError('Unknown table strategy.')
    if strategy in {'auto','docling'}:
        try:
            from extraction.deep_doc_adapter import enrich
            result=enrich(path,folder,result,analysis,strategy,defer_figures=defer_figures)
        except ImportError as exc:
            if strategy=='docling':
                raise RuntimeError('Explicit Docling extraction requires the Deep Doc dependencies in this Python environment: '+str(exc)) from exc
            result.warnings.append('Deep-Doc dependency unavailable: '+str(exc)+'. Native fallback used.')
            result.settings['extraction_evidence']={'strategy':strategy,'status':'native_fallback','error':str(exc)}
    logical_table_text(path,result)
    for page in result.pages:
        page.regions.sort(key=lambda r:(r.bbox[1],r.bbox[0]))
        for i,region in enumerate(page.regions): region.id=f'p{page.number}_r{i+1}'
    if strategy=='native': result.backend='coordinated-native'
    if analysis:
        from transformation.analysis.document_analysis import apply_routing_hints
        apply_routing_hints(result,analysis)
    result.validate()
    return result


RTL_LETTERS=__import__('re').compile(r'[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefe]')


def logical_table_text(path,document):
    """Right-to-left table cells read in the PDF's own character order.

    The table engines (Docling, Camelot, pdfplumber) join a cell's characters from left to right,
    which writes Arabic and Hebrew backwards ("زمرلا" for "الرمز", "code"). The PII label check then
    read every Arabic field label backwards, judged "Full name" and "Employee number" not personal,
    and an employee number stayed in the output (Arabic invoice, 2026-11-23). A cell with
    right-to-left letters takes the text PyMuPDF reads in its box, only when that has exactly the
    same characters."""
    from collections import Counter
    with pymupdf.open(path) as pdf:
        for page in document.pages:
            source=pdf[page.number-1]
            if source.rotation: continue
            for region in page.regions:
                if region.kind!='table': continue
                for name in ('cell_records','native_grid_cell_records'):
                    for cell in region.metadata.get(name) or []:
                        text=str(cell.get('text') or '')
                        if not RTL_LETTERS.search(text) or not cell.get('bbox'): continue
                        x0,y0,x1,y1=cell['bbox']
                        clip=pymupdf.Rect(x0+.5,y0+.5,x1-.5,y1-.5)
                        if clip.is_empty: continue
                        found=source.get_text('text',clip=clip).strip()
                        same=lambda a,b:Counter(c for c in a if not c.isspace())==Counter(c for c in b if not c.isspace())
                        if found==text or not same(found,text): continue
                        if '\n' not in text: found=' '.join(found.split())
                        cell['text']=found
                        row,column=cell.get('row'),cell.get('column')
                        if (name=='cell_records' and isinstance(row,int) and isinstance(column,int) and region.cells
                                and row<len(region.cells) and column<len(region.cells[row]) and region.cells[row][column]==text):
                            region.cells[row][column]=found


def content_ir(document):
    payload=document.to_dict()
    return {'schema_version':'transformx-content-1','format':'pdf',
            'source_name':document.source_name,'source_sha256':document.source_sha256,
            'backend':document.backend,'settings':document.settings,'warnings':document.warnings,'assets':[],
            'units':[{'id':'page-'+str(p['number']),'kind':'page','number':p['number'],
                      'width':p['width'],'height':p['height'],'regions':p['regions'],'warnings':p['warnings']}
                     for p in payload['pages']]}


def text_regions(ir):
    result=[]
    for unit in ir['units']:
        for region in unit['regions']:
            if region.get('text','').strip(): result.append(region)
            for row,cells in enumerate([] if region.get('metadata',{}).get('in_package_paragraphs') or region.get('metadata',{}).get('in_content_regions') else region.get('cells',[])):
                for col,text in enumerate(cells):
                    if text:
                        records=region.get('metadata',{}).get('cell_records',[])
                        matches=[c for c in records if c.get('row')==row and c.get('column')==col]
                        result.append({'id':region['id']+f':cell-{row}-{col}','text':str(text),
                            'bbox':matches[0]['bbox'] if len(matches)==1 else region['bbox'],
                            'metadata':{'table_parent':region['id'],'row':row,'column':col,
                                'column_header':region.get('cells',[[]])[0][col] if col<len(region.get('cells',[[]])[0]) else '',
                                'row_values':cells}})
    return result
