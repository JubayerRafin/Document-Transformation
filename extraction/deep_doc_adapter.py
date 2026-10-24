"""Geometry adapters around the original Deep-Doc Stage 1 implementations.

The original extractor algorithms remain unchanged. Translation must retain headers, short
labels and repeated text that the original Markdown-oriented filters removed.
"""
from pathlib import Path
import json
from functools import lru_cache
from threading import RLock
import time
import pymupdf
from extraction.deep_doc_stage1.text_extractor import TextExtractor
from extraction.deep_doc_stage1.image_extractor import ImageExtractor
try:
    from extraction.deep_doc_stage1.table_extractor import TableExtractor
    _TABLE_IMPORT_ERROR = None
except ImportError as exc:
    TableExtractor = object
    _TABLE_IMPORT_ERROR = str(exc)
from extraction.deep_doc_stage1.docling_table_extractor import DoclingTableExtractor
from extraction.deep_doc_stage1.extracted_element import ExtractedElement
from extraction.models import Region, clipped_box, intersection_fraction


class TextAdapter(TextExtractor):
    def __init__(self,tables):
        super().__init__({'min_block_chars':1})
        self.tables=tables

    def _get_table_bboxes(self,page):
        return self.tables.get(page.page_number,[])

    def _word_in_table(self,word,boxes):
        return any(intersection_fraction([word['x0'],word['top'],word['x1'],word['bottom']],b)>=.85 for b in boxes)

    def _is_footer_or_header(self,*args):
        return False

    def _format_text(self,text,*args):
        return text

    def _group_words_into_lines(self,words):
        lines=super()._group_words_into_lines(words)
        result=[]
        for line in lines:
            group=[]
            for word in sorted(line,key=lambda w:w['x0']):
                # A wide horizontal gap separates columns, not a sentence.
                if group and word['x0']-group[-1]['x1']>max(18,2*float(word.get('size',10))):
                    result.append(group); group=[]
                group.append(word)
            if group: result.append(group)
        return result

    def _group_lines_into_blocks(self,lines):
        # Keep geometric lines independent; later paragraph grouping uses boxes.
        return [[line] for line in lines]

    def _summarize_block(self,block):
        _,top,font,bold=super()._summarize_block(block)
        lines=[]
        for line in block:
            text=''
            previous=None
            for word in sorted(line,key=lambda w:w['x0']):
                # pdfplumber splits a word whenever font/style changes. Add a
                # space only when the glyph geometry contains a real gap.
                if previous is not None:
                    tolerance=max(.25,.08*min(float(previous.get('size',10)),float(word.get('size',10))))
                    if word['x0']-previous['x1']>tolerance: text+=' '
                text+=word['text']
                previous=word
            lines.append(text)
        return '\n'.join(lines).strip(),top,font,bold

    def _extract_page(self,page,page_num,median_font,table_bboxes):
        records=[]
        original=self._summarize_block
        def record(block):
            words=[w for line in block for w in line]
            records.append(words)
            return original(block)
        self._summarize_block=record
        try: elements=super()._extract_page(page,page_num,median_font,table_bboxes)
        finally: self._summarize_block=original
        for element,words in zip(elements,records):
            element.bbox=[min(w['x0'] for w in words),min(w['top'] for w in words),max(w['x1'] for w in words),max(w['bottom'] for w in words)]
            element.words=words
        return elements


_DOCLING_LOCK = RLock()


def _build_docling_converter():
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice
    opts=PdfPipelineOptions()
    opts.do_table_structure=True
    opts.do_ocr=False
    opts.generate_page_images=False
    opts.accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU)
    return DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=opts)})


@lru_cache(maxsize=1)
def _cached_docling_converter():
    # Only immutable processing policy is shared. Page results and candidates
    # remain on each adapter. Model construction and conversion are serialized.
    return _build_docling_converter()


