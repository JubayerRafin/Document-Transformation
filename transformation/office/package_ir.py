"""Native OOXML extraction and package-preserving text updates.

Flow documents have story positions, not invented PDF page coordinates.
Source parts and paragraph indices are stable references within this run.
"""
from io import BytesIO
import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
P='http://schemas.openxmlformats.org/presentationml/2006/main'
XML='http://www.w3.org/XML/1998/namespace'


def serialize(root,original):
    # Preserve namespace declarations referenced by mc:Ignorable even when no
    # element uses that namespace. ElementTree otherwise drops those prefixes.
    namespaces={prefix:uri for _,(prefix,uri) in ET.iterparse(BytesIO(original),events=['start-ns'])}
    for prefix,uri in namespaces.items():
        if not re.fullmatch(r'ns\d+',prefix): ET.register_namespace(prefix,uri)
    payload=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    declarations=[]
    from xml.sax.saxutils import quoteattr
    for prefix,uri in namespaces.items():
        name='xmlns'+(':'+prefix if prefix else '')
        if (name+'=').encode() not in payload:
            declarations.append(' '+name+'='+quoteattr(uri))
    if declarations:
        index=payload.find(b'>',payload.find(b'?>')+2)
        payload=payload[:index]+''.join(declarations).encode()+payload[index:]
    return payload


def paragraph_text_nodes(paragraph,tag):
    """Exclude nested textbox paragraphs from the enclosing paragraph text."""
    result=[]
    def walk(node):
        for child in node:
            if child.tag=='{'+tag+'}p': continue
            if child.tag=='{'+tag+'}t': result.append(child)
            else: walk(child)
    walk(paragraph)
    return result


# Run attributes that do not change how text looks: revision ids, proofing marks, language tags.
_NEUTRAL=re.compile(r'\s(?:w:)?rsid\w*="[^"]*"|\s(?:lang|altLang|dirty|err|smtClean|smtId|bmk|noProof)="[^"]*"')
_NEUTRAL_ELEMENTS={'{'+W+'}lang','{'+W+'}noProof','{'+W+'}proofErr','{'+W+'}rtl','{'+W+'}cs'}


