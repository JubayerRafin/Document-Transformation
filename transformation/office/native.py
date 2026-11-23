"""Word and PowerPoint documents translated and masked in their own format.

The package is changed in place (transformation/office/package_ir.write_package): every paragraph
keeps its runs, so styles, numbering, tables, pictures, notes and layout objects stay as they were.

Translation. A paragraph is cut at tabs and line breaks (a form line "Name<tab>Value" is two pieces).
Within a piece, neighbouring runs that look the same (package_ir.run_key) are merged; what remains
different from the piece's main formatting (a bold label, an italic word, a link) is sent marked as
**...**, which TranslateGemma keeps in every language tried (2026-11-23; it turns HTML tags into
markdown). The translated parts go back, in order, into runs of the same formatting. When the
markers do not come back intact, or the order cannot be kept, the whole translation goes into the
piece's main run and the other runs are emptied: the text is right, the emphasis simplified.
Identical pieces are translated once; pieces are translated concurrently.

Direction. Translating into Arabic (or Hebrew, Persian, Urdu) from a left-to-right language makes
the changed paragraphs right-to-left (w:bidi and w:rtl in Word, a:pPr rtl and right alignment in
PowerPoint); translating out of them does the reverse.

Masking. Values are masked where they stand, across runs, with the job's style ([NAME]-style labels,
pseudonyms, or a block for "box"). Before masking, tracked changes are accepted (deleted text would
otherwise still be in the file) and the author fields of the document properties are cleared.

PII detection reads the document as LibreOffice draws it, with speaker notes (PowerPoint) and
comments (Word) included: detection_pdf().

Fitting (PowerPoint). A translation that no longer fits its text box is measured, not guessed:
fit_slides() lets every text box grow to its text in a measuring copy, reads the grown heights from
LibreOffice's drawing and shrinks the font of each box that grew, down to a floor.
"""
import re
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from transformation.office.package_ir import A, P, W, open_package, serialize

RTL_LANGUAGES = {'ar', 'he', 'fa', 'ur'}
MARK = '**'
BOX_MASK = '█████'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


# Translation ------------------------------------------------------------------------------------
def pieces(region):
    """Node indices of a paragraph, cut where a tab or line break stands between two text nodes."""
    meta = region['metadata']
    texts = meta['run_texts']
    breaks = meta.get('run_breaks') or [None] * len(texts)
    out, current = [], []
    for index, separator in enumerate(breaks):
        if separator and current:
            out.append(current); current = []
        current.append(index)
    if current:
        out.append(current)
    return out


def segments(region, nodes):
    """[(key, [node indices], text)] with neighbouring same-format nodes merged."""
    meta = region['metadata']
    keys = meta.get('run_keys') or [''] * len(meta['run_texts'])
    out = []
    for i in nodes:
        text = meta['run_texts'][i]
        if out and out[-1][0] == keys[i]:
            out[-1][1].append(i); out[-1][2] += text
        else:
            out.append([keys[i], [i], text])
    return [s for s in out if s[2] != ''] or out[:1]


def marked(segs):
    """(text to translate, main key, keys of the marked parts in order) for one piece."""
    weight = {}
    for key, _, text in segs:
        weight[key] = weight.get(key, 0) + len(text.strip())
    main = max(weight, key=weight.get)
    plain = ''.join(s[2] for s in segs)
    emphasized = [s for s in segs if s[0] != main and s[2].strip()]
    if not emphasized or '*' in plain:
        return plain, main, []
    parts, keys, previous_marked = [], [], False
    for key, _, text in segs:
        if key == main or not text.strip():
            parts.append(text); previous_marked = False
            continue
        if previous_marked:  # two marked parts touching: cannot be told apart
            return plain, main, []
        core = text.strip()
        lead, tail = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        parts.append(lead + MARK + core + MARK + tail); keys.append(key); previous_marked = True
    return ''.join(parts), main, keys


