"""Export the approved Product/Studio look into a real same-origin application."""
from pathlib import Path
import re
import json
import pymupdf

PROJECT=Path(__file__).resolve().parents[2]
static=PROJECT/'backend/static'
source=Path('C:/Users/Jubayer/.codex/visualizations/2026/09/12/01a096b0-4004-7692-9820-5a65e4ede0d0/transformx-blue-product-and-studio.html').read_text(encoding='utf8')
css=re.search(r'<style>(.*?)</style>',source,re.S).group(1)
css+='\nbody{margin:0;background:#f3f8fc;font-family:Segoe UI,Arial,sans-serif}#tx-v1{max-width:1280px;margin:0 auto;min-height:100vh;border-radius:0}#tx-v1 .tx-account{padding-right:0}#tx-v1 .tx-product-appnav{display:none}#tx-v1[data-site=product] .tx-product-appnav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}#tx-v1 .tx-linkbutton{display:inline-block;padding:10px 14px;border-radius:7px;text-decoration:none;color:var(--tx-accent);border:1px solid var(--tx-line)}#tx-v1 #notice:not(:empty){padding:12px;background:var(--tx-warnbg);border:1px solid var(--tx-line);border-radius:8px;margin-bottom:15px}#tx-v1 .tx-site-nav{margin-left:auto}#tx-v1 .tx-stage-list small{margin-left:auto}#tx-v1 #signin-error{color:#a53a35}#tx-v1 .tx-compare img{min-height:100px}#tx-v1 .tx-compare figcaption{padding:10px}#tx-v1 .tx-panel p{color:var(--tx-muted)}#tx-v1 dd{font-size:12px}#tx-v1 .tx-docname{overflow-wrap:anywhere}@media(max-width:480px){#tx-v1 .tx-product-art{max-width:100%}}'
(static/'app.css').write_text(css,encoding='utf8')
landing=(PROJECT/'outputs/mentor-update-20261018/build/blue-saas.html').read_text(encoding='utf8')
landing=landing.replace('id="tx-product-original"','id="tx-product-original" src="/assets/cover-original.jpg"').replace('id="tx-product-translated"','id="tx-product-translated" src="/assets/cover-translated.jpg"')
landing=landing.replace('Design preview · No live processing or authentication','V1 pilot · Invitation-only · Generated output requires review')
shell=(static/'app-shell.html').read_text(encoding='utf8').replace('<!-- LANDING -->',landing)
html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TransformX · Document transformation</title><link rel="stylesheet" href="/assets/app.css"><script src="/assets/app.js" defer></script></head><body><div id="tx-v1" data-site="landing">'+shell+'</div></body></html>'
for before,after in json.loads((PROJECT/'ui/product-copy.json').read_text(encoding='utf8')).items():
    html=html.replace(before,after)
(static/'index.html').write_text(html,encoding='utf8')
root=PROJECT/'runs/dataset-evaluation/slide-generalization-regression-20261018-v2/regression/pdfs'
for kind,name in [('original','original'),('after','translated')]:
    with pymupdf.open(root/kind/'Slide_Test_Samples.pdf') as d:
        pix=d[0].get_pixmap(matrix=pymupdf.Matrix(.8,.8),alpha=False)
        pix.save(static/f'cover-{name}.jpg')
print('Frontend built with local, external-script-only assets. No inline credentials or host APIs.')
