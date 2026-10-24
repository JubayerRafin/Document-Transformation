"""Create a clean native text layer for BabelDOC from accepted Paddle evidence.

Raster glyphs are removed only where the existing reconstruction guards permit
it. Uncertain regions retain their source pixels and remain acceptance blockers.
"""
from pathlib import Path
import json
import pymupdf
from transformation.validation.privacy import mask


def prepare_native(data,ir,run,values=()):
    from transformation.pdf.scan_reconstruction import reconstruct
    blockers=[]
    with pymupdf.open(stream=data,filetype='pdf') as source,pymupdf.open() as merged:
        for unit in ir['units']:
            index=unit['number']-1
            if not unit.get('ocr_folder'):
                merged.insert_pdf(source,from_page=index,to_page=index); continue
            folder=Path(unit['ocr_folder'])/'native'; (folder/'output').mkdir(parents=True,exist_ok=True)
            with pymupdf.open() as single:
                single.insert_pdf(source,from_page=index,to_page=index)
                single.save(folder/'source.pdf')
            records=[]
            for region in unit['regions']:
                if region.get('text'): records.append(region)
            (folder/'ocr-document.json').write_text(json.dumps({'pages':[{'regions':records}]}),encoding='utf-8')
            mapping={r['id']:{'source_text':r['text'],'text':mask(r['text'],values)} for r in records}
            (folder/'region-translations.json').write_text(json.dumps(mapping),encoding='utf-8')
            output,report=reconstruct(folder,folder/'source.pdf',force_native=True)
            blockers.extend(dict(page=index+1,**b) for b in report['acceptance_blockers'])
            with pymupdf.open(output) as native:
                merged.insert_pdf(native)
        payload=merged.tobytes(garbage=4,deflate=True)
    Path(run,'scan-nativeization.json').write_text(json.dumps({'acceptance_blockers':blockers,'method':'guarded_raster_cleanup_then_native_text'},indent=2),encoding='utf-8')
    return payload,blockers