class DoclingAdapter(DoclingTableExtractor):
    def _init_docling(self):
        self.errors=[]
        self.page_results=[]
        try:
            started=time.monotonic()
            with _DOCLING_LOCK:
                reused=_cached_docling_converter.cache_info().currsize > 0
                self._converter=_cached_docling_converter()
            self.converter_timing={'seconds':round(time.monotonic()-started,6),'reused_in_process':reused}
            self.available=True
        except Exception as exc:
            self.available=False
            self.errors.append({'stage':'initialization','error':f'{type(exc).__name__}: {exc}'})

    def extract_page(self,path,number,start_index=0):
        if not self.available: return []
        self.geometry={}
        single=None
        try:
            single=self._single_page_pdf(str(path),number)
            with _DOCLING_LOCK:
                result=self._converter.convert(single)
            status=getattr(result.status,'value',str(result.status))
            if status!='success':
                raise RuntimeError(f'Docling conversion status {status}: {getattr(result,"errors",[])}')
            elements=[]
            for index,table in enumerate(result.document.tables,start_index):
                payload=self._table_to_json(table,number,index,result.document)
                record=self.geometry.get(index)
                if payload is None or not record:
                    raise ValueError('Docling table lacks exportable cells or page geometry.')
                element=ExtractedElement(number,record['bbox'][1],'table',json.dumps(payload,ensure_ascii=False))
                element.bbox=record['bbox']; element.matrix=record['matrix']; element.cell_records=record['cells']
                element.source_engine='docling'
                (Path(self.tables_dir)/f'table_p{number}_{index}.json').write_text(element.content,encoding='utf-8')
                elements.append(element)
            self.page_results.append({'page':number,'status':'completed','tables':len(elements)})
            return elements
        except Exception as exc:
            error={'page':number,'stage':'conversion','error':f'{type(exc).__name__}: {exc}'}
            self.errors.append(error)
            self.page_results.append(dict(error,status='failed'))
            return []
        finally:
            if single: Path(single).unlink(missing_ok=True)

    def _table_to_json(self,table,page,index,document):
        result=super()._table_to_json(table,page,index,document)
        if result is None or not table.prov: return None
        height=document.pages[table.prov[0].page_no].size.height
        box=table.prov[0].bbox.to_top_left_origin(height)
        matrix=[['']*table.data.num_cols for _ in range(table.data.num_rows)]
        cells=[]
        for cell in table.data.table_cells:
            row=cell.start_row_offset_idx; column=cell.start_col_offset_idx
            matrix[row][column]=cell.text
            cb=cell.bbox.to_top_left_origin(height) if cell.bbox else None
            cells.append({'row':row,'column':column,'text':cell.text,
                'row_span':cell.end_row_offset_idx-row,'column_span':cell.end_col_offset_idx-column,
                'bbox':[cb.l,cb.t,cb.r,cb.b] if cb else [box.l,box.t,box.r,box.b]})
        self.geometry[index]={'bbox':[box.l,box.t,box.r,box.b],'matrix':matrix,'cells':cells}
        return result


class TableAdapter(TableExtractor):
    def __init__(self,folder,use_docling=False):
        if _TABLE_IMPORT_ERROR:
            raise ImportError(_TABLE_IMPORT_ERROR)
        # Preserve the original Camelot -> pdfplumber orchestration. Expensive
        # Docling remains an explicit strategy, not an unconditional model load.
        super().__init__({'tables':{'use_docling':False,'fallback_flavor':'lattice'}},str(folder))
        self.structural_unavailable=False
        self.docling_candidate=None
        self.engine_calls={'camelot':[],'pdfplumber':[]}
        if use_docling:
            candidate=DoclingAdapter({},str(folder))
            self.docling_candidate=candidate
            if candidate.available: self._docling=candidate
            else: self.structural_unavailable=True

    def _run_camelot(self,path,pages,flavor):
        import camelot
        call={'pages':str(pages),'flavor':flavor}
        self.engine_calls['camelot'].append(call)
        try:
            tables=list(camelot.read_pdf(path,pages=pages,flavor=flavor,suppress_stdout=True))
            call.update(status='completed',tables=len(tables))
            return tables
        except Exception as exc:
            call.update(status='failed',error=f'{type(exc).__name__}: {exc}')
            return []

    def _camelot_table_to_element(self,table,index,path):
        element=super()._camelot_table_to_element(table,index,path)
        if element is None: return None
        with pymupdf.open(path) as pdf: height=pdf[int(table.page)-1].rect.height
        x0,y0,x1,y1=table._bbox
        element.bbox=[x0,height-y1,x1,height-y0]
        element.matrix=table.df.values.tolist()
        element.cell_records=[{'row':r,'column':c,'text':cell.text.strip(),
            'bbox':[cell.x1,height-cell.y2,cell.x2,height-cell.y1],
            'horizontal_span_candidate':cell.hspan,'vertical_span_candidate':cell.vspan}
            for r,row in enumerate(table.cells) for c,cell in enumerate(row)]
        element.source_engine='camelot'
        return element

    def _pdfplumber_fallback(self,path,pages,start):
        import pdfplumber
        elements=[]
        self.engine_calls['pdfplumber'].extend(pages)
        with pdfplumber.open(path) as pdf:
            for number in pages:
                for table in pdf.pages[number-1].find_tables(table_settings={'vertical_strategy':'lines_strict','horizontal_strategy':'lines_strict'}):
                    matrix=table.extract()
                    if len(matrix)<2 or max(map(len,matrix),default=0)<2: continue
                    element=ExtractedElement(number,table.bbox[1],'table',json.dumps(matrix))
                    element.bbox=list(table.bbox); element.matrix=matrix
                    element.source_engine='pdfplumber'
                    element.cell_records=[{'row':r,'column':c,'text':matrix[r][c] or '', 'bbox':list(box)}
                        for r,row in enumerate(table.rows) for c,box in enumerate(row.cells) if box]
                    elements.append(element)
        return elements


