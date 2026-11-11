"""Explicit user-selected PDF areas; independent of extraction, OCR and LLMs.

Coordinates are fractions of the displayed, rotated crop box. Every selection
is bound to the input bytes by SHA-256. Outputs are new files, never revisions
appended to an existing PDF. Passing these checks is not a whole-document PII
approval or proof that unknown sensitive information is absent.
"""
import hashlib
import math
from pathlib import Path

import pymupdf


def validate_regions(regions, pages=None):
    if not isinstance(regions, list) or not 1 <= len(regions) <= 500:
        raise ValueError('Select between 1 and 500 areas.')
    clean = []
    for r in regions:
        if not isinstance(r, dict) or set(r) != {'page', 'x', 'y', 'width', 'height'}:
            raise ValueError('Invalid area fields.')
        if type(r['page']) is not int or not 1 <= r['page'] <= (pages or 200):
            raise ValueError('Invalid page number.')
        for key in ('x', 'y', 'width', 'height'):
            if type(r[key]) not in (int, float) or not math.isfinite(r[key]):
                raise ValueError('Invalid area coordinates.')
        if (r['x'] < 0 or r['y'] < 0 or r['width'] <= 0 or r['height'] <= 0
                or r['x'] + r['width'] > 1.0000001
                or r['y'] + r['height'] > 1.0000001):
            raise ValueError('Area outside the page.')
        clean.append(dict(r))
    return clean


def remaining_glyphs(doc, rects):
    """Use glyph outlines rather than font line boxes to test actual overlap.

    Line boxes can extend into an adjacent line even when the glyph does not.
    Edge-touching glyphs, however, must still be removed as whole characters.
    """
    by_page = {}
    for index, rect in rects:
        by_page.setdefault(index, []).append(rect * doc[index].derotation_matrix)
    found = {}
    for index, selections in by_page.items():
        flags = pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_ACCURATE_BBOXES
        for block in doc[index].get_text('rawdict', flags=flags)['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    for char in span['chars']:
                        box = pymupdf.Rect(char['bbox'])
                        if any((box & selection).get_area() > 0.01 for selection in selections):
                            found.setdefault(index, []).append(box)
    return found


def redact_regions(source, destination, regions, source_sha256):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('A separate output file is required.')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != source_sha256:
        raise ValueError('The reviewed PDF version has changed.')
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        if doc.needs_pass or not 1 <= len(doc) <= 200:
            raise ValueError('Unsupported encrypted PDF or page count.')
        regions = validate_regions(regions, len(doc))
        # Preserve visible form values as page content, then remove interactive
        # fields. Comments/links are excluded from the exported copy to avoid
        # hidden annotation payloads retaining selected information.
        doc.bake(annots=False, widgets=True)
        for page in doc:
            for annot in list(page.annots() or []):
                page.delete_annot(annot)
            for link in page.get_links():
                page.delete_link(link)
        rects = []
        rotations = [page.rotation for page in doc]
        for r in regions:
            page = doc[r['page'] - 1]
            view = page.rect
            rect = pymupdf.Rect(r['x'] * view.width, r['y'] * view.height,
                                (r['x'] + r['width']) * view.width,
                                (r['y'] + r['height']) * view.height)
            rects.append((page.number, rect))
            unrotated = rect * page.derotation_matrix
            # Apply in an unrotated coordinate system, including cropped pages.
            page.set_rotation(0)
            page.add_redact_annot(unrotated, fill=(0, 0, 0), cross_out=False)
            page.set_rotation(rotations[page.number])
        for page in doc:
            if page.number not in {index for index, _ in rects}:
                continue
            page.set_rotation(0)
            # First remove selected image/vector content. Text is handled below
            # using actual glyph outlines so oversized font line boxes do not
            # delete neighboring lines that the user did not touch.
            if not page.apply_redactions(images=2, graphics=2, text=1):
                raise ValueError('Could not apply selected areas.')
            page.set_rotation(rotations[page.number])
        # MuPDF may retain a glyph when only a thin edge intersects a region.
        # Remove those complete glyphs explicitly, without touching neighboring
        # images/vectors or changing the user's black rectangle. Bounded passes
        # avoid accepting a document whose selected content cannot be removed.
        corrected_glyphs = 0
        for _ in range(2):
            pending = remaining_glyphs(doc, rects)
            if not pending:
                break
            for index, boxes in pending.items():
                page = doc[index];page.set_rotation(0)
                for box in boxes:
                    page.add_redact_annot(box, fill=False, cross_out=False)
                if not page.apply_redactions(images=0, graphics=0, text=0):
                    raise ValueError('Could not remove selected glyphs.')
                page.set_rotation(rotations[index])
                corrected_glyphs += len(boxes)
        # A subsequent content-removal pass can clip an earlier fill. Restore
        # only the user-selected fills after physical content removal completes.
        for index, rect in rects:
            page=doc[index];unrotated=rect*page.derotation_matrix
            page.set_rotation(0)
            page.draw_rect(unrotated,color=(0,0,0),fill=(0,0,0),width=0,overlay=True)
            page.set_rotation(rotations[index])
        doc.scrub(attached_files=True, clean_pages=True, embedded_files=True,
                  hidden_text=True, javascript=True, metadata=True,
                  redactions=False, remove_links=True, reset_fields=True,
                  reset_responses=True, thumbnails=True, xml_metadata=True)
        result = doc.tobytes(garbage=4, deflate=True, clean=True)
    with pymupdf.open(stream=result, filetype='pdf') as verified:
        pages = len(verified)
        if remaining_glyphs(verified, rects):
            raise ValueError('Selected text remains in the exported PDF.')
        for index, rect in rects:
            page = verified[index]
            # Verify the interior is opaque black in an independent rendering.
            interior = rect + (0.6, 0.6, -0.6, -0.6)
            if interior.is_empty:
                raise ValueError('Selection is too small to verify.')
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), clip=interior,
                                  colorspace=pymupdf.csRGB, alpha=False)
            if not pix.width or not pix.height or max(pix.samples) > 12:
                raise ValueError('Selected pixels were not fully removed.')
        if verified.embfile_count() or verified.xref_xml_metadata():
            raise ValueError('Ancillary payload cleanup failed.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    return {'regions': len(regions), 'pages': pages, 'glyphs_removed': corrected_glyphs,
            'source_sha256': source_sha256, 'output_sha256': hashlib.sha256(result).hexdigest(),
            'selected_text_check': 'passed', 'selected_pixels_check': 'passed',
            'review_status': 'requires_visual_review',
            'limitations': 'Checks cover selected areas, not unknown PII. Comments, links, attachments and metadata are removed; form values are flattened.'}


if __name__ == '__main__':
    import json
    import sys
    # A separate process keeps PDF parsing isolated while the agent continues
    # reporting job heartbeats. Paths come from the trusted agent, not the UI.
    source, destination, plan, digest, audit = sys.argv[1:]
    report = redact_regions(source, destination, json.loads(Path(plan).read_text(encoding='utf8')), digest)
    Path(audit).write_text(json.dumps(report, indent=2), encoding='utf8')
