"""Straighten slightly skewed scanned pages before OCR.

A page scanned a degree or two off level was read correctly, but the source text is erased and the
translation placed with level boxes: Chinese lines showed through the translation and neighbouring
paragraphs overlapped (Chinese newsletter scan, 2026-11-19). Pages that are only a picture of a page
are measured, and turned level when their text lines are clearly tilted; pages with a text layer are
never changed.
"""
import io

MIN_ANGLE = 0.25   # degrees; below this the boxes still fit
MAX_ANGLE = 5.0    # more is a photo or a deliberate design, not scanner skew
MEASURE_DPI = 100


def is_scan(page):
    """A page without text whose picture covers most of it."""
    if page.get_text('text').strip():
        return False
    area = abs(page.rect) or 1
    covered = 0.0
    for info in page.get_images(full=True):
        for rect in page.get_image_rects(info[0]):
            covered = max(covered, abs(rect & page.rect))
    return covered >= 0.8 * area


def _score(xs, ys, angle):
    import numpy as np
    projected = np.round(ys - xs * np.tan(np.radians(angle))).astype(np.int64)
    counts = np.bincount(projected - projected.min())
    return float((counts.astype(np.float64) ** 2).sum())


def skew_angle(gray):
    """Tilt of the text lines in a greyscale image (numpy, dark ink on light paper), in degrees,
    positive when lines fall to the right; 0.0 when there is no clear tilt."""
    import numpy as np
    # The top and bottom edges of ink, not the ink itself: large filled shapes on slides outweighed
    # the text lines and flattened the peak, while their edges are tilted lines too.
    ink = gray < 128
    edges = np.zeros_like(ink)
    edges[1:] = ink[1:] != ink[:-1]
    ys, xs = np.nonzero(edges)
    if len(xs) < 500:
        return 0.0
    if len(xs) > 60000:
        pick = np.random.default_rng(0).choice(len(xs), 60000, replace=False)
        xs, ys = xs[pick], ys[pick]
    xs, ys = xs.astype(np.float64), ys.astype(np.float64)
    coarse = np.arange(-MAX_ANGLE, MAX_ANGLE + 1e-9, 0.2)
    scores = [_score(xs, ys, a) for a in coarse]
    best = float(coarse[int(np.argmax(scores))])
    fine = np.arange(best - 0.2, best + 0.2 + 1e-9, 0.02)
    fine_scores = [_score(xs, ys, a) for a in fine]
    angle = float(fine[int(np.argmax(fine_scores))])
    # Text lines give a sharp peak; a photo or a blank page does not.
    if max(fine_scores) < 1.05 * _score(xs, ys, 0.0):
        return 0.0
    return round(angle, 2)


def _image_dpi(page):
    best = 0
    for info in page.get_images(full=True):
        for rect in page.get_image_rects(info[0]):
            if rect.width > 0:
                best = max(best, info[2] / (rect.width / 72))
    return int(min(400, max(150, best or 200)))


def _gray(page, dpi):
    import numpy as np
    import pymupdf
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def deskewed(data):
    """The PDF bytes with skewed scanned pages turned level, or data unchanged."""
    import pymupdf
    try:
        pdf = pymupdf.open(stream=data, filetype='pdf')
    except Exception:
        return data
    with pdf:
        if pdf.needs_pass:
            return data
        angles = {}
        for page in pdf:
            if is_scan(page):
                angle = skew_angle(_gray(page, MEASURE_DPI))
                if MIN_ANGLE <= abs(angle) <= MAX_ANGLE:
                    angles[page.number] = angle
        if not angles:
            return data
        from PIL import Image
        for number, angle in angles.items():
            page = pdf[number]
            dpi = _image_dpi(page)
            pix = page.get_pixmap(dpi=dpi, annots=False)
            mode = 'L' if pix.n == 1 else 'RGB'
            image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            white = 255 if mode == 'L' else (255, 255, 255)
            # Lines falling to the right are turned back counter-clockwise.
            level = image.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=white)
            buffer = io.BytesIO()
            level.save(buffer, format='JPEG', quality=92)
            rect = page.rect
            level_page = pdf.new_page(pno=number, width=rect.width, height=rect.height)
            level_page.insert_image(level_page.rect, stream=buffer.getvalue())
            pdf.delete_page(number + 1)
        return pdf.tobytes(garbage=3, deflate=True)