class ImageAdapter(ImageExtractor):
    MARGIN_PT=0

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.page_results=[]

    def extract(self,path,page_numbers=None):
        import pdfplumber
        elements=[]
        with pdfplumber.open(path) as pdf:
            for index in self._resolve_pages(page_numbers,len(pdf.pages)):
                try:
                    found=self._extract_page(pdf.pages[index],index+1)
                    elements.extend(found)
                    self.page_results.append({'page':index+1,'status':'completed','candidates':len(found)})
                except Exception as exc:
                    self.page_results.append({'page':index+1,'status':'failed','error':f'{type(exc).__name__}: {exc}'})
        return elements

    def _merge_regions(self,regions):
        self.boxes=super()._merge_regions(regions)
        return self.boxes

    def _extract_page(self,page,number):
        self.boxes=[]
        # Render outside the legacy method's broad exception handler, so a
        # failed render cannot be reported as a successful zero-figure page.
        rendered=page.to_image(resolution=self.DPI)
        class RenderProxy:
            def __getattr__(self,name): return getattr(page,name)
            def to_image(self,**kwargs): return rendered
        elements=super()._extract_page(RenderProxy(),number)
        # The base extractor records top but discards the crop box. Recover its
        # exact box from detected candidates and saved pixel dimensions.
        from PIL import Image
        for element in elements:
            candidates=[b for b in self.boxes if abs(b[1]/self.SCALE-element.y_coordinate)<.01]
            with Image.open(Path(self.images_dir).parent/element.content) as image:
                candidates=[b for b in candidates if abs(min(page.width*self.SCALE,b[2]+5)-max(0,b[0]-5)-image.width)<=2]
            if len(candidates)==1:
                b=candidates[0]
                element.bbox=[max(0,(b[0]-5)/self.SCALE),max(0,(b[1]-5)/self.SCALE),min(page.width,(b[2]+5)/self.SCALE),min(page.height,(b[3]+5)/self.SCALE)]
        return elements


