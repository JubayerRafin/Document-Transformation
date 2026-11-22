"""Version-pinned BabelDOC integration hooks; no vendor files are modified."""
import functools
import json
import os
import sys
from pathlib import Path
import runpy
from types import SimpleNamespace

def constrain_caption_boxes(document, source_images=None):
    """Constrain slight overlaps above and below images.

    Coordinates use a bottom-left origin: larger y means higher on the page.
    Leave image-interior text and substantial overlaps for human review.
    """
    changes = []
    gap = 2.0
    minimum_height = 6.0

    def horizontally_overlaps(a, b):
        return min(a.x2, b.x2) > max(a.x, b.x)

    for page in document.page:
        figures = list(page.pdf_figure) + (
            source_images or {}
        ).get(page.page_number, [])

        for paragraph in page.pdf_paragraph:
            box = paragraph.box

            # Titles can now be constrained; tables remain excluded.
            if not box or paragraph.layout_label == "table":
                continue

            for figure in figures:
                image = figure.box
                height = box.y2 - box.y

                if (
                    not image
                    or height <= 0
                    or not horizontally_overlaps(box, image)
                ):
                    continue

                overlap = min(box.y2, image.y2) - max(box.y, image.y)

                if not (0 < overlap <= height * 0.4):
                    continue

                before = [box.x, box.y, box.x2, box.y2]

                if box.y < image.y2 < box.y2:
                    # Move the whole heading upward, preserving its height.
                    # Extra clearance allows for glyphs extending beyond
                    # BabelDOC's paragraph geometry.
                    heading_gap = 4.0
                    shift = image.y2 + heading_gap - box.y

                    bottom = box.y + shift
                    top = box.y2 + shift

                    crop = getattr(page, "cropbox", None)
                    page_box = getattr(crop, "box", None)

                    if page_box is None:
                        continue  # Cannot verify the page boundary.

                    upper_limit = page_box.y2 - gap

                    for other in page.pdf_paragraph:
                        ob = other.box
                        if (
                            other is not paragraph
                            and ob
                            and ob.y >= box.y2
                            and horizontally_overlaps(ob, box)
                        ):
                            upper_limit = min(upper_limit, ob.y - gap)

                    if top > upper_limit:
                        continue  # Insufficient room above the heading.

                    reason = "text_above_image_shift"

                elif box.y < image.y < box.y2:
                    # Text is mostly BELOW the image.
                    # Retain the existing caption adjustment.
                    top = image.y - gap
                    lower_limit = 0.0

                    for other in page.pdf_paragraph:
                        ob = other.box
                        if (
                            other is not paragraph
                            and ob
                            and ob.y2 <= box.y
                            and horizontally_overlaps(ob, box)
                        ):
                            lower_limit = max(lower_limit, ob.y2 + gap)

                    bottom = max(lower_limit, top - height)
                    reason = "text_below_image"

                else:
                    continue

                if top - bottom < minimum_height:
                    continue  # Too little space; leave for human review.

                changes.append({
                    "page": page.page_number,
                    "paragraph_id": paragraph.debug_id,
                    "reason": reason,
                    "before": before,
                    "after": [box.x, bottom, box.x2, top],
                })

                box.y = bottom
                box.y2 = top

    return changes


def clear_side_images(document, source_images=None, skip=()):
    """Narrow a paragraph's box so it no longer reaches under an image beside its text.

    In the source, text can wrap around a picture: a caption's first line starts right of a photo
    and the next lines run under it. BabelDOC merges the lines into one box from the left margin,
    and the translation reflowed from that edge put its first words under the photo, hidden or
    overprinted, in all ten languages (2026-11-17, an ID-photo caption). When an image sits at the
    left or right end of a paragraph box, overlaps it vertically, and covers less than 60% of its
    width, the box is narrowed to start (or end) just past the image; the text is fitted into it.
    Paragraphs adjusted by constrain_caption_boxes (skip) and table text are left alone."""
    changes = []
    gap = 2.0
    for page in document.page:
        images = [f.box for f in list(page.pdf_figure) + (source_images or {}).get(page.page_number, []) if f.box]
        for paragraph in page.pdf_paragraph:
            box = paragraph.box
            if not box or paragraph.layout_label == 'table' or paragraph.debug_id in skip: continue
            width = box.x2 - box.x
            if width <= 0: continue
            for image in images:
                vertical = min(box.y2, image.y2) - max(box.y, image.y)
                horizontal = min(box.x2, image.x2) - max(box.x, image.x)
                if vertical <= 0 or horizontal <= 0 or horizontal > 0.6 * width: continue
                before = [box.x, box.y, box.x2, box.y2]
                if image.x <= box.x + gap * 3 and image.x2 < box.x2:      # image at the left end
                    box.x = image.x2 + gap; side = 'left'
                elif image.x2 >= box.x2 - gap * 3 and image.x > box.x:    # image at the right end
                    box.x2 = image.x - gap; side = 'right'
                else:
                    continue  # an image in the middle of a text block: leave for review
                changes.append({'page': page.page_number, 'paragraph_id': paragraph.debug_id,
                                'reason': f'text_beside_image_{side}', 'before': before,
                                'after': [box.x, box.y, box.x2, box.y2]})
                width = box.x2 - box.x
    return changes


