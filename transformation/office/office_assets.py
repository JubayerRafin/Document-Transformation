"""Process Office raster assets through the same Paddle/translation pipeline.

No image bytes are guessed from document text. Original relationships and asset
dimensions stay intact; rejected asset processing fails the enclosing run.
"""
from io import BytesIO
import zipfile
from pathlib import Path
from PIL import Image
import pymupdf


def process(data,filename,run,translate,redact,categories,assist,options):
    from transformation.pipeline import run_pipeline
    replacements={}; reports=[]
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for part in archive.namelist():
            if '/media/' not in part: continue
            raw=archive.read(part)
            try:
                image=Image.open(BytesIO(raw)); image.load(); image_format=image.format
            except OSError:
                reports.append({'part':part,'status':'blocked','reason':'Unsupported image format needs a renderer and OCR.'}); continue
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=image.width,height=image.height)
                source=BytesIO(); image.convert('RGB').save(source,format='PNG')
                page.insert_image(page.rect,stream=source.getvalue()); wrapped=pdf.tobytes()
            asset_options=dict(options)
            asset_options.pop('ocr_recognition_dir',None)
            for event in run_pipeline(wrapped,Path(part).stem+'.pdf',root=Path(run)/'image-runs',translate=translate,
                    redact=redact,pii_categories=categories,pii_assist=assist,table_strategy='native',**asset_options):
                yield {'part':part,'progress':event}
            if event['status']!='generated': raise RuntimeError('Office image processing failed: '+event.get('error','unknown'))
            with pymupdf.open(event['output_pdf']) as result:
                pix=result[0].get_pixmap(alpha=False)
                output_image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples).resize(image.size)
            target=BytesIO(); output_image.save(target,format=image_format)
            replacements[part]=target.getvalue()
            reports.append({'part':part,'status':'processed_requires_review','run_dir':event['run_dir']})
    output=BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as archive,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for item in archive.infolist(): target.writestr(item,replacements.get(item.filename,archive.read(item)))
    yield {'result':output.getvalue(),'reports':reports}