def enrich(path,folder,document,analysis=None,strategy='auto',*,defer_figures=False):
    folder=Path(folder)/'deep-doc'; folder.mkdir(parents=True,exist_ok=True)
    # A page containing only table cells still has a usable native text layer.
    with pymupdf.open(path) as pdf:
        digital=[p.number for p in document.pages if not p.rotation and pdf[p.number-1].get_text().strip()]
    # Auto uses Stage 1's primary Docling route on pages with table evidence.
    # Explicit Docling also probes pages without a native/visual table hint.
    hinted=[v['page'] for v in (analysis or {}).get('visual_pages',[]) if any(r['kind']=='table' for r in v['regions'])]
    structural=strategy=='docling' or bool(hinted) or any(r.kind=='table' for p in document.pages for r in p.regions)
    evidence={'strategy':strategy,'eligible_pages':digital,'tables':{'requested_docling':structural,'accepted':0,'rejected':[]},
              'text':{'engine':'pdfplumber','executed_pages':[],'accepted_pages':[],'native_retained_pages':[]},
              'images':{'engine':'deep-doc-render-and-mask','executed_pages':[],'accepted':0},'errors':[]}
    tables=[]
    extractor=None
    if digital:
        try:
            extractor=TableAdapter(folder,structural)
            if strategy=='docling' and extractor.structural_unavailable:
                raise RuntimeError(f'Docling initialization failed: {extractor.docling_candidate.errors}')
            tables=extractor.extract(str(path),digital)
            candidate=extractor.docling_candidate
            evidence['tables'].update(status='completed',engine_calls=extractor.engine_calls,
                docling_pages=candidate.page_results if candidate else [],docling_errors=candidate.errors if candidate else [],
                candidates=len(tables),converter_timing=getattr(candidate,'converter_timing',None))
            if strategy=='docling' and candidate and candidate.errors:
                raise RuntimeError(f'Docling table extraction failed: {candidate.errors}')
            if extractor.structural_unavailable or (candidate and candidate.errors):
                document.warnings.append('Docling table extraction unavailable or failed; see extraction-evidence.json for Camelot/pdfplumber/native fallback details.')
        except (ImportError,RuntimeError) as exc:
            evidence['tables']['status']='unavailable'
            evidence['errors'].append({'component':'tables','error':f'{type(exc).__name__}: {exc}'})
            if strategy=='docling':
                (folder/'extraction-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
                raise RuntimeError('Explicit Docling extraction could not complete; '+str(exc)) from exc
            document.warnings.append('Deep-Doc table dependencies unavailable: '+str(exc)+'. Native tables retained; text and figures still attempted.')
    else:
        evidence['tables']['status']='not_applicable_no_upright_native_text'
    boxes={p.number:[] for p in document.pages}
    for element in tables:
        page=document.pages[element.page_num-1]
        box=clipped_box(element.bbox,page.width,page.height)
        if not box:
            evidence['tables']['rejected'].append({'page':element.page_num,'reason':'invalid_geometry'})
            continue
        # Require geometry agreement with a native ruled table before removing
        # native text. Other candidates remain diagnostics, avoiding silent loss.
        overlaps=[r for r in page.regions if r.kind=='table' and intersection_fraction(r.bbox,box)>.9 and intersection_fraction(box,r.bbox)>.9]
        # Matching outlines do not establish cell-content coverage. Check every
        # candidate, including ruled tables, and retain duplicate token counts.
        import re
        from collections import Counter
        with pymupdf.open(path) as pdf:
            words=[w for w in pdf[element.page_num-1].get_text('words') if intersection_fraction(w[:4],box)>.85]
        tokenize=lambda text:Counter(re.findall(r'\w+|[^\w\s]',text))
        expected=tokenize(' '.join(w[4] for w in words))
        actual=tokenize(' '.join(str(v or '') for row in element.matrix for v in row))
        missing=expected-actual
        if (not overlaps and len(words)<4) or missing:
            evidence['tables']['rejected'].append({'page':element.page_num,'engine':element.source_engine,
                'reason':'native_word_coverage_guard','missing_tokens':list(missing.elements())})
            continue
        page.regions=[r for r in page.regions if r not in overlaps]
        kept=[]
        for region in page.regions:
            if region.kind!='text': kept.append(region); continue
            if intersection_fraction(region.bbox,box)>.85: continue
            kept.append(region)
        page.regions=kept
        # Structural cell boxes can bound text rather than the drawn grid.
        # Preserve matching native cell outlines for render-and-mask evidence.
        grid=[dict(cell) for original in overlaps for cell in original.metadata.get('cell_records',[])]
        page.regions.append(Region('','table',box,cells=element.matrix,metadata={'method':'deep-doc-stage1-table-adapter',
            'engine':element.source_engine,'cell_records':element.cell_records,'native_grid_cell_records':grid}))
        evidence['tables']['accepted']+=1
    for page in document.pages: boxes[page.number]=[r.bbox for r in page.regions if r.kind=='table']
    text=TextAdapter(boxes).extract(str(path),digital) if digital else []
    image_detector=ImageAdapter({},str(folder)) if not defer_figures else None
    images=image_detector.extract(str(path),digital) if digital and image_detector else []
    evidence['text']['executed_pages']=digital
    evidence['images']['executed_pages']=digital if image_detector else []
    evidence['images']['status']='deferred_to_spatial_pass' if defer_figures else 'completed'
    evidence['images']['page_results']=image_detector.page_results if image_detector else []
    for entry in evidence['images']['page_results']:
        if entry['status']=='failed':
            document.warnings.append(f'Deep-Doc render-and-mask failed on page {entry["page"]}: {entry["error"]}')
    for page in document.pages:
        elements=[e for e in text if e.page_num==page.number]
        if elements:
            # Do not replace a richer native text layer if adapter token coverage
            # is lower. This is an extraction safety check, not a quality score.
            from collections import Counter
            old=Counter(w for r in page.regions if r.kind=='text' for w in r.text.split())
            new=Counter(w for e in elements for w in e.content.split())
            if sum((old-new).values())==0:
                page.regions=[r for r in page.regions if r.kind!='text']
                for e in elements:
                    box=clipped_box(e.bbox,page.width,page.height)
                    if box: page.regions.append(Region('','text',box,e.content,metadata={'method':'deep-doc-stage1-text-adapter','font_size':e.font_size,'bold':e.is_bold,'words':e.words}))
                evidence['text']['accepted_pages'].append(page.number)
            else:
                page.warnings.append('Deep-Doc text coverage check retained the native text fallback.')
                evidence['text']['native_retained_pages'].append(page.number)
        elif page.number in digital:
            evidence['text']['native_retained_pages'].append(page.number)
        for e in images:
            if e.page_num!=page.number or not hasattr(e,'bbox'): continue
            box=clipped_box(e.bbox,page.width,page.height)
            if not box or any(intersection_fraction(box,b)>.4 for b in boxes[page.number]): continue
            if any(r.kind in {'figure','raster_page'} and intersection_fraction(box,r.bbox)>.8 for r in page.regions): continue
            page.regions.append(Region('','figure',box,asset=str(folder/e.content),metadata={'method':'deep-doc-stage1-render-mask','requires_review':True}))
            evidence['images']['accepted']+=1
        # Visual analysis can request a crop missed by raster/vector detectors.
        # It cannot erase text or declare a region correct.
        views=[v for v in (analysis or {}).get('visual_pages',[]) if v['page']==page.number]
        for view in views:
            for hint in view['regions']:
                if hint['kind'] not in {'figure','signature'}: continue
                b=hint['bbox']; box=[b[0]*page.width,b[1]*page.height,b[2]*page.width,b[3]*page.height]
                if any(r.kind in {'figure','raster_page'} and intersection_fraction(box,r.bbox)>.7 for r in page.regions): continue
                if any(r.kind=='text' and intersection_fraction(r.bbox,box)>.1 for r in page.regions):
                    page.warnings.append('Visual figure hint intersects native text; OCR crop withheld.'); continue
                page.regions.append(Region('','figure',box,metadata={'method':'visual_analysis_crop','requires_review':True,'role':hint['kind']}))
    document.settings['deep_doc_modules']=['TextExtractor','TableExtractor','ImageExtractor']
    import importlib.metadata
    document.settings['deep_doc_versions']={}
    for package in ('pdfplumber','camelot-py','docling','pypdf'):
        try: document.settings['deep_doc_versions'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: document.settings['deep_doc_versions'][package]=None
    from collections import Counter
    evidence['tables']['selected_engines']=dict(Counter(r.metadata.get('engine','native') for p in document.pages for r in p.regions if r.kind=='table'))
    evidence['tables']['native_retained_pages']=[p.number for p in document.pages if any(r.kind=='table' and r.metadata.get('method')!='deep-doc-stage1-table-adapter' for r in p.regions)]
    document.settings['extraction_evidence']=evidence
    (folder/'extraction-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    document.warnings=[w for w in document.warnings if not w.startswith('Native reference only:')]
    document.warnings.append('Deep-Doc candidates use native coverage and geometry guards; extraction accuracy still requires review.')
    document.backend='deep-doc-stage1-coordinated'
    document.backend_version='adapter-2'
    return document