def source_image_boxes(path):
    """Read actual PDF image placements; native IL does not always list figures."""
    import pymupdf
    result = {}
    with pymupdf.open(path) as pdf:
        for i, page in enumerate(pdf):
            if page.rotation or page.cropbox != page.mediabox or page.mediabox.x0 or page.mediabox.y0:
                continue  # No speculative geometry conversion for transformed pages.
            result[i] = []
            for image in page.get_image_info():
                left, top, right, bottom = image["bbox"]
                result[i].append(SimpleNamespace(box=SimpleNamespace(x=left, y=page.rect.height-bottom, x2=right, y2=page.rect.height-top)))
    return result


def source_table_boxes(path):
    """Read table boundaries and convert to bottom-left coordinates."""
    import pymupdf

    result = {}

    with pymupdf.open(path) as pdf:
        for page_number, page in enumerate(pdf):
            # Use the same coordinate restrictions as source_image_boxes.
            if (
                page.rotation
                or page.cropbox != page.mediabox
                or page.mediabox.x0
                or page.mediabox.y0
            ):
                continue

            result[page_number] = []

            for table in page.find_tables().tables:
                left, top, right, bottom = table.bbox

                result[page_number].append(
                    SimpleNamespace(
                        box=SimpleNamespace(
                            x=left,
                            y=page.rect.height - bottom,
                            x2=right,
                            y2=page.rect.height - top,
                        )
                    )
                )

    return result
def constrain_table_headings(document, source_tables):
    """Move text immediately above tables upward when space permits."""
    changes = []
    gap = 4.0

    def overlaps_x(a, b):
        return min(a.x2, b.x2) > max(a.x, b.x)

    for page in document.page:
        crop = getattr(page, "cropbox", None)
        page_box = getattr(crop, "box", None)
        if page_box is None:
            continue

        for table in source_tables.get(page.page_number, []):
            boundary = table.box

            for paragraph in page.pdf_paragraph:
                box = paragraph.box
                if not box or paragraph.layout_label == "table":
                    continue

                height = box.y2 - box.y
                if height <= 0 or not overlaps_x(box, boundary):
                    continue

                # Only consider text mostly above the table.
                if box.y2 <= boundary.y2:
                    continue

                overlap = max(0.0, boundary.y2 - box.y)
                if overlap > height * 0.4:
                    continue

                shift = boundary.y2 + gap - box.y
                if shift <= 0:
                    continue  # Already has sufficient clearance.

                bottom = box.y + shift
                top = box.y2 + shift

                if top > page_box.y2 - 2:
                    continue

                blocked = False
                for other in page.pdf_paragraph:
                    ob = other.box
                    if other is paragraph or not ob:
                        continue

                    if (
                        overlaps_x(box, ob)
                        and bottom < ob.y2 + 2
                        and top > ob.y - 2
                    ):
                        blocked = True
                        break

                if blocked:
                    continue

                changes.append({
                    "page": page.page_number,
                    "paragraph_id": paragraph.debug_id,
                    "reason": "text_above_table_shift",
                    "before": [box.x, box.y, box.x2, box.y2],
                    "after": [box.x, bottom, box.x2, top],
                })

                box.y = bottom
                box.y2 = top

    return changes
