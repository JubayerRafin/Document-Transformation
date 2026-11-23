"""Word and PowerPoint evaluation documents: translation (11 languages) and PII (5 languages).

    python -m evaluation.office.make_office OUT --set dev|heldout

Translation documents use the corpus statements (evaluation/robustness/make_fullpage.SENTENCES,
written in all eleven languages), so every translated statement has a reference. Each language gets:

  report.docx  header and footer, title, headings, plain and mixed-format paragraphs (bold label +
               text, one paragraph split into same-format runs), a bulleted list, a table, a link,
               an italic note, a chart picture and, for Latin-script languages, a picture with text
  deck.pptx    title slide, bullet slide, table slide, a slide with a text box, picture, grouped
               shapes and speaker notes, and a slide with mixed-format bullets

Arabic documents are written right to left (w:bidi, a:pPr rtl). PII documents (en, de, fr, es, ko):
letter.docx (sender, recipient, references, bank account, signature, header reference) and
contacts.pptx (presenter, team table, notes), with an answer key in the PII set's format
(evaluation/pii/score.py): expected [category, value, where], negatives that must stay visible.

manifest.json lists every file with its language, kind and the statements it uses (run_site reads
it). The sets differ only in their seed; never tune on the held-out set.
"""
import argparse
import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from evaluation.robustness.make_fullpage import HEADINGS, ITEMS, LABELS, LANGS, SENTENCES, TABLE
from evaluation.pii import generate as pii

SEEDS = {'dev': 20261124, 'heldout': 20261125}
LATIN = {'en', 'fr', 'de', 'es', 'pt'}
COMPANIES = ['Northwind Logistics', 'Halcyon Biotech', 'Bluepeak Energy', 'Orchid Retail Group', 'Atlas Engineering']
FONT_DIRS = [Path('C:/Windows/Fonts'), Path('/usr/share/fonts/truetype/dejavu')]


def font(size, bold=False):
    for folder in FONT_DIRS:
        for name in (('arialbd.ttf', 'DejaVuSans-Bold.ttf') if bold else ('arial.ttf', 'DejaVuSans.ttf')):
            if (folder / name).exists():
                return ImageFont.truetype(str(folder / name), size)
    return ImageFont.load_default()


def statement(rng, lang, used):
    i = rng.randrange(len(SENTENCES[lang]))
    used.add(i)
    text = SENTENCES[lang][i]
    return text.replace('{pct}', str(rng.randint(3, 40))).replace('{n}', str(rng.randint(2, 90)))


def chart_png(rng):
    image = Image.new('RGB', (640, 360), 'white')
    draw = ImageDraw.Draw(image)
    colors = ['#1f3a5f', '#5b2a86', '#0f5132', '#7a2e0e']
    for k in range(6):
        h = rng.randint(60, 300)
        draw.rectangle([40 + k * 100, 340 - h, 110 + k * 100, 340], fill=colors[k % 4])
    draw.line([30, 340, 620, 340], fill='black', width=2)
    out = io.BytesIO(); image.save(out, 'PNG')
    return out.getvalue()


def text_png(lines):
    """A notice picture with text, for the image OCR and translation path (Latin scripts only)."""
    big, small = font(30, True), font(24)
    image = Image.new('RGB', (900, 90 + 40 * len(lines)), '#fff4d6')
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 4, image.width - 5, image.height - 5], outline='#b45309', width=4)
    draw.text((28, 22), lines[0], fill='#7c2d12', font=big)
    for k, line in enumerate(lines[1:]):
        draw.text((28, 70 + 40 * k), line, fill='black', font=small)
    out = io.BytesIO(); image.save(out, 'PNG')
    return out.getvalue()


def wrap(text, width=58):
    words, lines, line = text.split(), [], ''
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line); line = w
        else:
            line = (line + ' ' + w).strip()
    return lines + [line] if line else lines


# Word ------------------------------------------------------------------------------------------
def rtl_paragraph(paragraph):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    ppr = paragraph._p.get_or_add_pPr()
    if ppr.find(qn('w:bidi')) is None:
        ppr.insert(0, OxmlElement('w:bidi'))
    for run in paragraph.runs:
        rpr = run._r.get_or_add_rPr()
        if rpr.find(qn('w:rtl')) is None:
            rpr.append(OxmlElement('w:rtl'))


