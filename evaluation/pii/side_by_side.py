"""Render original and redacted first pages side by side, and copy both PDFs, for chosen documents.

usage: python -m evaluation.pii.side_by_side <set_dir> <label> <out_dir> <doc> [doc ...]
"""
import io, json, shutil, sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def page_image(path, dpi=80):
    with pymupdf.open(path) as pdf:
        return Image.open(io.BytesIO(pdf[0].get_pixmap(dpi=dpi).tobytes('png'))).convert('RGB')


def main():
    set_dir, label, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    for name in sys.argv[4:]:
        summary = json.loads((set_dir / 'runs' / label / name / 'summary.json').read_text(encoding='utf-8'))
        if summary.get('status') != 'generated': print(name, 'not generated'); continue
        original, redacted = set_dir / 'pdfs' / f'{name}.pdf', Path(summary['output_file'])
        shutil.copy(original, out / f'{name}-original.pdf'); shutil.copy(redacted, out / f'{name}-redacted.pdf')
        a, b = page_image(original), page_image(redacted)
        gap, top = 24, 36
        sheet = Image.new('RGB', (a.width + b.width + gap, max(a.height, b.height) + top), 'white')
        sheet.paste(a, (0, top)); sheet.paste(b, (a.width + gap, top))
        draw = ImageDraw.Draw(sheet)
        draw.text((8, 10), 'Original', fill=(40, 40, 40)); draw.text((a.width + gap + 8, 10), 'Redacted', fill=(40, 40, 40))
        sheet.save(out / f'{name}-side-by-side.png')
        print(name, 'ok')


if __name__ == '__main__':
    main()