def cells_with_undrawn_borders(cells, words, ink, scale):
    """Indexes of found table cells with a border that runs through a word and is not drawn there:
    such a border is a line of a picture's drawing, clipped away outside the picture, or an edge
    PyMuPDF extended across text when it merged two tables; that cell is not a cell.

    Two printer drawings of an HP manual page made a "table" whose cells reached over the text above
    them; a list step was moved to the top of a cell in the picture area and drawn over the heading
    (2026-11-23). Borders drawn through words (a newsletter's date column spilling over its table)
    are kept. `ink` is a 2-D boolean array of the rendered page (True where something is drawn),
    `scale` its pixels per point; cells are (x0, top, x1, bottom) and words as from
    get_text("words")."""
    height, width = ink.shape

    def drawn(edge):
        kind, at, start, stop = edge
        a, b = max(0, int(start * scale)), int(stop * scale)
        c = int(round(at * scale))
        lo, hi = max(0, c - 1), c + 2
        if kind == 'v':
            strip = ink[a:min(height, b), lo:min(width, hi)].any(axis=1) if lo < width else None
        else:
            strip = ink[lo:min(height, hi), a:min(width, b)].any(axis=0) if lo < height else None
        return strip is not None and strip.size and strip.mean() >= 0.8

    seen, out = {}, set()
    for number, cell in enumerate(cells):
        if cell is None:
            continue
        x0, top, x1, bottom = cell
        for word in words:
            wx0, wy0, wx1, wy1 = word[:4]
            middle, centre = (wy0 + wy1) / 2, (wx0 + wx1) / 2
            edges = [('v', x, top, bottom) for x in (x0, x1) if wx0 + 1 < x < wx1 - 1 and top < middle < bottom]
            edges += [('h', y, x0, x1) for y in (top, bottom) if wy0 + 1 < y < wy1 - 1 and x0 < centre < x1]
            for edge in edges:
                if edge not in seen:
                    seen[edge] = drawn(edge)
            if any(not seen[edge] for edge in edges):
                out.add(number)
                break
    return out


@functools.lru_cache(maxsize=4)
def source_table_cells(path):
    """Read strict table-cell boundaries in bottom-left coordinates (once per file: paragraph
    finding and typesetting both use them). Callers must not change the result."""
    import pymupdf

    result = {}

    with pymupdf.open(path) as pdf:
        for page_number, page in enumerate(pdf):
            if (
                page.rotation
                or page.cropbox != page.mediabox
                or page.mediabox.x0
                or page.mediabox.y0
            ):
                continue

            cells = []
            tables = page.find_tables(strategy="lines_strict")
            words = ink = None

            for table_number, table in enumerate(tables.tables):
                if words is None:
                    words = page.get_text("words")
                    pix = page.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY, alpha=False)
                    import numpy as np
                    ink = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width) < 245
                undrawn = {tuple(table.cells[i]) for i in cells_with_undrawn_borders(table.cells, words, ink, 100 / 72)}
                seen = set(undrawn)

                for row_number, row in enumerate(table.rows):
                    for column_number, boundary in enumerate(row.cells):
                        if boundary is None:
                            continue

                        boundary = tuple(boundary)
                        if boundary in seen:
                            continue
                        seen.add(boundary)

                        left, top, right, bottom = boundary

                        cells.append(SimpleNamespace(
                            table=table_number,
                            row=row_number,
                            column=column_number,
                            box=SimpleNamespace(
                                x=left,
                                y=page.rect.height - bottom,
                                x2=right,
                                y2=page.rect.height - top,
                            ),
                        ))

            result[page_number] = cells

    return result
def inspect_table_cell_text(document, cells_by_page):
    """Record text blocks overlapping each cell without changing layout."""
    records = []

    for page in document.page:
        for cell in cells_by_page.get(page.page_number, []):
            cb = cell.box
            blocks = []

            for paragraph in page.pdf_paragraph:
                pb = paragraph.box
                if pb is None:
                    continue

                width = max(0, min(pb.x2, cb.x2) - max(pb.x, cb.x))
                height = max(0, min(pb.y2, cb.y2) - max(pb.y, cb.y))
                area = (pb.x2 - pb.x) * (pb.y2 - pb.y)

                if width * height <= 0 or area <= 0:
                    continue

                blocks.append({
                    "paragraph_id": paragraph.debug_id,
                    "text": paragraph.unicode,
                    "bbox": [pb.x, pb.y, pb.x2, pb.y2],
                    "fraction_inside_cell": round(width * height / area, 3),
                })

            records.append({
                "page": page.page_number + 1,
                "table": cell.table + 1,
                "row": cell.row + 1,
                "column": cell.column + 1,
                "cell_bbox": [cb.x, cb.y, cb.x2, cb.y2],
                "overlapping_blocks": blocks,
            })

    return records