def place(translated, region, nodes, main, keys):
    """{node index: text} putting the translated parts into runs of their own formatting, in order;
    None when the markers did not come back intact or the order cannot be kept."""
    meta = region['metadata']
    run_keys = meta.get('run_keys') or [''] * len(meta['run_texts'])
    split = re.split(r'\*\*(.+?)\*\*', translated, flags=re.S)
    if len(split) != 2 * len(keys) + 1 or '*' in ''.join(split[0::2]):
        return None
    wanted = []
    for k, text in enumerate(split):
        wanted.append((main if k % 2 == 0 else keys[k // 2], text))
    out, position = {i: '' for i in nodes}, 0
    for key, text in wanted:
        if not text:
            continue
        while position < len(nodes) and run_keys[nodes[position]] != key:
            position += 1
        if position == len(nodes):
            return None
        out[nodes[position]] += text
    return out


def collapse(translated, region, nodes, main):
    """The whole translation in the piece's first main-format run, the other runs emptied."""
    run_keys = region['metadata'].get('run_keys') or [''] * len(region['metadata']['run_texts'])
    target = next((i for i in nodes if run_keys[i] == main and region['metadata']['run_texts'][i].strip()), nodes[0])
    out = {i: '' for i in nodes}
    out[target] = translated.replace(MARK, '')
    return out


def keep_edges(source, translated):
    """The piece's own leading and trailing spaces (the model strips them; a run "Total: " needs its space)."""
    lead, tail = source[:len(source) - len(source.lstrip())], source[len(source.rstrip()):]
    return lead + translated.strip() + tail


def translate_regions(regions, translate_one, workers=16):
    """Translate every text region; a generator of progress dicts ending with
    {'updates': {region id: [run texts]}, 'errors': [...], 'pairs': [(source, translation)], 'notes': {...}}.

    translate_one(text) -> translated text (raises ValueError, OSError or TimeoutError on failure)."""
    jobs = []  # (region, nodes, main, keys, text)
    for region in regions:
        texts = region.get('metadata', {}).get('run_texts')
        if not texts or not region['text'].strip():
            continue
        for nodes in pieces(region):
            segs = segments(region, nodes)
            text, main, keys = marked(segs)
            jobs.append((region, nodes, main, keys, text))
    unique = list(dict.fromkeys(j[4].strip() for j in jobs if any(c.isalpha() for c in j[4])))
    results, failures = {}, {}

    def work(text):
        try:
            return text, translate_one(text), None
        except (ValueError, OSError, TimeoutError) as exc:
            if MARK in text:
                # The markers themselves can trip a check; the plain text is then translated and
                # placed in the piece's main run (formatting simplified, text translated).
                try:
                    return text, translate_one(text.replace(MARK, '')), None
                except (ValueError, OSError, TimeoutError) as again:
                    exc = again
            return text, None, str(exc)[:300]

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for text, translation, error in pool.map(work, unique):
            done += 1
            if error is None:
                results[text] = translation
            else:
                failures[text] = error
            if done % 8 == 0 or done == len(unique):
                yield {'pieces_translated': done, 'pieces': len(unique)}
    updates, errors, pairs = {}, [], []
    notes = {'pieces': len(jobs), 'unique_pieces': len(unique), 'formatting_kept': 0, 'formatting_simplified': 0}
    for region, nodes, main, keys, text in jobs:
        runs = updates.setdefault(region['id'], list(region['metadata']['run_texts']))
        key = text.strip()
        if key not in results:
            if key in failures:
                errors.append({'region_id': region['id'], 'reason': failures[key]})
            continue
        translation = keep_edges(text, results[key])
        placed = place(translation, region, nodes, main, keys) if keys else None
        if keys:
            notes['formatting_kept' if placed is not None else 'formatting_simplified'] += 1
        for i, value in (placed or collapse(translation, region, nodes, main)).items():
            runs[i] = value
        pairs.append((text.replace(MARK, '').strip(), translation.replace(MARK, '').strip()))
    # Regions whose pieces all failed keep their source text; unchanged entries are dropped.
    source = {r['id']: r['metadata'].get('run_texts') for r in regions if r.get('metadata')}
    updates = {k: v for k, v in updates.items() if v != source.get(k)}
    yield {'updates': updates, 'errors': errors, 'pairs': pairs, 'notes': notes}


# Masking ----------------------------------------------------------------------------------------
def mask_label(style, labels):
    if style == 'box':
        return lambda value: BOX_MASK
    return lambda value: labels.get(value, '[REDACTED]')


def office_values(values, texts=()):
    """Values to mask in the package. The detector reads the document as drawn, where a value can
    wrap ("sofia_mbeki@tide / watersuppl.exam / ple" in a narrow table cell): it is matched whole,
    line breaks aside (literal_spans ignores spaces). Only a value found in no single paragraph of
    the package (texts), such as an address block of separate paragraphs, is also masked line by
    line, lines shorter than four characters left out ("07", "Pr": the next cell's text)."""
    from transformation.validation.value_redaction import literal_spans
    out = []
    for value in values:
        whole = value.strip()
        if len(whole) < 2:
            continue
        out.append(whole)
        parts = [p.strip(' ,;') for p in whole.splitlines() if p.strip(' ,;')]
        if len(parts) > 1 and not any(literal_spans(t, whole) for t in texts):
            # A line is masked only where it stands as a whole token: a table cell's ID read with the
            # end of the next cell's date ("P-870-1828" / "-09-04") masked the date "2025-09-04"
            # whole (Word and PowerPoint mass test, 2026-11-24). Hyphens join codes and dates.
            alone = lambda part: any(re.search(r'(?<![\w-])' + re.escape(part) + r'(?![\w-])', t) for t in texts)
            out.extend(p for p in parts if len(p) >= 4 and (alone(p) or not texts))
    return list(dict.fromkeys(out))


def token_spans(text, value):
    """(start, end) of each whitespace-delimited token of text that holds value (spaces and line
    breaks of value ignored) where the output check would find it, though not as a whole word."""
    from transformation.validation.output_validation import residual_in
    needle = ''.join(value.split())
    if len(needle) < 4 or not residual_in(value, text):
        return []
    spans = []
    for token in re.finditer(r'\S+', text):
        start, end = token.start(), token.end()
        while start < end and text[start] in '([{"\'«“':
            start += 1
        while end > start and text[end - 1] in '.,;:!?)]}"\'»”':
            end -= 1   # sentence punctuation after an address stays
        if needle.casefold() in text[start:end].casefold():
            spans.append((start, end))
    return spans


def masked_runs(texts, values, label):
    """Run texts with every value replaced by label(value), across run boundaries, styles kept."""
    from transformation.validation.value_redaction import literal_spans
    text = ''.join(texts)
    owners = [i for i, t in enumerate(texts) for _ in t]
    matches = []
    for value in sorted(set(values), key=len, reverse=True):
        for start, end in literal_spans(text, value):
            if not any(start < b and end > a for a, b, _ in matches):
                matches.append((start, end, value))
    # A piece of a value the drawing wrapped ("@stonebridgemu" of "mathis.chevalier@stonebridgemut
    # .example" in a narrow table cell, 2026-11-24) is inside a longer token, so the word-bounded match
    # above skips it while the output check (residual_in) still finds it and blocks the result. Such a
    # piece masks the whole whitespace-delimited token it stands in.
    for value in sorted(set(values), key=len, reverse=True):
        for start, end in token_spans(text, value):
            # The token replaces the masks inside it ("mathis.chevalier" of the same address).
            inside = [m for m in matches if start <= m[0] and m[1] <= end]
            if any(start < b and end > a for a, b, _ in matches if (a, b, _) not in inside):
                continue
            for m in inside:
                matches.remove(m)
            matches.append((start, end, value))
    if not matches:
        return None
    starts = {a: (b, v) for a, b, v in matches}
    result, index = [''] * len(texts), 0
    while index < len(text):
        if index in starts:
            end, value = starts[index]
            result[owners[index]] += label(value); index = end
        else:
            result[owners[index]] += text[index]; index += 1
    return result


def mask_updates(regions, values, label):
    updates = {}
    for region in regions:
        texts = region.get('metadata', {}).get('run_texts')
        if texts:
            masked = masked_runs(texts, values, label)
            if masked is not None:
                updates[region['id']] = masked
    return updates


def accept_revisions(data, kind):
    """Word tracked changes accepted: deleted and moved-away text removed, insertions kept as text.
    Returns (data, number of revisions)."""
    if kind != 'docx':
        return data, 0
    out, count = BytesIO(), 0
    with open_package(data, kind) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename.startswith('word/') and item.filename.endswith('.xml'):
                root = ET.fromstring(payload)
                parents = {c: p for p in root.iter() for c in p}
                changed = False
                for tag in ('del', 'moveFrom'):
                    for node in list(root.iter('{' + W + '}' + tag)):
                        if node in parents and parents[node].tag != '{' + W + '}rPr':
                            parents[node].remove(node); count += 1; changed = True
                for tag in ('ins', 'moveTo'):
                    for node in list(root.iter('{' + W + '}' + tag)):
                        parent = parents.get(node)
                        if parent is None or parent.tag == '{' + W + '}rPr':
                            continue
                        index = list(parent).index(node)
                        parent.remove(node)
                        for k, child in enumerate(list(node)):
                            parent.insert(index + k, child)
                        count += 1; changed = True
                parents = {c: p for p in root.iter() for c in p}
                for tag in ('rPrChange', 'pPrChange', 'sectPrChange', 'tblPrChange', 'trPrChange', 'tcPrChange'):
                    for node in list(root.iter('{' + W + '}' + tag)):
                        if node in parents:
                            parents[node].remove(node); changed = True
                if changed:
                    payload = serialize(root, payload)
            target.writestr(item, payload)
    return out.getvalue(), count


AUTHOR_ATTRIBUTES = {'author', 'initials', 'userId', 'providerId'}


def scrub_properties(data, kind):
    """Author, editor and company fields of the document properties emptied, and the people who wrote
    comments or tracked changes named "Reviewer": Word keeps each comment's author and initials
    (word/comments.xml, people.xml) and PowerPoint its comment authors (commentAuthors.xml,
    authors.xml) as attributes no page shows but anyone opening the file can read."""
    fields = {'{http://purl.org/dc/elements/1.1/}creator', '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy',
              '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Company',
              '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Manager'}
    out = BytesIO()
    with open_package(data, kind) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename in ('docProps/core.xml', 'docProps/app.xml'):
                root = ET.fromstring(payload)
                changed = False
                for node in root.iter():
                    if node.tag in fields and (node.text or '').strip():
                        node.text = ''; changed = True
                if changed:
                    payload = serialize(root, payload)
            elif item.filename.endswith('.xml') and (re.search(r'(^|/)(comments[^/]*|people|commentAuthors|authors)\.xml$', item.filename)
                                                     or item.filename.startswith(('word/document', 'word/header', 'word/footer',
                                                                                  'word/footnotes', 'word/endnotes'))):
                root = ET.fromstring(payload)
                changed = False
                for node in root.iter():
                    for name in list(node.attrib):
                        local = name.split('}')[-1]
                        if local in AUTHOR_ATTRIBUTES and node.attrib[name] not in ('', 'Reviewer', 'R'):
                            node.set(name, 'R' if local == 'initials' else '' if local in ('userId', 'providerId') else 'Reviewer')
                            changed = True
                if changed:
                    payload = serialize(root, payload)
            target.writestr(item, payload)
    return out.getvalue()


# Direction --------------------------------------------------------------------------------------
_PPR_AFTER_BIDI = ['adjustRightInd', 'snapToGrid', 'spacing', 'ind', 'contextualSpacing', 'mirrorIndents',
                   'suppressOverlap', 'jc', 'textDirection', 'textAlignment', 'textboxTightWrap', 'outlineLvl',
                   'divId', 'cnfStyle', 'rPr', 'sectPr', 'pPrChange']
_RPR_AFTER_RTL = ['cs', 'em', 'lang', 'eastAsianLayout', 'specVanish', 'oMath']


def _insert_ordered(parent, element, after_names):
    names = ['{' + W + '}' + n for n in after_names]
    for index, child in enumerate(list(parent)):
        if child.tag in names:
            parent.insert(index, element); return
    parent.append(element)


def set_direction(data, kind, region_ids, rtl):
    """Right-to-left (rtl=True) or left-to-right paragraphs for the given region ids."""
    if not region_ids:
        return data
    wanted = {}
    for rid in region_ids:
        part, _, index = rid.rpartition(':p')
        wanted.setdefault(part, set()).add(int(index))
    tag = W if kind == 'docx' else A
    out = BytesIO()
    with open_package(data, kind) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename in wanted:
                root = ET.fromstring(payload)
                changed = set()
                for index, paragraph in enumerate(root.iter('{' + tag + '}p')):
                    if index in wanted[item.filename]:
                        (_word_direction if kind == 'docx' else _slide_direction)(paragraph, rtl)
                        changed.add(paragraph)
                if kind == 'docx':
                    # A table whose text changed direction reads from the other side too (first
                    # column on the right in Arabic): w:bidiVisual.
                    for table in root.iter('{' + W + '}tbl'):
                        if any(p in changed for p in table.iter('{' + W + '}p')):
                            _table_direction(table, rtl)
                payload = serialize(root, payload)
            target.writestr(item, payload)
    return out.getvalue()


def _word_direction(paragraph, rtl):
    ppr = paragraph.find('{' + W + '}pPr')
    if ppr is None:
        if not rtl:
            return
        ppr = ET.Element('{' + W + '}pPr'); paragraph.insert(0, ppr)
    bidi = ppr.find('{' + W + '}bidi')
    if rtl and bidi is None:
        _insert_ordered(ppr, ET.Element('{' + W + '}bidi'), _PPR_AFTER_BIDI)
    elif rtl and bidi is not None:
        bidi.attrib.pop('{' + W + '}val', None)
    elif bidi is not None:
        ppr.remove(bidi)
    for run in paragraph.iter('{' + W + '}r'):
        rpr = run.find('{' + W + '}rPr')
        if rpr is None:
            if not rtl:
                continue
            rpr = ET.Element('{' + W + '}rPr'); run.insert(0, rpr)
        mark = rpr.find('{' + W + '}rtl')
        if rtl and mark is None:
            _insert_ordered(rpr, ET.Element('{' + W + '}rtl'), _RPR_AFTER_RTL)
        elif not rtl and mark is not None:
            rpr.remove(mark)


def _table_direction(table, rtl):
    props = table.find('{' + W + '}tblPr')
    if props is None:
        props = ET.Element('{' + W + '}tblPr'); table.insert(0, props)
    mark = props.find('{' + W + '}bidiVisual')
    if rtl and mark is None:
        after = ['tblStyleRowBandSize', 'tblStyleColBandSize', 'tblW', 'jc', 'tblCellSpacing', 'tblInd', 'tblBorders',
                 'shd', 'tblLayout', 'tblCellMar', 'tblLook', 'tblCaption', 'tblDescription', 'tblPrChange']
        _insert_ordered(props, ET.Element('{' + W + '}bidiVisual'), after)
    elif not rtl and mark is not None:
        props.remove(mark)


def _slide_direction(paragraph, rtl):
    ppr = paragraph.find('{' + A + '}pPr')
    if ppr is None:
        ppr = ET.Element('{' + A + '}pPr'); paragraph.insert(0, ppr)
    was_rtl = ppr.get('rtl') == '1'
    if rtl:
        ppr.set('rtl', '1')
        if ppr.get('algn') in (None, 'l'):
            ppr.set('algn', 'r')
    else:
        ppr.attrib.pop('rtl', None)
        if was_rtl and ppr.get('algn') == 'r':
            ppr.set('algn', 'l')


# Rendering --------------------------------------------------------------------------------------
NOTES_FILTER = {'pptx': 'pdf:impress_pdf_Export:{"ExportNotesPages":{"type":"boolean","value":"true"}}',
                'docx': 'pdf:writer_pdf_Export:{"ExportNotesInMargin":{"type":"boolean","value":"true"}}'}


def detection_pdf(data, kind):
    """The document as LibreOffice draws it, with speaker notes pages (PowerPoint) or comments in the
    margin (Word), for the PDF PII detector."""
    from transformation.office.office import render
    return render(data, 'source.' + kind, convert=NOTES_FILTER[kind])[0]


# PowerPoint fitting -----------------------------------------------------------------------------
def _measuring_copy(data):
    """A copy where every top-level text box grows to its text and is outlined in a colour that
    names it; returns (bytes, {colour: (slide number, shape id, original height in EMU)})."""
    from pptx import Presentation
    from pptx.oxml.ns import qn
    from lxml import etree
    deck = Presentation(BytesIO(data))
    colours, n = {}, 0
    for number, slide in enumerate(deck.slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text_frame.text.strip() or shape.height is None or shape.rotation:
                continue
            n += 1
            colour = f'{(n * 2654435761) % 0xFFFFFF:06X}'
            while colour in colours or colour in ('000000', 'FFFFFF'):
                n += 1; colour = f'{(n * 2654435761) % 0xFFFFFF:06X}'
            colours[colour] = (number, shape.shape_id, shape.height)
            body = shape.text_frame._txBody.find(qn('a:bodyPr'))
            for tag in ('a:normAutofit', 'a:noAutofit', 'a:spAutoFit'):
                for node in body.findall(qn(tag)):
                    body.remove(node)
            etree.SubElement(body, qn('a:spAutoFit'))
            sppr = shape._element.spPr
            for node in sppr.findall(qn('a:ln')):
                sppr.remove(node)
            line = etree.SubElement(sppr, qn('a:ln'), w='12700')
            fill = etree.SubElement(line, qn('a:solidFill'))
            etree.SubElement(fill, qn('a:srgbClr'), val=colour)
    out = BytesIO(); deck.save(out)
    return out.getvalue(), colours


def measure_overflow(data):
    """{(slide number, shape id): (grown height / box height, font size in points)} for text boxes
    whose text is taller than the box, from LibreOffice's drawing of a measuring copy. The size is
    the largest text size drawn inside the grown box (the size runs inherit when they set none)."""
    import pymupdf
    from transformation.office.office import render
    copy, colours = _measuring_copy(data)
    if not colours:
        return {}
    pdf, _ = render(copy, 'measure.pptx')
    grown = {}
    with pymupdf.open(stream=pdf, filetype='pdf') as doc:
        for page_number, page in enumerate(doc, 1):
            spans = None
            for drawing in page.get_drawings():
                colour = drawing.get('color')
                if not colour:
                    continue
                key = '%02X%02X%02X' % tuple(round(c * 255) for c in colour[:3])
                if key not in colours or colours[key][0] != page_number:
                    continue
                _, shape_id, height = colours[key]
                box_height, rect = height / 12700, drawing['rect']
                if box_height <= 0 or rect.height <= box_height * 1.04 + 2:
                    continue
                if spans is None:
                    spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']
                             if s['text'].strip()]
                sizes = [s['size'] for s in spans if rect.contains(pymupdf.Rect(s['bbox']).tl + (1, 1))]
                grown[(page_number, shape_id)] = (rect.height / box_height, max(sizes) if sizes else None)
    return grown


def measure_tables(data):
    """{(slide number, shape id): (available height / drawn height, font size)} for tables whose
    drawn rows run past the bottom of the slide (rows grow with their text)."""
    import pymupdf
    from pptx import Presentation
    from transformation.office.office import render
    deck = Presentation(BytesIO(data))
    frames = [(n, s) for n, slide in enumerate(deck.slides, 1) for s in slide.shapes if getattr(s, 'has_table', False) and s.has_table]
    if not frames:
        return {}
    pdf, _ = render(data, 'tables.pptx')
    out = {}
    with pymupdf.open(stream=pdf, filetype='pdf') as doc:
        for number, shape in frames:
            if number > len(doc):
                continue
            page = doc[number - 1]
            x0, y0 = shape.left / 12700, shape.top / 12700
            x1 = x0 + shape.width / 12700
            spans = [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']
                     if s['text'].strip() and x0 - 2 <= s['bbox'][0] <= x1 and s['bbox'][1] >= y0 - 2]
            if not spans:
                continue
            bottom = max(s['bbox'][3] for s in spans)
            limit = page.rect.height - 8
            if bottom > limit and bottom > y0:
                out[(number, shape.shape_id)] = (max(0.3, (limit - y0) / (bottom - y0)), max(s['size'] for s in spans))
    return out


def _shrink(frames, factor, measured, minimum_pt):
    from pptx.util import Pt
    changed = False
    for frame in frames:
        for paragraph in frame.paragraphs:
            for run in paragraph.runs:
                size = run.font.size.pt if run.font.size else measured
                if not size:
                    continue
                new = max(minimum_pt, round(size * factor * 2) / 2)
                if new < size - 0.25:
                    run.font.size = Pt(new); changed = True
    return changed


def _beyond(measured, baseline, slack=1.03):
    """Measurements worse than the source deck's own: LibreOffice draws some boxes taller than
    PowerPoint does (a title placeholder 8% taller, a subtitle twice as tall, in the source too), and
    a source that already overflows is not the translation's doing. The ratio returned is relative."""
    out = {}
    for key, (ratio, size) in measured.items():
        base = baseline.get(key, (1.0, None))[0]
        if ratio > base * slack + 0.02:
            out[key] = (ratio / base, size)
    return out


def fit_slides(data, baseline=None, floor=0.6, minimum_pt=9, rounds=3):
    """Shrink the font of PowerPoint text boxes (and tables) whose translation no longer fits, compared
    with the source deck (baseline, the same shapes before translation). Returns (data, report):
    each change, and any box still too tall at the floor."""
    from pptx import Presentation
    from pptx.oxml.ns import qn
    report = {'changes': [], 'still_overflowing': []}
    scale = {}
    base_boxes = measure_overflow(baseline) if baseline else {}
    base_tables = measure_tables(baseline) if baseline else {}
    base_tables = {k: (1 / r, s) for k, (r, s) in base_tables.items()}  # as "drawn / available"
    for _ in range(rounds):
        grown = _beyond(measure_overflow(data), base_boxes)
        tables = {k: (1 / r, s) for k, (r, s) in _beyond({k: (1 / r, s) for k, (r, s) in measure_tables(data).items()},
                                                         base_tables).items()}
        report['still_overflowing'] = []
        if not grown and not tables:
            break
        deck = Presentation(BytesIO(data))
        changed = False
        for number, slide in enumerate(deck.slides, 1):
            for shape in slide.shapes:
                key = (number, shape.shape_id)
                if key in grown:
                    ratio, measured = grown[key]
                    factor = (1 / ratio) ** 0.5 * 0.96
                    frames = [shape.text_frame]
                elif key in tables:
                    ratio, measured = tables[key]
                    factor = ratio ** 0.5 * 0.96
                    frames = [cell.text_frame for row in shape.table.rows for cell in row.cells]
                else:
                    continue
                factor = max(factor, floor / scale.get(key, 1.0))
                if factor >= 0.995:
                    report['still_overflowing'].append({'slide': number, 'shape_id': shape.shape_id})
                    continue
                if key in grown:
                    body = shape.text_frame._txBody.find(qn('a:bodyPr'))
                    for node in body.findall(qn('a:normAutofit')):
                        node.attrib.pop('fontScale', None); node.attrib.pop('lnSpcReduction', None)
                if _shrink(frames, factor, measured, minimum_pt):
                    changed = True
                    scale[key] = scale.get(key, 1.0) * factor
                    report['changes'].append({'slide': number, 'shape_id': shape.shape_id, 'factor': round(factor, 3)})
                else:
                    report['still_overflowing'].append({'slide': number, 'shape_id': shape.shape_id})
        if not changed:
            break
        out = BytesIO(); deck.save(out); data = out.getvalue()
    else:
        left = {**_beyond(measure_overflow(data), base_boxes),
                **_beyond({k: (1 / r, s) for k, (r, s) in measure_tables(data).items()}, base_tables)}
        report['still_overflowing'] = [{'slide': s, 'shape_id': i} for (s, i) in left]
    return data, report


_STR_CACHE = re.compile(r'<(?P<p>(?:\w+:)?)strCache\b[^>]*>.*?</(?P=p)strCache>', re.S)
_CACHE_VALUE = re.compile(r'(<(?P<p>(?:\w+:)?)v>)(?P<text>[^<]*)(</(?P=p)v>)')


def translate_chart_labels(data, translate_one, workers=8):
    """Chart category and series names translated in the chart's text cache, which PowerPoint and Word
    draw: in the Word and PowerPoint mass test every category ("Barcode scanner") and series name
    ("Total due") stayed in English while titles were translated (2026-11-24). Each distinct name is
    translated once through the same checked call as paragraphs; a name that fails its checks keeps
    its text. Only the cached text changes (the part's other bytes stay as they are); the chart's
    embedded workbook keeps the original names, so editing the data in Office shows those again.
    Returns (package, [(name, translation)], [names that failed])."""
    from html import escape, unescape
    with zipfile.ZipFile(BytesIO(data)) as package:
        charts = [n for n in package.namelist() if re.search(r'/charts/chart[^/]*\.xml$', n)]
        parts = {n: package.read(n).decode('utf-8') for n in charts}
    names = {}
    for xml in parts.values():
        for block in _STR_CACHE.finditer(xml):
            for value in _CACHE_VALUE.finditer(block.group(0)):
                text = unescape(value.group('text')).strip()
                if text and re.search(r'[^\W\d_]', text):
                    names.setdefault(text, None)
    if not names:
        return data, [], []
    failed = []

    def work(text):
        try:
            return text, translate_one(text)
        except Exception:
            failed.append(text)
            return text, None
    with ThreadPoolExecutor(max(1, min(workers, len(names)))) as pool:
        done = {a: b for a, b in pool.map(work, list(names)) if b and b.strip()}
    if not done:
        return data, [], failed

    def rewrite(block):
        def one(value):
            text = unescape(value.group('text'))
            new = done.get(text.strip())
            return value.group(1) + escape(new.strip(), quote=False) + value.group(4) if new else value.group(0)
        return _CACHE_VALUE.sub(one, block.group(0))
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename in parts:
                payload = _STR_CACHE.sub(rewrite, parts[item.filename]).encode('utf-8')
            target.writestr(item, payload)
    return out.getvalue(), sorted(done.items()), failed