def run_key(node,parents,paragraph):
    """What a text node looks like: its run's character properties (without revision ids, proofing
    and language marks) and the hyperlink it sits in. Neighbouring nodes with the same key are one
    piece of formatting; a paragraph is often split into many such runs by editing history alone."""
    run=parents.get(node)
    key=''
    if run is not None:
        props=run.find('{'+W+'}rPr')
        if props is None: props=run.find('{'+A+'}rPr')
        if props is not None:
            clean=ET.Element(props.tag,{k:v for k,v in props.attrib.items() if k.split('}')[-1] not in
                                        {'lang','altLang','dirty','err','smtClean','smtId','bmk','noProof'}})
            for child in props:
                if child.tag not in _NEUTRAL_ELEMENTS: clean.append(child)
            key=_NEUTRAL.sub('',ET.tostring(clean,encoding='unicode'))
    ancestor=run
    while ancestor is not None and ancestor is not paragraph:
        if ancestor.tag=='{'+W+'}hyperlink':
            key+='|link:'+(ancestor.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id') or ancestor.get('{'+W+'}anchor') or '')
            break
        ancestor=parents.get(ancestor)
    return key


def node_breaks(paragraph,tag):
    """For each text node of paragraph_text_nodes(), 'tab' or 'br' when a tab or line break element
    stands between it and the text before it, else None. The text nodes join without them, so a
    form line "Name<tab>Value" would otherwise reach the translator as "NameValue"."""
    breaks=[]; pending=[None]
    def walk(node):
        for child in node:
            if child.tag=='{'+tag+'}p': continue
            local=child.tag.split('}')[-1]
            if local in {'pPr','rPr','tabs','instrText','delText'}: continue
            if child.tag=='{'+tag+'}t':
                breaks.append(pending[0]); pending[0]=None
            elif local in {'tab','ptab'}: pending[0]=pending[0] or 'tab'
            elif local in {'br','cr'}: pending[0]='br'
            else: walk(child)
    walk(paragraph)
    return breaks


def open_package(data,kind):
    archive=zipfile.ZipFile(BytesIO(data))
    entries=archive.infolist()
    if len(entries)>10000 or sum(x.file_size for x in entries)>300_000_000:
        archive.close(); raise ValueError('Office package exceeds prototype limits.')
    if len({x.filename for x in entries})!=len(entries):
        archive.close(); raise ValueError('Office package contains duplicate part names.')
    required='word/document.xml' if kind=='docx' else 'ppt/presentation.xml'
    if required not in archive.namelist():
        archive.close(); raise ValueError('The uploaded package does not match its file type.')
    return archive


def inspect_package(data,kind):
    with open_package(data,kind) as archive:
        names=archive.namelist()
        return {'format':kind,'parts':len(names),'media_assets':sum('/media/' in n for n in names),
                'slides':sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml',n)) for n in names) if kind=='pptx' else None,
                'analysis_method':'OOXML package inspection before content extraction',
                'visual_understanding':'not_run'}


def extract_package(data,filename):
    kind=filename.rsplit('.',1)[-1].lower()
    tag=W if kind=='docx' else A
    ir={'schema_version':'transformx-content-1','source_name':filename,
        'source_sha256':hashlib.sha256(data).hexdigest(),'format':kind,
        'units':[],'assets':[],'warnings':[],'backend':'native-ooxml'}
    with open_package(data,kind) as archive:
        for name in sorted(archive.namelist(),key=lambda n:re.sub(r'\d+',lambda m:m[0].zfill(8),n)):
            if '/media/' in name:
                ir['assets'].append({'id':name,'part':name,'bytes':archive.getinfo(name).file_size})
            prefix='word/' if kind=='docx' else 'ppt/'
            if not name.startswith(prefix) or not name.endswith('.xml'):
                continue
            root=ET.fromstring(archive.read(name))
            paragraphs=list(root.iter('{'+tag+'}p'))
            if not paragraphs:
                continue
            parents={child:parent for parent in root.iter() for child in parent}
            unit={'id':name,'kind':'story' if kind=='docx' else 'slide_or_story','regions':[]}
            for index,paragraph in enumerate(paragraphs):
                nodes=paragraph_text_nodes(paragraph,tag)
                text=''.join(n.text or '' for n in nodes)
                if not text.strip():
                    continue
                ancestor=paragraph; shape=None; cell=None
                while ancestor in parents:
                    ancestor=parents[ancestor]
                    if ancestor.tag in {'{'+P+'}sp','{'+P+'}graphicFrame'} and shape is None:
                        shape=ancestor
                    if ancestor.tag in {'{'+W+'}tc','{'+A+'}tc'} and cell is None:
                        cell=ancestor
                # Placeholder prompts of slide masters and layouts ("Click to edit Master title
                # style") never show on a slide; real master text (a footer, a logo caption) does.
                if (shape is not None and re.match(r'ppt/(slideMasters|slideLayouts|notesMasters|handoutMasters)/',name)
                        and shape.find('.//{'+P+'}nvPr/{'+P+'}ph') is not None):
                    continue
                bbox=None
                if shape is not None:
                    transform=shape.find('.//{'+A+'}xfrm')
                    if transform is None: transform=shape.find('{'+P+'}xfrm')
                    if transform is not None:
                        off=transform.find('{'+A+'}off'); ext=transform.find('{'+A+'}ext')
                        if off is not None and ext is not None:
                            x,y,cx,cy=[float(v)/12700 for v in (off.get('x','0'),off.get('y','0'),ext.get('cx','0'),ext.get('cy','0'))]
                            bbox=[x,y,x+cx,y+cy]
                unit['regions'].append({'id':name+':p'+str(index),'kind':'text','text':text,'bbox':bbox,
                    'source_ref':{'part':name,'paragraph_index':index,'text_nodes':len(nodes)},
                    'metadata':{'inside_table':cell is not None,'runs':len(nodes),
                        'run_texts':[n.text or '' for n in nodes],
                        'run_keys':[run_key(n,parents,paragraph) for n in nodes],
                        'run_breaks':node_breaks(paragraph,tag),
                        'run_styles':[ET.tostring(parents[n],encoding='unicode') for n in nodes]},'warnings':[]})
            paragraph_ids={id(p):name+':p'+str(i) for i,p in enumerate(paragraphs)}
            for index,table in enumerate(root.iter('{'+tag+'}tbl')):
                cells=[]; cell_ids=[]; spans=[]
                for row_number,row in enumerate(table.findall('{'+tag+'}tr')):
                    values=[]; ids=[]
                    for column,cell in enumerate(row.findall('{'+tag+'}tc')):
                        ids.append([paragraph_ids[id(p)] for p in cell.iter('{'+tag+'}p') if id(p) in paragraph_ids])
                        values.append('\n'.join(''.join(t.text or '' for t in p.iter('{'+tag+'}t')) for p in cell.iter('{'+tag+'}p')))
                        spans.append({'row':row_number,'column':column,'source_cell_xml':ET.tostring(cell,encoding='unicode')})
                    cells.append(values); cell_ids.append(ids)
                unit['regions'].append({'id':name+':table'+str(index),'kind':'table','text':'','bbox':None,'cells':cells,
                    'metadata':{'in_package_paragraphs':True,'cell_region_ids':cell_ids,'source_cells':spans},'warnings':[]})
                by_id={r['id']:r for r in unit['regions']}
                for row,ids in zip(cells,cell_ids):
                    for column,paragraph_ids_in_cell in enumerate(ids):
                        for pid in paragraph_ids_in_cell:
                            if pid in by_id: by_id[pid]['metadata']['row_context']={'column':column,'row_values':row}
            ir['units'].append(unit)
        unsupported=[n for n in archive.namelist() if '/embeddings/' in n or '/charts/' in n or 'vbaProject' in n]
        if unsupported: ir['warnings'].append('Embedded objects and chart data need separate content review; original parts are retained.')
        if ir['assets']: ir['warnings'].append('Image-contained text has not been OCR-processed in native Office mode.')
        ir['warnings'].append('Native Office text fitting, fields, grouping and complex styling require visual review.')
    return ir


def refresh_tables(ir):
    regions={r['id']:r for u in ir['units'] for r in u['regions']}
    for region in regions.values():
        ids=region.get('metadata',{}).get('cell_region_ids')
        if ids:
            region['cells']=[['\n'.join(regions[key].get('transformed_text',regions[key]['text']) for key in cell if key in regions)
                              for cell in row] for row in ids]


def write_package(data,kind,updates,redacted_values=()):
    """Apply ID-keyed paragraph changes without rebuilding the Office package."""
    tag=W if kind=='docx' else A
    out=BytesIO(); warnings=[]
    with open_package(data,kind) as original, zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for item in original.infolist():
            payload=original.read(item.filename)
            if item.filename.endswith(('.xml','.rels')):
                root=ET.fromstring(payload); changed=False
                for index,paragraph in enumerate(root.iter('{'+tag+'}p')):
                    key=item.filename+':p'+str(index)
                    if key not in updates: continue
                    nodes=paragraph_text_nodes(paragraph,tag)
                    if not nodes: continue
                    update=updates[key]
                    if isinstance(update,str) and len(nodes)>1 and redacted_values:
                        from transformation.validation.privacy import mask_runs
                        candidate=mask_runs([n.text or '' for n in nodes],redacted_values)
                        if ''.join(candidate)==update: update=candidate
                    if isinstance(update,list):
                        if len(update)!=len(nodes) or not all(isinstance(t,str) for t in update):
                            raise ValueError('Styled paragraph update does not match its run count: '+key)
                        for node,text in zip(nodes,update):
                            node.text=text
                            if kind=='docx': node.set('{'+XML+'}space','preserve')  # Word only: DrawingML a:t has no xml:space
                    elif len(nodes)==1 or update==''.join(n.text or '' for n in nodes):
                        if len(nodes)==1:
                            nodes[0].text=update
                            if kind=='docx': nodes[0].set('{'+XML+'}space','preserve')
                    else:
                        # Preserve emphasis boundaries for callers that have not
                        # supplied a validated styled translation. Never silently
                        # flatten a mixed-run paragraph.
                        raise ValueError('A changed mixed-run paragraph requires run-aligned text: '+key)
                    changed=True
                # Remove selected literals from auxiliary textual XML too (chart labels, alt text,
                # custom XML), word-bounded like the output check: "Mat" is not in "Format".
                if redacted_values:
                    from transformation.validation.output_validation import residual_in
                    from transformation.validation.privacy import mask
                    for node in root.iter():
                        for field in ('text','tail'):
                            value_text=getattr(node,field)
                            if value_text and any(residual_in(v,value_text) for v in redacted_values):
                                setattr(node,field,mask(value_text,redacted_values)); changed=True
                        for key,val in list(node.attrib.items()):
                            if not any(residual_in(v,val) for v in redacted_values): continue
                            if item.filename.endswith('.rels') and key=='Target' and node.get('TargetMode')=='External':
                                masked=val
                                for value in redacted_values: masked=masked.replace(value,'REDACTED')
                                node.set(key,masked); changed=True
                                warnings.append('A sensitive external link target was replaced: '+item.filename)
                            else:
                                node.set(key,mask(val,redacted_values)); changed=True
                                warnings.append('A removed value was also masked in an object description: '+item.filename)
                if changed: payload=serialize(root,payload)
            target.writestr(item,payload)
    result=out.getvalue()
    if redacted_values:
        from transformation.validation.output_validation import residual_in
        with open_package(result,kind) as verified:
            for part in verified.namelist():
                if part.endswith(('.xml','.rels')):
                    root=ET.fromstring(verified.read(part))
                    joined=''.join(root.itertext())+' '+' '.join(v for n in root.iter() for v in n.attrib.values())
                    if any(residual_in(v,joined) for v in redacted_values):
                        raise ValueError('A selected sensitive value remains in the Office package.')
    return result,list(dict.fromkeys(warnings))