def resolve_cell_claims(candidates):
    """One slot for a paragraph that several planned cells claim: [(cell_bbox, slot), ...].
    A single claim stands. When the cells are nested (a merged cell around a smaller one, as the
    table detector reports around masking boxes), the innermost cell wins. Otherwise None."""
    if len(candidates) == 1:
        return candidates[0][1]
    def contains(outer, inner, tolerance=1.0):
        return (outer[0] - tolerance <= inner[0] and outer[1] - tolerance <= inner[1]
                and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)
    area = lambda box: (box[2] - box[0]) * (box[3] - box[1])
    ordered = sorted(candidates, key=lambda candidate: area(candidate[0]))
    innermost = ordered[0][0]
    if all(contains(cell, innermost) for cell, _ in ordered[1:]):
        return ordered[0][1]
    return None


def plan_table_text_slots(cell_report, padding=1.0, gap=1.0, images=None):
    """Plan bounded text areas without modifying the document.

    Supports one block or vertically separated blocks per cell.
    More complex arrangements are flagged for review.

    images: {page index: [objects with .box (bottom-left coordinates)]}. In a cell holding a picture
    (a warning triangle between "CAUTION:" and its text), each block's slot starts where the block
    starts and ends above the next block or picture below it. Equal shares of the cell put the text
    below the triangle over the triangle (HP manual tables, 2026-11-23).
    """
    plans = []

    for cell in cell_report:
        left, bottom, right, top = cell["cell_bbox"]

        # Ignore small overlaps from neighboring rows.
        blocks = [
            block for block in cell["overlapping_blocks"]
            if block["fraction_inside_cell"] >= 0.8
        ]
        blocks.sort(
            key=lambda block: block["bbox"][3],
            reverse=True,
        )

        plan = {
            "page": cell["page"],
            "table": cell["table"],
            "row": cell["row"],
            "column": cell["column"],
            "cell_bbox": cell["cell_bbox"],
            "status": "planned",
            "slots": [],
        }
        plans.append(plan)

        if not blocks:
            plan["status"] = "requires_review"
            plan["reason"] = "No confidently assigned text block."
            continue

        # Do not stack blocks that originally sit beside or overlap each other.
        if any(
            upper["bbox"][1] < lower["bbox"][3]
            for upper, lower in zip(blocks, blocks[1:])
        ):
            plan["status"] = "requires_review"
            plan["reason"] = "Blocks are not vertically separated."
            continue

        usable_height = (
            top - bottom - 2 * padding - gap * (len(blocks) - 1)
        )
        slot_height = usable_height / len(blocks)

        # Geometry check only; font readability is checked during fitting.
        if slot_height < 6 or right - left <= 2 * padding:
            plan["status"] = "requires_review"
            plan["reason"] = "Insufficient space for separate text slots."
            continue

        pictures = [im.box for im in (images or {}).get(cell["page"] - 1, [])
                    if im.box.x < right and im.box.x2 > left and im.box.y < top and im.box.y2 > bottom
                    and (im.box.x2 - im.box.x) < (right - left) * 0.95]
        for index, block in enumerate(blocks):
            slot_top = top - padding - index * (slot_height + gap)
            slot_bottom = slot_top - slot_height
            if pictures:
                block_top, block_bottom = block["bbox"][3], block["bbox"][1]
                slot_top = top - padding if index == 0 else min(top - padding, block_top + padding)
                slot_bottom = (blocks[index + 1]["bbox"][3] + gap) if index + 1 < len(blocks) else bottom + padding
                for picture in pictures:
                    if block_bottom + 1 >= picture.y2 > slot_bottom:
                        slot_bottom = picture.y2 + gap
                if slot_top - slot_bottom < 6:
                    slot_bottom = slot_top - 6

            plan["slots"].append({
                "paragraph_id": block["paragraph_id"],
                "single_block_cell_bbox": cell["cell_bbox"] if len(blocks)==1 else None,
                "text": block["text"],
                "bbox": [
                    left + padding,
                    slot_bottom,
                    right - padding,
                    slot_top,
                ],
            })

    return plans
SHAPED_PARAGRAPHS = []


