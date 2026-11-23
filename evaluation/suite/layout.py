"""Layout preservation of a translated or redacted PDF against its source, page by page.

    python -m evaluation.suite.layout RESULTS CORPUS OUT.json

RESULTS is a run_site results folder (<plan>/<target>/<doc>/output.pdf and summary.json); CORPUS
holds the source PDFs. Per page, the source and the output are rendered at 50 dpi in grey:

  ssim               structural similarity of the whole page (text differs in a translation, so this
                     is below 1 even for a perfect one; compare it between runs and languages)
  ssim_graphics      SSIM over the pixels outside every text line of either page: pictures, rules,
                     tables and backgrounds, which a translation should leave where they were
  picture_iou        each source picture against its best-matching output picture (1 = in place)
  block_iou          each source text block against its best-matching output text block, weighted by
                     area (where the text sits; reflowed translations move a little)
  text_extent_iou    the box around all text of the page, source against output
  font_ratio         median output line size / median source line size (below 1 = shrunk text)
  new_collisions     text lines overlapping each other that do not overlap in the source (page_audit)
  text_over_pictures pictures with text drawn across them that the source did not have (page_audit)
  picture_text_placements  pictures whose own text was translated and set inside them (intended)
  untranslated_lines lines still in the source language (page_audit)
  off_page           text spans outside the page
"""
import json
import statistics
import sys
from pathlib import Path

import numpy as np
import pymupdf

from evaluation.robustness import page_audit

DPI = 50


def gray(page, size=None):
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    if size and image.shape != size:
        import cv2
        image = cv2.resize(image, (size[1], size[0]), interpolation=cv2.INTER_AREA)
    return image


def text_mask(pages, shape):
    mask = np.zeros(shape, bool)
    for page in pages:
        sx, sy = shape[1] / page.rect.width, shape[0] / page.rect.height
        for line in page_audit.text_lines(page):
            r = line['bbox']
            mask[max(0, int(r.y0 * sy)):int(np.ceil(r.y1 * sy)), max(0, int(r.x0 * sx)):int(np.ceil(r.x1 * sx))] = True
    return mask


def iou(a, b):
    i = a & b
    if i.is_empty:
        return 0.0
    return i.get_area() / max(1e-6, a.get_area() + b.get_area() - i.get_area())


def best_iou(sources, outputs, weighted=False):
    if not sources:
        return None
    scores = [(max((iou(s, o) for o in outputs), default=0.0), s.get_area()) for s in sources]
    if weighted:
        total = sum(w for _, w in scores)
        return sum(v * w for v, w in scores) / total if total else None
    return sum(v for v, _ in scores) / len(scores)


def blocks(page):
    return [pymupdf.Rect(b[:4]) & page.rect for b in page.get_text('blocks') if b[6] == 0 and b[4].strip()]


def images(page):
    return [pymupdf.Rect(i['bbox']) & page.rect for i in page.get_image_info()
            if (pymupdf.Rect(i['bbox']) & page.rect).get_area() > 100]


def extent(lines):
    box = pymupdf.Rect(lines[0]['bbox'])
    for line in lines[1:]:
        box |= line['bbox']
    return box


def page_metrics(src, out, source, target):
    from skimage.metrics import structural_similarity
    a = gray(src)
    b = gray(out, a.shape)
    score, ssim_map = structural_similarity(a, b, data_range=255, full=True)
    mask = text_mask([src, out], a.shape)
    graphics = float(ssim_map[~mask].mean()) if (~mask).any() else None
    s_lines, o_lines = page_audit.text_lines(src), page_audit.text_lines(out)
    audit = page_audit.audit_page(src, out, source, target)
    issues = {i['kind']: i.get('count', 1) for i in audit['issues']}
    off = sum(1 for l in o_lines if not (out.rect + (-1, -1, 1, 1)).contains(l['bbox']))
    ratio = None
    if s_lines and o_lines:
        ratio = statistics.median(l['size'] for l in o_lines) / max(0.1, statistics.median(l['size'] for l in s_lines))
    return {
        'ssim': round(float(score), 4),
        'ssim_graphics': None if graphics is None else round(graphics, 4),
        'picture_iou': None if not images(src) else round(best_iou(images(src), images(out)), 4),
        'block_iou': None if not blocks(src) else round(best_iou(blocks(src), blocks(out), weighted=True), 4),
        'text_extent_iou': round(iou(extent(s_lines), extent(o_lines)), 4) if s_lines and o_lines else None,
        'font_ratio': None if ratio is None else round(ratio, 3),
        'new_collisions': issues.get('collisions', 0),
        'text_over_pictures': issues.get('over_pictures', 0),
        'picture_text_placements': len(audit.get('picture_text_placements', [])),
        'untranslated_lines': issues.get('untranslated', 0),
        'off_page': off,
    }


def document(source_pdf, output_pdf, source, target):
    with pymupdf.open(source_pdf) as src, pymupdf.open(output_pdf) as out:
        pages = [page_metrics(src[i], out[i], source, target) for i in range(min(len(src), len(out)))]
        row = {'pages': len(src), 'output_pages': len(out), 'page_count_match': len(src) == len(out)}
    for key in pages[0] if pages else []:
        values = [p[key] for p in pages if p[key] is not None]
        row[key] = (round(sum(values) / len(values), 4) if values else None) if key not in (
            'new_collisions', 'text_over_pictures', 'picture_text_placements', 'untranslated_lines', 'off_page') else sum(values)
    return row


def main(results, corpus, out):
    rows = []
    for summary in sorted(Path(results).rglob('summary.json')):
        s = json.loads(summary.read_text(encoding='utf-8'))
        output = summary.parent / 'output.pdf'
        source = Path(corpus) / ((s.get('doc') or s.get('file', '').removesuffix('.pdf')) + '.pdf')
        if not output.exists() or not source.exists():
            continue
        row = {'doc': source.stem, 'source': s.get('source'), 'target': s.get('target'), 'kind': s.get('kind'),
               'operation': s.get('operation', 'translation')}
        try:
            row.update(document(source, output, s.get('source'), s.get('target')))
        except Exception as exc:  # one document must not stop the others
            row['error'] = f'{type(exc).__name__}: {exc}'[:200]
        rows.append(row)
        print(row['doc'], row.get('target'), {k: row.get(k) for k in ('ssim', 'ssim_graphics', 'block_iou', 'picture_iou')}, flush=True)
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main(*sys.argv[1:4])