def add_link(paragraph, url, text):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    rel = paragraph.part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
                                   is_external=True)
    link = OxmlElement('w:hyperlink'); link.set(qn('r:id'), rel)
    run = OxmlElement('w:r'); rpr = OxmlElement('w:rPr')
    style = OxmlElement('w:rStyle'); style.set(qn('w:val'), 'Hyperlink'); rpr.append(style)
    color = OxmlElement('w:color'); color.set(qn('w:val'), '0563C1'); rpr.append(color)
    underline = OxmlElement('w:u'); underline.set(qn('w:val'), 'single'); rpr.append(underline)
    run.append(rpr)
    t = OxmlElement('w:t'); t.text = text; run.append(t)
    link.append(run); paragraph._p.append(link)


def report_docx(rng, lang, used):
    from docx import Document
    from docx.shared import Inches, Pt
    doc = Document()
    company = rng.choice(COMPANIES)
    section = doc.sections[0]
    section.header.paragraphs[0].text = f"{company} · {LABELS[lang][0]}"
    section.footer.paragraphs[0].text = f"{LABELS[lang][11]}: {company}"
    doc.add_heading(LABELS[lang][0], 0)
    doc.add_heading(HEADINGS[lang][0], 1)
    doc.add_paragraph(statement(rng, lang, used) + ' ' + statement(rng, lang, used))
    p = doc.add_paragraph()
    p.add_run(HEADINGS[lang][1] + ': ').bold = True
    p.add_run(statement(rng, lang, used))
    split = statement(rng, lang, used)
    cuts = sorted(rng.sample(range(1, len(split) - 1), 2))
    p = doc.add_paragraph()
    for a, b in zip([0] + cuts, cuts + [len(split)]):
        p.add_run(split[a:b])
    for _ in range(3):
        doc.add_paragraph(statement(rng, lang, used), style='List Bullet')
    doc.add_heading(HEADINGS[lang][2], 1)
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    for cell, text in zip(table.rows[0].cells, TABLE[lang]):
        cell.text = ''
        cell.paragraphs[0].add_run(text).bold = True
    for item in rng.sample(ITEMS[lang], 3):
        qty, price = rng.randint(1, 40), rng.randint(5, 900)
        values = [item, f'SKU-{rng.randint(1000, 9999)}', str(qty), f'{price}.00', f'{qty * price}.00',
                  f'2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}']
        for cell, text in zip(table.add_row().cells, values):
            cell.text = text
    p = doc.add_paragraph(statement(rng, lang, used) + ' ')
    add_link(p, 'https://www.example.com/report', 'www.example.com/report')
    doc.add_picture(io.BytesIO(chart_png(rng)), width=Inches(4.5))
    doc.add_paragraph(f'{LABELS[lang][6]} 1')
    if lang in LATIN:
        notice = [LABELS[lang][4]] + wrap(LABELS[lang][5])
        doc.add_picture(io.BytesIO(text_png(notice)), width=Inches(5.5))
    doc.add_page_break()
    doc.add_heading(HEADINGS[lang][5], 1)
    doc.add_paragraph(statement(rng, lang, used))
    note = doc.add_paragraph()
    note.add_run(statement(rng, lang, used)).italic = True
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            if run.font.size is None and paragraph.style.name == 'Normal':
                run.font.size = Pt(11)
    if lang == 'ar':
        for paragraph in list(doc.paragraphs) + [c.paragraphs[0] for r in table.rows for c in r.cells] + \
                list(section.header.paragraphs) + list(section.footer.paragraphs):
            rtl_paragraph(paragraph)
    return doc


def save_docx(doc):
    out = io.BytesIO(); doc.save(out); return out.getvalue()


# PowerPoint ------------------------------------------------------------------------------------
def rtl_frame(frame):
    for paragraph in frame.paragraphs:
        ppr = paragraph._p.get_or_add_pPr()
        ppr.set('rtl', '1'); ppr.set('algn', 'r')


