"""Optional local Office-to-PDF conversion through an isolated LibreOffice profile."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import json
import pymupdf


# Word 97-2003 (.doc) and PowerPoint 97-2003 (.ppt) files are converted to their current formats on
# arrival (modernize) and come back as .docx and .pptx.
LEGACY={'.doc':'docx','.ppt':'pptx'}
LEGACY_FILTERS={'docx':'docx:MS Word 2007 XML','pptx':'pptx:Impress MS PowerPoint 2007 XML'}


def modernize(data,filename,executable=None):
    """(bytes, file name) of a .doc or .ppt converted by LibreOffice to .docx or .pptx; other files
    unchanged. A file LibreOffice cannot convert (damaged, password-protected) raises ValueError."""
    suffix=Path(filename).suffix.lower()
    if suffix not in LEGACY:
        return data,filename
    if data[:4]!=b'\xd0\xcf\x11\xe0':
        raise ValueError('This file is not a Word 97-2003 or PowerPoint 97-2003 document.')
    target=LEGACY[suffix]
    executable=executable or os.environ.get('TRANSFORMX_LIBREOFFICE') or shutil.which('soffice')
    if not executable:
        raise ValueError('Converting .doc and .ppt files requires LibreOffice.')
    with tempfile.TemporaryDirectory(prefix='transformx-legacy-') as folder:
        root=Path(folder)
        source=root/('source'+suffix); source.write_bytes(data)
        command=[str(executable),'-env:UserInstallation='+(root/'profile').as_uri(),'--headless','--convert-to',
                 LEGACY_FILTERS[target],'--outdir',str(root/'out'),str(source)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=300,check=False)
        output=root/'out'/('source.'+target)
        if result.returncode or not output.exists():
            raise ValueError('This file could not be converted; it may be damaged or password-protected.')
        return output.read_bytes(),Path(filename).stem+'.'+target


def render(data,filename,executable=None,convert='pdf'):
    """(PDF bytes, report). convert is LibreOffice's --convert-to argument (a PDF export filter with
    options, e.g. speaker notes pages; see transformation/office/native.NOTES_FILTER)."""
    suffix=Path(filename).suffix.lower()
    if suffix=='.pdf':
        return data,{'renderer':'input PDF','overflow':[]}
    if suffix not in {'.docx','.pptx','.doc','.ppt'}:
        raise ValueError('Supported inputs are PDF, DOCX, PPTX, DOC and PPT.')
    if executable and not Path(executable).is_file():
        raise ValueError('Explicit renderer requires LibreOffice at a valid executable path.')
    executable=executable or os.environ.get('TRANSFORMX_LIBREOFFICE') or shutil.which('soffice')
    if not executable:
        for candidate in (Path('C:/Program Files/LibreOffice/program/soffice.exe'),Path('C:/Program Files (x86)/LibreOffice/program/soffice.exe')):
            if candidate.is_file():
                executable=str(candidate)
                break
    with tempfile.TemporaryDirectory(prefix='transformx-office-') as folder:
        root=Path(folder)
        source=root/('source'+suffix)
        source.write_bytes(data)
        output=root/'source.pdf'
        report={'renderer':'LibreOffice','overflow':[]}
        if executable and Path(executable).is_file():
            command=[str(executable),'-env:UserInstallation='+(root/'profile').as_uri(),'--headless','--convert-to',convert,'--outdir',str(root),str(source)]
        elif os.name=='nt':
            command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(Path(__file__).with_name('render_office.ps1')),'-Source',str(source),'-Destination',str(output),'-Report',str(root/'render.json')]
        else:
            raise ValueError('Office rendering requires Microsoft Office on Windows or LibreOffice.')
        result=subprocess.run(command,capture_output=True,text=True,timeout=300,check=False)
        if result.returncode or not output.exists():
            raise RuntimeError('Office rendering failed: '+result.stderr[-1000:])
        if (root/'render.json').exists(): report=json.loads((root/'render.json').read_text(encoding='utf-8-sig'))
        converted=output.read_bytes()
        with pymupdf.open(stream=converted,filetype='pdf') as doc:
            if not len(doc) or doc.needs_pass:
                raise ValueError('Office conversion produced an unusable PDF.')
        return converted,report


def to_pdf(data,filename,executable=None):
    return render(data,filename,executable)[0]


def fit_pptx(data,minimum=8):
    """Bounded fitting using actual Office glyph measurements, never silent loss."""
    from io import BytesIO
    from pptx import Presentation
    from pptx.util import Pt
    changes=[]
    def frames(shapes,prefix=''):
        for shape in shapes:
            path=prefix+str(shape.shape_id)
            if shape.has_text_frame: yield path,shape.text_frame
            if shape.has_table:
                for r,row in enumerate(shape.table.rows,1):
                    for c,cell in enumerate(row.cells,1): yield path+f'/cell-{r}-{c}',cell.text_frame
            if hasattr(shape,'shapes'): yield from frames(shape.shapes,path+'/')
    for attempt in range(3):
        preview,report=render(data,'fit.pptx')
        if not report['overflow']: return data,preview,dict(report,adjustments=changes)
        deck=Presentation(BytesIO(data)); changed=False
        ratios={}
        for row in report['overflow']:
            key=(row['slide'],row.get('path',str(row['shape']))); ratios[key]=min(ratios.get(key,1),row.get('ratio',1)*.97)
        for number,slide in enumerate(deck.slides,1):
            for path,frame in frames(slide.shapes):
                ratio=ratios.get((number,path))
                if not ratio: continue
                for paragraph in frame.paragraphs:
                    for run in paragraph.runs:
                        size=run.font.size or paragraph.font.size
                        if size is None: continue
                        new=max(minimum,size.pt*ratio)
                        if new<size.pt-.05:
                            changes.append({'slide':number,'shape_path':path,'before':size.pt,'after':round(new,2)})
                            run.font.size=Pt(new); changed=True
        if not changed: return data,preview,dict(report,adjustments=changes)
        output=BytesIO(); deck.save(output); data=output.getvalue()
    preview,report=render(data,'fit.pptx')
    return data,preview,dict(report,adjustments=changes)
