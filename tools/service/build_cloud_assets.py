"""Copy the manual redaction editor and pdf.js into the published site (cloud/public).

The customer site itself is the static Next.js export (tools/service/package_customer_frontend.mjs).
Files left from the removed Studio page are deleted so a deploy cannot publish them again.
"""
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[2]
target = PROJECT / 'cloud/public'
target.mkdir(parents=True, exist_ok=True)
for stale in ('studio.html', 'studio.css', 'app.js', 'app.css', 'cover-original.jpg', 'cover-translated.jpg'):
    (target / stale).unlink(missing_ok=True)
for name in ('redaction-review.html', 'redaction-review.css', 'redaction-review.mjs'):
    shutil.copy2(PROJECT / 'cloud/ui' / name, target / name)
pdfjs = PROJECT / 'frontend/node_modules/pdfjs-dist'
(target / 'pdfjs').mkdir(exist_ok=True)
for name in ('pdf.mjs', 'pdf.worker.mjs'):
    shutil.copy2(pdfjs / 'build' / name, target / 'pdfjs' / name)
for name in ('cmaps', 'standard_fonts', 'wasm', 'iccs'):
    shutil.copytree(pdfjs / name, target / 'pdfjs' / name, dirs_exist_ok=True)
shutil.copy2(pdfjs / 'LICENSE', target / 'pdfjs/LICENSE')
print('Manual redaction editor and pdf.js copied.')
