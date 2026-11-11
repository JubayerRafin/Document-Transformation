"""Synthetic-only browser fixture. Never reads production configuration."""
import hashlib
import json
import sys
from pathlib import Path
import pymupdf
from transformation.validation.manual_redaction import redact_regions

root=Path(sys.argv[2]);root.mkdir(parents=True,exist_ok=True)
if sys.argv[1]=='create':
    doc=pymupdf.open()
    page=doc.new_page(width=400,height=520)
    page.insert_text((35,45),'Synthetic service agreement',fontsize=20)
    page.insert_text((35,95),'Name: already automatically redacted',fontsize=12)
    page.draw_rect(pymupdf.Rect(76,81,330,100),fill=(0,0,0),color=(0,0,0),overlay=True)
    page.insert_text((35,160),'Confidential project: DEMO AURORA',fontsize=13)
    page.insert_text((35,230),'KEEP THIS CONTENT',fontsize=14)
    page.draw_rect(pymupdf.Rect(35,290,350,400),fill=(.8,.9,.95),color=(.4,.6,.7))
    page.insert_text((55,345),'Internal diagram - synthetic',fontsize=12)
    page=doc.new_page(width=600,height=400)
    page.insert_text((75,115),'ROTATED CROP SECRET',fontsize=17)
    page.insert_text((75,210),'KEEP ROTATED CONTENT',fontsize=15)
    page.set_cropbox(pymupdf.Rect(30,40,560,370));page.set_rotation(90)
    raster=pymupdf.open();p=raster.new_page(width=400,height=520)
    p.insert_text((35,150),'SCANNED PRIVATE DETAIL',fontsize=18)
    p.insert_text((35,250),'UNSELECTED IMAGE CONTENT',fontsize=14)
    page=doc.new_page(width=400,height=520);page.insert_image(page.rect,stream=p.get_pixmap().tobytes('png'));raster.close()
    doc.save(root/'source.pdf');doc.close()
else:
    request=json.loads((root/'request.json').read_text())
    audit=redact_regions(root/'source.pdf',root/'result.pdf',request['regions'],request['source_sha256'])
    (root/'audit.json').write_text(json.dumps(audit))
