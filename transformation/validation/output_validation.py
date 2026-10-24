"""Independent artifact inspection, including non-visible package content."""
from io import BytesIO
import re
import zipfile
import xml.etree.ElementTree as ET
import pymupdf


def normalized(value):
    from urllib.parse import unquote
    return re.sub(r'\s+','',unquote(value)).casefold()


def inspect_output(data,kind,values=(),min_font_size=None,font_regions=(),intended_overlays=()):
    residual=[]; uninspected=[]; geometry=[]; candidates=[]
    import math
    for region in font_regions:
        if not isinstance(region.get('minimum'),(int,float)) or not math.isfinite(region['minimum']) or not 4<=region['minimum']<=8:
            raise ValueError('Invalid source-relative font validation region.')
    def check(text,location):
        for value in values:
            if normalized(value) and normalized(value) in normalized(text):
                residual.append({'location':location,'value':value})
    if kind=='pdf':
        with pymupdf.open(stream=data,filetype='pdf') as pdf:
            check(str(pdf.metadata),'PDF metadata'); check(pdf.get_xml_metadata(),'PDF XML metadata')
            for number,page in enumerate(pdf,1):
                check(page.get_text(),'page '+str(number))
                text_spans=[]
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines',[]):
                        for span in line['spans']:
                            text_spans.append(span)
                            minimum=min_font_size
                            matching=[r['minimum'] for r in font_regions if r['page']==number and
                                      (pymupdf.Rect(r['bbox'])+(-.25,-.25,.25,.25)).contains(pymupdf.Rect(span['bbox']))]
                            if minimum is not None and matching:minimum=min(minimum,min(matching))
                            if minimum is not None and span['text'].strip() and span['size']<minimum-.05:
                                geometry.append({'page':number,'reason':'text below configured font limit',
                                                 'text':span['text'],'font_size':span['size'],'minimum':minimum})
                            if not page.rect.contains(pymupdf.Rect(span['bbox'])):
                                geometry.append({'page':number,'reason':'text outside page','text':span['text']})
                for image in page.get_image_info():
                    uninspected.append({'page':number,'kind':'raster','bbox':image['bbox']})
                    rect=pymupdf.Rect(image['bbox'])
                    if rect.get_area()<page.rect.get_area()*.85:
                        for span in text_spans:
                            overlap=(rect & pymupdf.Rect(span['bbox'])).get_area()
                            if overlap>pymupdf.Rect(span['bbox']).get_area()*.2:
                                row={'page':number,'kind':'image_text_overlap','text':span['text'],'bbox':span['bbox'],'image_bbox':image['bbox'],
                                     'classification':'unverified_overlap'}
                                for proof in intended_overlays:
                                    if (proof['page']==number and proof['text']==span['text'] and
                                        max(abs(a-b) for a,b in zip(proof['bbox'],span['bbox']))<=.5 and
                                        (pymupdf.Rect(proof['image_bbox'])+(-.5,-.5,.5,.5)).contains(rect)):
                                        row.update(classification='intended_raster_text_placement_requires_review',region_id=proof['region_id'])
                                        break
                                candidates.append(row)
                from transformation.validation.overlap_index import candidate_pairs
                rectangles=[pymupdf.Rect(span['bbox']) for span in text_spans]
                areas=[rect.get_area() for rect in rectangles]
                for index,other_index in candidate_pairs(rectangles):
                    overlap=(rectangles[index] & rectangles[other_index]).get_area()
                    if overlap>min(areas[index],areas[other_index])*.35:
                        candidates.append({'page':number,'kind':'text_text_overlap',
                                           'text':[text_spans[index]['text'],text_spans[other_index]['text']]})
                for link in page.get_links(): check(str(link),'page '+str(number)+' link')
                for annot in page.annots() or []: check(str(annot.info),'page '+str(number)+' annotation')
            for index in range(pdf.embfile_count()):
                name=pdf.embfile_names()[index]; check(name,'attachment name')
                uninspected.append({'kind':'attachment','name':name})
    else:
        def package(payload,prefix='',depth=0):
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                if sum(i.file_size for i in archive.infolist())>300_000_000: raise ValueError('Validation package exceeds size limit.')
                for item in archive.infolist():
                    part=archive.read(item); location=prefix+item.filename
                    if item.filename.endswith(('.xml','.rels')):
                        root=ET.fromstring(part)
                        check(''.join(root.itertext()),location)
                        check(' '.join(v for node in root.iter() for v in node.attrib.values()),location+' attributes')
                    elif '/embeddings/' in item.filename:
                        if zipfile.is_zipfile(BytesIO(part)) and depth<2: package(part,location+'/',depth+1)
                        else:
                            check(part.decode('utf-8',errors='ignore'),location)
                            check(part.decode('utf-16-le',errors='ignore'),location)
                            uninspected.append({'kind':'embedded_binary','part':location})
                    elif '/media/' in item.filename:
                        uninspected.append({'kind':'image','part':location})
        package(data)
    return {'known_sensitive_residuals':residual,'geometry_blockers':geometry,
            'layout_review_candidates':candidates,
            'uninspected_content':uninspected,'privacy_complete':False if values else None,
            'acceptable':not residual and not geometry and not uninspected and not candidates}