def draw_shaped_paragraphs():
    """Draw the paragraphs taken out before typesetting into the written single-language PDF."""
    from transformation.llm.ollama_native import record_progress
    from transformation.pdf.complex_script import draw
    output = Path(sys.argv[sys.argv.index('--output') + 1])
    written = sorted(output.glob('*.mono.pdf'))
    if len(written) != 1:
        raise RuntimeError(f'Expected one translated PDF to draw shaped text into, found {len(written)}.')
    # BabelDOC's own Noto font (already downloaded for these languages) for the invisible text layer.
    text_font = None
    try:
        from babeldoc.const import get_cache_file_path
        candidate = get_cache_file_path('GoNotoKurrent-Regular.ttf', 'fonts')
        text_font = candidate if candidate.exists() else None
    except Exception:
        pass
    results = draw(written[0], SHAPED_PARAGRAPHS, text_font)
    folder = os.environ.get('TRANSFORMX_RUN_DIR')
    if folder:
        Path(folder, 'complex-script-drawing.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    record_progress('complex_script_paragraphs', drawn=sum(r['status'] == 'drawn' for r in results), total=len(results))
    if any(r['status'] != 'drawn' for r in results):
        raise RuntimeError('Some translated paragraphs could not be drawn; see complex-script-drawing.json.')


def main():
    from transformation.llm.ollama_native import record_progress
    record_progress('worker_hooks_starting')
    from transformation.pdf.layout_cpu import install as install_layout_cpu
    record_progress('worker_layout_cpu_policy', idle_spinning_disabled=install_layout_cpu())
    from transformation.pdf.layout_device import install as install_layout_device
    record_progress('worker_layout_device', providers=install_layout_device())
    from transformation.pdf.babeldoc_speed import install as install_babeldoc_speed
    record_progress('worker_babeldoc_setup_reuse', installed=install_babeldoc_speed())
    from transformation.pdf.thin_glyphs import install as install_thin_glyphs
    record_progress('worker_thin_glyph_lines', installed=install_thin_glyphs())
    from transformation.pdf.layout_duplicates import install as install_layout_duplicates
    record_progress('worker_layout_duplicates', installed=install_layout_duplicates())
    from transformation.pdf.font_flags import install as install_font_flags, learn as learn_font_styles
    record_progress('worker_font_flags', installed=install_font_flags())
    if '--files' in sys.argv:
        # Serif or sans-serif from the source's own fonts (font_shapes.py), for choosing output fonts.
        record_progress('worker_font_styles', fonts=learn_font_styles(sys.argv[sys.argv.index('--files') + 1]))
    from transformation.pdf.symbol_fonts import install as install_symbol_fonts
    record_progress('worker_symbol_fonts', installed=install_symbol_fonts())
    from transformation.pdf.layout_service import install as install_layout_service
    install_layout_service()
    record_progress('worker_layout_service_ready', shared=bool(os.environ.get('TRANSFORMX_LAYOUT_SERVICE')))
    from transformation.pdf.stage_timing import install as install_stage_timing
    install_stage_timing()
    from transformation.pdf.stage_timing import install_rate_timing
    install_rate_timing()
    if os.environ.get('TRANSFORMX_LIGHT_PDF_IO')=='1':
        from transformation.pdf.io_worker import install as install_light_pdf_io
        install_light_pdf_io()
    protected_values = []
    from importlib.metadata import version
    if version("babeldoc") != "0.6.4":
        raise RuntimeError("Integration hooks require BabelDOC 0.6.4; validate before upgrading.")
    if os.environ.get("TRANSFORMX_OLLAMA_NATIVE_ENDPOINT"):
        from transformation.llm.ollama_native import install
        protection_file = os.environ.get("TRANSFORMX_PROTECTED_VALUES_FILE")
        context_file = os.environ.get("TRANSFORMX_DOCUMENT_CONTEXT_FILE")
        document_context = (
            json.loads(Path(context_file).read_text(encoding="utf-8"))
            if context_file else ""
        )
        protected_values = (
            json.loads(Path(protection_file).read_text(encoding="utf-8"))
            if protection_file else []
        )
        install(
            os.environ["TRANSFORMX_OLLAMA_NATIVE_ENDPOINT"],
            paragraph_mode=os.environ.get("TRANSFORMX_PARAGRAPH_MODE") == "1",
            protected_values=protected_values,
            document_context=document_context,
            external_context=json.loads(Path(os.environ['TRANSFORMX_EXTERNAL_CONTEXT_FILE']).read_text(encoding='utf-8')) if os.environ.get('TRANSFORMX_EXTERNAL_CONTEXT_FILE') else None,
            terminology=json.loads(Path(os.environ['TRANSFORMX_TERMINOLOGY_FILE']).read_text(encoding='utf-8')) if os.environ.get('TRANSFORMX_TERMINOLOGY_FILE') else None,
            # Possibly still being written by the pipeline: loaded on the first paragraph.
            glossary=__import__('transformation.llm.glossary',fromlist=['pending']).pending(os.environ['TRANSFORMX_GLOSSARY_FILE']) if os.environ.get('TRANSFORMX_GLOSSARY_FILE') else None,
        )
        if os.environ.get("TRANSFORMX_PARAGRAPH_MODE") == "1":
            from transformation.pdf.source_repairs import install_source_repairs
            install_source_repairs(protected_values)
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    workers=int(os.environ.get('TRANSFORMX_TRANSLATION_WORKERS','1'))
    if workers>1:
        from babeldoc.translator.translator import OpenAITranslator
        from transformation.pdf.translation_concurrency import install as install_parallel_isolation
        install_parallel_isolation(ILTranslator,OpenAITranslator)
    record_progress('translation_concurrency_policy',workers=workers,isolated_commits=workers>1)
    from dataclasses import asdict
    original_translate=ILTranslator.translate
    locked_slots={}

    def tracked_translate(self,document):
        folder=os.environ.get('TRANSFORMX_RUN_DIR')
        external=os.environ.get('TRANSFORMX_EXTERNAL_IR_FILE')
        if external and folder:
            from transformation.pdf.ir_bridge import map_document,lock_ocr_regions
            external_ir=json.loads(Path(external).read_text(encoding='utf-8'))
            locks=[]
            try:
                locked,slots,locks=lock_ocr_regions(document,external_ir,evidence=locks)
            finally:
                Path(folder,'locked-regions.json').write_text(json.dumps(locks,ensure_ascii=False,indent=2),encoding='utf-8')
            self._transformx_locked_inputs=locked; locked_slots.update(slots)
            mapping=map_document(document,external_ir)
            Path(folder,'ir-mapping.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding='utf-8')
        if folder:
            from transformation.pdf.ir_json import dumps as ir_dumps
            Path(folder,'babeldoc-source-ir.json').write_text(ir_dumps(document),encoding='utf-8')
        from transformation.layout.source_atoms import capture_source_atoms
        source_atoms.clear();source_atoms.update(capture_source_atoms(document))
        from transformation.layout.line_spacing import measure as measure_line_spacing
        measure_line_spacing(document)
        result=original_translate(self,document)
        if folder:
            from transformation.pdf.ir_json import dumps as ir_dumps
            Path(folder,'babeldoc-transformed-ir.json').write_text(ir_dumps(document),encoding='utf-8')
        return result

    source_atoms={}
    ILTranslator.translate=tracked_translate
    from transformation.layout.table_fitting import install_table_fitting
    from transformation.layout.line_breaks import install_date_line_breaks, widen_page_footers, join_split_dates, heading_slots
    install_date_line_breaks(Typesetting, protected_values)
    from transformation.layout.line_spacing import install as install_source_line_spacing
    install_source_line_spacing(Typesetting)
    from transformation.layout.page_scale import install as install_page_scale
    install_page_scale(Typesetting)
    from transformation.layout.line_breaks import install_whole_word_breaks, install_attached_punctuation
    install_whole_word_breaks(Typesetting)
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    install_attached_punctuation(TypesettingUnit)
    from transformation.layout.line_breaks import install_korean_word_breaks, install_real_first_line_indents
    install_korean_word_breaks(TypesettingUnit)
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
    install_real_first_line_indents(ParagraphFinder)
    from transformation.layout.stacked_lines import install as install_stacked_lines
    install_stacked_lines(ParagraphFinder)
    install_table_fitting(Typesetting)
    original = Typesetting.typesetting_document

    def guarded_typesetting(self, document):
        self._transformx_source_atoms=source_atoms
        input_file = self.translation_config.input_file
        folder = os.environ.get("TRANSFORMX_RUN_DIR")
        # These refer to the same immutable input PDF throughout this hook.
        # Reuse geometry for before/after audits rather than detecting its
        # tables three times and enumerating its images twice.
        source_cells = source_table_cells(str(input_file))
        source_images = source_image_boxes(input_file)
        source_tables = source_table_boxes(input_file)
        if folder:
            cell_report = inspect_table_cell_text(
                document, source_cells
            )
            Path(folder, "table-cell-text.json").write_text(
                json.dumps(cell_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        changes = constrain_caption_boxes(
            document, source_images
        )
        changes.extend(clear_side_images(document, source_images, {c['paragraph_id'] for c in changes}))
        changes.extend(widen_page_footers(document))
        changes.extend(join_split_dates(document))
        changes.extend(
            constrain_table_headings(
                document, source_tables
            )
        )
        from transformation.layout.line_breaks import widen_single_lines
        changes.extend(widen_single_lines(document, source_images, {c.get('paragraph_id') for c in changes}))
        folder = os.environ.get("TRANSFORMX_RUN_DIR")
        if folder:
            Path(folder, "layout-adjustments.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
        plans = plan_table_text_slots(
            inspect_table_cell_text(
                document, source_cells
            ),
            images=source_images,
        )

        self._transformx_table_slots = {}
        self._transformx_table_fit_results = []
        # Keep the established safe heading boxes fixed during rendering.
        # Native typesetting may otherwise expand them downward into a figure.
        self._transformx_heading_slots = heading_slots(document, changes, source_images)
        self._transformx_heading_fit_results = []
        from transformation.layout.panels import PANEL_SLOTS
        self._transformx_panel_slots = dict(PANEL_SLOTS)
        self._transformx_panel_fit_results = []

        claims = {}
        for plan in plans:
            if plan["status"] != "planned":
                continue
            for slot in plan["slots"]:
                claims.setdefault((plan["page"], slot["paragraph_id"]), []).append((plan["cell_bbox"], slot))
        for key, candidates in claims.items():
            slot = resolve_cell_claims(candidates)
            if slot is None:
                # Overlapping, non-nested cells (table lines detected twice, e.g. around masked
                # values): leave this paragraph to BabelDOC's own layout rather than guess.
                changes.append({"type": "ambiguous_table_cell_left_to_native_layout", "page": key[0], "paragraph_id": key[1]})
                continue
            self._transformx_table_slots[key] = slot
        if folder:
            Path(folder, "layout-adjustments.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
        self._transformx_table_slots.update(locked_slots)

        # Text colours BabelDOC's parser lost (white table headers on a dark fill came out black).
        from transformation.pdf.source_colours import restore as restore_source_colours
        try:
            for page_number, paragraph_id, colour in restore_source_colours(document, input_file):
                changes.append({"type": "source_text_colour_restored", "page": page_number,
                                "paragraph_id": paragraph_id, "colour": colour})
        except Exception as error:  # a colour is never worth failing the document for
            changes.append({"type": "source_text_colour_check_failed", "error": str(error)[:200]})
        if folder:
            Path(folder, "layout-adjustments.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")

        # Arabic/Hebrew/Indic paragraphs are drawn after the PDF is written, with shaping
        # (transformation/pdf/complex_script.py); only for single-language output.
        if '--no-dual' in sys.argv:
            from transformation.pdf.complex_script import take, save_records
            slots = {key: slot['bbox'] for source in (self._transformx_panel_slots, self._transformx_heading_slots,
                                                      self._transformx_table_slots) for key, slot in source.items()}
            taken = take(document, slots)
            SHAPED_PARAGRAPHS.extend(taken)
            # Fitting hooks skip these: they have no glyphs for BabelDOC and are drawn after the PDF is written.
            self._transformx_shaped_keys = {(r['page'] + 1, r['paragraph_id']) for r in taken}
            if folder and SHAPED_PARAGRAPHS: save_records(folder, SHAPED_PARAGRAPHS)

            def fallback_draw(page, paragraph, box, folder=folder):
                from transformation.pdf.complex_script import take_paragraph
                SHAPED_PARAGRAPHS.append(take_paragraph(page, paragraph, box, reason='did_not_fit_slot'))
                if folder: save_records(folder, SHAPED_PARAGRAPHS)
            self._transformx_fallback_draw = fallback_draw

        try:
            result = original(self, document)
        finally:
            if folder:
                Path(folder, "panel-fitting.json").write_text(
                    json.dumps(self._transformx_panel_fit_results, ensure_ascii=False, indent=2), encoding="utf-8")
                Path(folder, "heading-fitting.json").write_text(
                    json.dumps(self._transformx_heading_fit_results, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                Path(folder, "table-fitting.json").write_text(
                    json.dumps(
                        {
                            "plans": plans,
                            "results": self._transformx_table_fit_results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

        if folder:
            after_report = inspect_table_cell_text(
                document, source_cells
            )
            Path(folder, "table-cell-text-after.json").write_text(
                json.dumps(after_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return result
    Typesetting.typesetting_document = guarded_typesetting
    from transformation.llm.ollama_native import TRANSLATION_FAILED

    TRANSLATION_FAILED.clear()

    # TRANSFORMX_TRACE_PARAGRAPHS=1: one line per paragraph BabelDOC prepares for translation
    # (paragraph-trace.jsonl in the run folder), to find paragraphs left untranslated without an error.
    if os.environ.get('TRANSFORMX_TRACE_PARAGRAPHS') == '1' and os.environ.get('TRANSFORMX_RUN_DIR'):
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator as _Traced
        _original_pre = _Traced.pre_translate_paragraph
        _trace_path = Path(os.environ['TRANSFORMX_RUN_DIR'], 'paragraph-trace.jsonl')

        def _traced_pre(self, paragraph, *args, **kwargs):
            text, translate_input = _original_pre(self, paragraph, *args, **kwargs)
            with _trace_path.open('a', encoding='utf-8') as trace:
                trace.write(json.dumps({'paragraph_id': paragraph.debug_id, 'layout_label': paragraph.layout_label,
                                        'prepared': text is not None, 'characters': len(paragraph.unicode or ''),
                                        'compositions': len(paragraph.pdf_paragraph_composition or ())}) + '\n')
            return text, translate_input
        _Traced.pre_translate_paragraph = _traced_pre
    # TRANSFORMX_BABELDOC_DEBUG=1: BabelDOC keeps its intermediate files. For investigating a document.
    if os.environ.get('TRANSFORMX_BABELDOC_DEBUG') == '1' and '--debug' not in sys.argv:
        sys.argv.append('--debug')
    # BabelDOC logs only the message of a failure ("translate error: maximum recursion depth exceeded",
    # a 50-page manual, 2026-11-19); the traceback (code locations, no document text) goes to the
    # job log too, so a failed document can be diagnosed from its log.
    import logging
    import traceback

    class _TracebackOnError(logging.Filter):
        def filter(self, record):
            if record.levelno >= logging.ERROR and 'translate error' in str(record.msg) and sys.exc_info()[0]:
                sys.stderr.write('TransformX: traceback of the BabelDOC failure:\n' + traceback.format_exc() + '\n')
            return True
    logging.getLogger('babeldoc.format.pdf.high_level').addFilter(_TracebackOnError())
    try:
        record_progress('worker_vendor_starting')
        try:
            runpy.run_module("babeldoc.main", run_name="__main__")
        except SystemExit as exit_:
            if exit_.code not in (None, 0): raise
        if SHAPED_PARAGRAPHS and not TRANSLATION_FAILED.is_set():
            draw_shaped_paragraphs()
        # Copy and search give the same characters the page shows (text_layer.py).
        try:
            from transformation.pdf.text_layer import plain_spaces_and_hyphens
            output_dir = Path(sys.argv[sys.argv.index('--output') + 1])
            changed = sum(plain_spaces_and_hyphens(p) for p in output_dir.glob('*.mono.pdf'))
            record_progress('text_layer_plain_characters', mappings=changed)
        except Exception as error:   # never fail a translated document for its copy text
            record_progress('text_layer_plain_characters_failed', error=type(error).__name__)
    finally:
        if TRANSLATION_FAILED.is_set():
            raise RuntimeError(
                "A protected paragraph translation failed. "
                "This run requires review; any generated PDF "
                "must not be treated as a successful result. "
                "Check process.log for the original error."
            )

if __name__ == "__main__":
    # TRANSFORMX_PROFILE_FILE: write a cProfile of this run there (for measuring; off by default).
    if os.environ.get("TRANSFORMX_PROFILE_FILE"):
        import cProfile
        profiler = cProfile.Profile()
        try:
            profiler.runcall(main)
        finally:
            profiler.dump_stats(os.environ["TRANSFORMX_PROFILE_FILE"])
    else:
        main()