def deck_pptx(rng, lang, used):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    deck = Presentation()
    frames = []  # the default template's 4:3 slide (10 x 7.5 in), whose layouts fit it
    company = rng.choice(COMPANIES)
    s = deck.slides.add_slide(deck.slide_layouts[0])
    s.shapes.title.text = LABELS[lang][0]
    s.placeholders[1].text = f'{company} · 2026'
    frames += [s.shapes.title.text_frame, s.placeholders[1].text_frame]
    s = deck.slides.add_slide(deck.slide_layouts[1])
    s.shapes.title.text = HEADINGS[lang][1]
    body = s.placeholders[1].text_frame
    body.text = statement(rng, lang, used)
    for _ in range(2):
        body.add_paragraph().text = statement(rng, lang, used)
    frames += [s.shapes.title.text_frame, body]
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = HEADINGS[lang][2]
    frames.append(s.shapes.title.text_frame)
    shape = s.shapes.add_table(4, 5, Inches(0.4), Inches(1.8), Inches(9.2), Inches(2.4))
    for c, text in enumerate(TABLE[lang][:5]):
        shape.table.cell(0, c).text = text
    for r, item in enumerate(rng.sample(ITEMS[lang], 3), 1):
        qty, price = rng.randint(1, 40), rng.randint(5, 900)
        for c, text in enumerate([item, f'SKU-{rng.randint(1000, 9999)}', str(qty), f'{price}.00', f'{qty * price}.00']):
            shape.table.cell(r, c).text = text
    frames += [shape.table.cell(r, c).text_frame for r in range(4) for c in range(5)]
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = HEADINGS[lang][3]
    frames.append(s.shapes.title.text_frame)
    box = s.shapes.add_textbox(Inches(0.4), Inches(1.6), Inches(4.6), Inches(1.6))
    box.text_frame.word_wrap = True
    box.text_frame.text = statement(rng, lang, used)
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    frames.append(box.text_frame)
    s.shapes.add_picture(io.BytesIO(chart_png(rng)), Inches(5.2), Inches(1.6), width=Inches(4.4))
    if lang in LATIN:
        s.shapes.add_picture(io.BytesIO(text_png([LABELS[lang][4]] + wrap(LABELS[lang][5], 40))), Inches(0.4), Inches(4.9),
                             width=Inches(4.6))
    group = s.shapes.add_group_shape()
    for k, label in enumerate([LABELS[lang][7], LABELS[lang][8]]):
        g = group.shapes.add_textbox(Inches(5.2 + 2.25 * k), Inches(5.0), Inches(2.15), Inches(0.6))
        g.text_frame.text = label
        frames.append(g.text_frame)
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    s = deck.slides.add_slide(deck.slide_layouts[1])
    s.shapes.title.text = HEADINGS[lang][5]
    body = s.placeholders[1].text_frame
    for k in range(2):
        paragraph = body.paragraphs[0] if k == 0 else body.add_paragraph()
        label = paragraph.add_run(); label.text = HEADINGS[lang][6 + k] + ': '; label.font.bold = True
        paragraph.add_run().text = statement(rng, lang, used)
    frames += [s.shapes.title.text_frame, body]
    if lang == 'ar':
        for frame in frames:
            rtl_frame(frame)
    out = io.BytesIO(); deck.save(out)
    return out.getvalue()


# PII -------------------------------------------------------------------------------------------
class Key:
    def __init__(self):
        self.expected, self.negatives = [], []

    def pii(self, category, value):
        self.expected.append([category, value, 'text']); return value

    def neg(self, value):
        self.negatives.append(value); return value


def letter_docx(rng, lang, key):
    from docx import Document
    l = pii.L[lang]
    doc = Document()
    org = key.neg(rng.choice(pii.ORGS))
    sender, recipient = pii.person(rng, lang), pii.person(rng, lang)
    ref = pii.ident(rng)
    doc.sections[0].header.paragraphs[0].text = f"{org} · {l['ref']}: {key.pii('Identifiers', ref)}"
    doc.add_paragraph(key.pii('Names', sender)).runs[0].bold = True
    doc.add_paragraph(key.pii('Addresses', pii.address(rng, lang)))
    doc.add_paragraph(f"{l['phone']}: {key.pii('Phone numbers', pii.phone(rng, lang))}")
    doc.add_paragraph(f"{l['email']}: {key.pii('Email addresses', pii.email(rng, sender))}")
    doc.add_paragraph(f"{l['date']}: {key.neg(pii.date(rng))}")
    doc.add_paragraph()
    doc.add_paragraph(key.pii('Names', recipient))
    doc.add_paragraph(key.pii('Addresses', pii.address(rng, lang)))
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.add_run(f"{l['cust']}: ").bold = True
    p.add_run(key.pii('Identifiers', pii.ident(rng)))
    p = doc.add_paragraph()
    p.add_run(f"{l['bank']}: ").bold = True
    p.add_run(key.pii('Bank accounts', pii.iban(rng, pii.COUNTRY[lang])))
    table = doc.add_table(rows=1, cols=3); table.style = 'Table Grid'
    for cell, text in zip(table.rows[0].cells, [l['code'], l['desc'], l['amount']]):
        cell.text = text
    for item in rng.sample(['Steel shelving unit', 'Cable organiser kit', 'Label printer', 'Desk lamp, LED'], 2):
        for cell, text in zip(table.add_row().cells, [key.neg(pii.product(rng)), key.neg(item), key.neg(pii.money(rng, pii.CUR[lang]))]):
            cell.text = text
    doc.add_paragraph()
    doc.add_paragraph(f"{l['ec']}: {key.pii('Names', pii.person(rng, lang))}, {key.pii('Phone numbers', pii.phone(rng, lang))}")
    doc.add_paragraph()
    doc.add_paragraph(key.pii('Names', sender) if rng.random() < .5 else sender)
    doc.sections[0].footer.paragraphs[0].text = key.neg(org)
    return save_docx(doc)


def contacts_pptx(rng, lang, key):
    from pptx import Presentation
    from pptx.util import Inches
    l = pii.L[lang]
    deck = Presentation()
    org = key.neg(rng.choice(pii.ORGS))
    presenter = pii.person(rng, lang)
    s = deck.slides.add_slide(deck.slide_layouts[0])
    s.shapes.title.text = org
    s.placeholders[1].text = f"{l['owner']}: {key.pii('Names', presenter)}"
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = l['present']
    shape = s.shapes.add_table(4, 4, Inches(0.4), Inches(1.6), Inches(9.2), Inches(2.6))
    for c, text in enumerate([l['name'], l['email'], l['phone'], l['emp']]):
        shape.table.cell(0, c).text = text
    for r in range(1, 4):
        who = pii.person(rng, lang)
        for c, text in enumerate([key.pii('Names', who), key.pii('Email addresses', pii.email(rng, who, org)),
                                  key.pii('Phone numbers', pii.phone(rng, lang)), key.pii('Identifiers', pii.ident(rng))]):
            shape.table.cell(r, c).text = text
    box = s.shapes.add_textbox(Inches(0.4), Inches(4.8), Inches(9.2), Inches(1))
    box.text_frame.text = f"{l['code']}: {key.neg(pii.product(rng))} · {l['date']}: {key.neg(pii.date(rng))}"
    s.notes_slide.notes_text_frame.text = f"{l['ec']}: {key.pii('Phone numbers', pii.phone(rng, lang))}"
    out = io.BytesIO(); deck.save(out)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out'); ap.add_argument('--set', choices=sorted(SEEDS), required=True)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEEDS[args.set])
    manifest = []
    for lang in LANGS:
        for kind, build, ext in (('report', report_docx, 'docx'), ('deck', deck_pptx, 'pptx')):
            used = set()
            r = random.Random(rng.random())
            data = build(r, lang, used)
            data = save_docx(data) if ext == 'docx' else data
            name = f'{args.set}-{lang}-{kind}.{ext}'
            (out / name).write_bytes(data)
            manifest.append({'file': name, 'lang': lang, 'kind': f'office-{kind}', 'format': ext, 'statements': sorted(used)})
    key = {'_meta': {'seed': SEEDS[args.set], 'generator': 'evaluation/office/make_office.py',
                     'categories': ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts']}}
    (out / 'pii').mkdir(exist_ok=True)
    for lang in ('en', 'de', 'fr', 'es', 'ko'):
        for kind, build, ext in (('letter', letter_docx, 'docx'), ('contacts', contacts_pptx, 'pptx')):
            k = Key()
            name = f'{args.set}-pii-{lang}-{kind}'
            (out / 'pii' / f'{name}.{ext}').write_bytes(build(random.Random(rng.random()), lang, k))
            key[name] = {'kind': f'{kind} / {ext}', 'language': lang, 'variant': ext, 'pages': 1, 'file': f'{name}.{ext}',
                         'expected': k.expected, 'optional': [], 'negatives': sorted(set(k.negatives))}
    (out / 'pii' / 'answer_key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'manifest.json').write_text(json.dumps({'set': args.set, 'documents': manifest}, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
    print(len(manifest), 'translation documents,', len(key) - 1, 'PII documents ->', out)


if __name__ == '__main__':
    main()
