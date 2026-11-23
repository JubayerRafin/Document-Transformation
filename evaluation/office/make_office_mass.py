"""Word and PowerPoint mass-test documents: harder content, big documents, 97-2003 copies, PII.

    python -m evaluation.office.make_office_mass OUT --set dev|heldout [--big-pages 150] [--big-slides 120]

Per language (all eleven; text from the corpus statements, so translations have references):

  policy.docx    title, a table of contents field, sections with their own headers, headings, plain,
                 bold-label and numbered/bulleted paragraphs, footnotes, a comment, a tracked insertion
                 and deletion, a text box, a table with merged cells, page-number footers
  form.docx      a two-column form table, "Label<tab>Value" lines, check boxes, links
  layout.docx    a two-column section, pictures with captions, a pull-quote text box
  briefing.pptx  title, agenda, a chart (its categories, series and title in the language, data in an
                 embedded workbook), a table, two-content and comparison slides, grouped shapes, a
                 picture with text, speaker notes on every slide, text on the slide master
  long.pptx      30 bullet slides with notes

Big documents (English, Korean, German and Arabic; --big-pages / --big-slides): a policy document of
that many pages and a deck of that many slides. 97-2003 copies (form.doc, briefing.ppt) are made from
the .docx/.pptx by LibreOffice (form-97.doc, briefing-97.ppt): run `python -m evaluation.office.make_office_mass OUT --legacy` where
soffice is installed (the GPU server).

PII (English, German, French, Spanish, Korean): letter.docx (comments by a named author, a tracked
deletion holding a name, a header reference), form.docx (a table of personal data, a text box with a
phone number), contacts.pptx (a team table, notes) and roster.pptx (presenter on the slide master),
with an answer key in evaluation/pii's format. Arabic documents are written right to left.

The sets differ only in their seed; never tune on the held-out set.
"""
import argparse
import io
import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from evaluation.office.make_office import (COMPANIES, LATIN, Key, chart_png, rtl_frame, rtl_paragraph, save_docx,
                                           statement, text_png, wrap)
from evaluation.pii import generate as pii
from evaluation.robustness.make_fullpage import HEADINGS, ITEMS, LABELS, LANGS, SENTENCES, TABLE

SEEDS = {'dev': 20261130, 'heldout': 20261131}
BIG = ('en', 'ko', 'de', 'ar')
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
FOOTNOTES_CT = 'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml'
FOOTNOTES_RT = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes'


# Word building blocks ------------------------------------------------------------------------------
def xml(fragment):
    from docx.oxml import parse_xml
    return parse_xml(fragment)


def ns():
    return f'xmlns:w="{W}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'


class Notes:
    """Footnotes collected while writing; the part is added at the end (python-docx has no API)."""

    def __init__(self):
        self.texts = []

    def add(self, paragraph, text):
        self.texts.append(text)
        paragraph._p.append(xml(f'<w:r {ns()}><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                                f'<w:footnoteReference w:id="{len(self.texts)}"/></w:r>'))

    def attach(self, doc):
        if not self.texts:
            return
        from docx.opc.packuri import PackURI
        from docx.opc.part import Part
        body = ''.join(f'<w:footnote w:id="{i}"><w:p><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/>'
                       f'</w:r><w:r><w:t xml:space="preserve"> {escape(t)}</w:t></w:r></w:p></w:footnote>'
                       for i, t in enumerate(self.texts, 1))
        payload = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes xmlns:w="{W}">'
                   '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
                   '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p>'
                   f'</w:footnote>{body}</w:footnotes>')
        part = Part(PackURI('/word/footnotes.xml'), FOOTNOTES_CT, payload.encode('utf-8'), doc.part.package)
        doc.part.relate_to(part, FOOTNOTES_RT)


def toc(doc, entries):
    """A table of contents field with its last computed entries (heading, tab, page)."""
    for k, (title, page) in enumerate(entries):
        p = doc.add_paragraph()
        if k == 0:
            p._p.append(xml(f'<w:r {ns()}><w:fldChar w:fldCharType="begin"/></w:r>'))
            p._p.append(xml(f'<w:r {ns()}><w:instrText xml:space="preserve"> TOC \\o "1-3" \\h \\z \\u </w:instrText></w:r>'))
            p._p.append(xml(f'<w:r {ns()}><w:fldChar w:fldCharType="separate"/></w:r>'))
        p.add_run(title)
        p.add_run().add_tab()
        p.add_run(str(page))
        if k == len(entries) - 1:
            p._p.append(xml(f'<w:r {ns()}><w:fldChar w:fldCharType="end"/></w:r>'))


def page_field(paragraph):
    paragraph._p.append(xml(f'<w:fldSimple {ns()} w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'))


def tracked(doc, inserted, deleted, author='Reviewer'):
    p = doc.add_paragraph()
    p._p.append(xml(f'<w:ins {ns()} w:id="901" w:author="{escape(author)}" w:date="2026-01-05T09:00:00Z"><w:r>'
                    f'<w:t xml:space="preserve">{escape(inserted)} </w:t></w:r></w:ins>'))
    p._p.append(xml(f'<w:del {ns()} w:id="902" w:author="{escape(author)}" w:date="2026-01-05T09:00:00Z"><w:r>'
                    f'<w:delText xml:space="preserve">{escape(deleted)}</w:delText></w:r></w:del>'))
    return p


def text_box(doc, text, width=300, height=70):
    p = doc.add_paragraph()
    p._p.append(xml(f'<w:r {ns()}><w:pict><v:shape id="tb{random.randint(1000, 9999)}" o:spid="" '
                    f'style="width:{width}pt;height:{height}pt" stroked="t" fillcolor="#eef3f8"><v:textbox>'
                    f'<w:txbxContent><w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p></w:txbxContent>'
                    '</v:textbox></v:shape></w:pict></w:r>'))
    return p


def columns(section, count=2):
    section._sectPr.append(xml(f'<w:cols {ns()} w:num="{count}" w:space="720"/>'))


def item_rows(rng, lang, n):
    rows = []
    for item in [rng.choice(ITEMS[lang]) for _ in range(n)]:
        qty, price = rng.randint(1, 40), rng.randint(5, 900)
        rows.append([item, f'SKU-{rng.randint(1000, 9999)}', str(qty), f'{price}.00', f'{qty * price}.00',
                     f'2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'])
    return rows


def finish(doc, lang, extra=()):
    if lang == 'ar':
        paragraphs = list(doc.paragraphs) + [c.paragraphs[0] for t in doc.tables for r in t.rows for c in r.cells]
        for s in doc.sections:
            paragraphs += list(s.header.paragraphs) + list(s.footer.paragraphs)
        for p in paragraphs + list(extra):
            rtl_paragraph(p)


def policy_docx(rng, lang, used, sections=4):
    from docx import Document
    from docx.enum.section import WD_SECTION
    doc, notes = Document(), Notes()
    company = rng.choice(COMPANIES)
    doc.add_heading(LABELS[lang][0], 0)
    heads = [HEADINGS[lang][k % len(HEADINGS[lang])] for k in range(sections)]
    toc(doc, [(h, 2 + 3 * k) for k, h in enumerate(heads)])
    commented = False
    for k, head in enumerate(heads):
        section = doc.add_section(WD_SECTION.NEW_PAGE)
        section.header.is_linked_to_previous = False
        section.header.paragraphs[0].text = f'{company} · {head}'
        section.footer.is_linked_to_previous = False
        footer = section.footer.paragraphs[0]
        footer.text = f'{LABELS[lang][11]}: {company} · '
        page_field(footer)
        doc.add_heading(head, 1)
        for _ in range(3):
            p = doc.add_paragraph(statement(rng, lang, used) + ' ' + statement(rng, lang, used))
            if rng.random() < .5:
                notes.add(p, statement(rng, lang, used))
        p = doc.add_paragraph()
        p.add_run(HEADINGS[lang][(k + 1) % len(HEADINGS[lang])] + ': ').bold = True
        run = p.add_run(statement(rng, lang, used))
        if not commented:
            doc.add_comment(run, text=statement(rng, lang, used), author='Reviewer', initials='R')
            commented = True
        for style in ('List Number', 'List Bullet'):
            for _ in range(2):
                doc.add_paragraph(statement(rng, lang, used), style=style)
        if k % 2 == 0:
            table = doc.add_table(rows=1, cols=6)
            table.style = 'Table Grid'
            for cell, text in zip(table.rows[0].cells, TABLE[lang]):
                cell.text = text
            for values in item_rows(rng, lang, 4):
                for cell, text in zip(table.add_row().cells, values):
                    cell.text = text
            merged = table.add_row().cells
            merged[0].merge(merged[4]).text = LABELS[lang][8]
            merged[5].text = f'{rng.randint(1000, 90000)}.00'
        else:
            text_box(doc, statement(rng, lang, used))
        if k == 0:
            tracked(doc, statement(rng, lang, used), statement(rng, lang, used))
    finish(doc, lang)
    notes.attach(doc)
    return doc


def form_docx(rng, lang, used):
    from docx import Document
    doc = Document()
    doc.add_heading(LABELS[lang][1], 0)
    table = doc.add_table(rows=0, cols=2)
    table.style = 'Table Grid'
    for label, value in zip(TABLE[lang], item_rows(rng, lang, 1)[0]):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, value
    for label in HEADINGS[lang][:3]:
        p = doc.add_paragraph()
        p.add_run(label)
        p.add_run().add_tab()
        p.add_run(statement(rng, lang, used))
    for label in LABELS[lang][7:10]:
        doc.add_paragraph(f'☐ {label}')
    p = doc.add_paragraph(statement(rng, lang, used) + ' ')
    from evaluation.office.make_office import add_link
    add_link(p, 'https://www.example.com/terms', LABELS[lang][10])
    finish(doc, lang)
    return doc


def layout_docx(rng, lang, used):
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.shared import Inches
    doc = Document()
    doc.add_heading(LABELS[lang][3], 0)
    doc.add_paragraph(statement(rng, lang, used))
    section = doc.add_section(WD_SECTION.CONTINUOUS)
    columns(section)
    for k in range(6):
        doc.add_heading(HEADINGS[lang][k % len(HEADINGS[lang])], 2)
        doc.add_paragraph(statement(rng, lang, used) + ' ' + statement(rng, lang, used))
        if k == 2:
            doc.add_picture(io.BytesIO(chart_png(rng)), width=Inches(2.8))
            doc.add_paragraph(f'{LABELS[lang][6]} {k}: {statement(rng, lang, used)}').runs[0].italic = True
        if k == 4:
            text_box(doc, '“' + statement(rng, lang, used) + '”', width=200, height=90)
    if lang in LATIN:
        doc.add_picture(io.BytesIO(text_png([LABELS[lang][4]] + wrap(LABELS[lang][5], 40))), width=Inches(2.8))
    finish(doc, lang)
    return doc


# PowerPoint ----------------------------------------------------------------------------------------
def master_text(deck, text):
    from pptx.shapes.autoshape import Shape
    from pptx.util import Inches, Pt
    shapes = deck.slide_master.shapes   # MasterShapes has no add_textbox: the shape tree adds one
    ids = [int(v) for v in shapes._spTree.xpath('//p:cNvPr/@id')]
    sp = shapes._spTree.add_textbox(max(ids + [1]) + 1, 'Master text', Inches(0.3), Inches(7.0), Inches(6), Inches(0.35))
    box = Shape(sp, shapes)
    box.text_frame.text = text
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    return box.text_frame


def briefing_pptx(rng, lang, used):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches, Pt
    deck = Presentation()
    company = rng.choice(COMPANIES)
    frames = [master_text(deck, f'{company} · {LABELS[lang][0]}')]
    s = deck.slides.add_slide(deck.slide_layouts[0])
    s.shapes.title.text = LABELS[lang][0]
    s.placeholders[1].text = f'{company} · 2026'
    frames += [s.shapes.title.text_frame, s.placeholders[1].text_frame]
    s = deck.slides.add_slide(deck.slide_layouts[1])
    s.shapes.title.text = HEADINGS[lang][0]
    body = s.placeholders[1].text_frame
    body.text = HEADINGS[lang][1]
    for h in HEADINGS[lang][2:5]:
        body.add_paragraph().text = h
    frames += [s.shapes.title.text_frame, body]
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = HEADINGS[lang][2]
    frames.append(s.shapes.title.text_frame)
    data = CategoryChartData()
    data.categories = [rng.choice(ITEMS[lang]) for _ in range(4)]
    data.add_series(LABELS[lang][8], [rng.randint(10, 90) for _ in range(4)])
    data.add_series(LABELS[lang][7], [rng.randint(10, 90) for _ in range(4)])
    chart = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.6), Inches(8.8), Inches(4.6), data).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = HEADINGS[lang][2]
    chart.has_legend = True
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = HEADINGS[lang][3]
    frames.append(s.shapes.title.text_frame)
    shape = s.shapes.add_table(5, 5, Inches(0.4), Inches(1.6), Inches(9.2), Inches(3.2))
    for c, text in enumerate(TABLE[lang][:5]):
        shape.table.cell(0, c).text = text
    for r, values in enumerate(item_rows(rng, lang, 4), 1):
        for c, text in enumerate(values[:5]):
            shape.table.cell(r, c).text = text
    frames += [shape.table.cell(r, c).text_frame for r in range(5) for c in range(5)]
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    s = deck.slides.add_slide(deck.slide_layouts[3])   # two content
    s.shapes.title.text = HEADINGS[lang][4]
    for idx in (1, 2):
        frame = s.placeholders[idx].text_frame
        frame.text = statement(rng, lang, used)
        frame.add_paragraph().text = statement(rng, lang, used)
        frames.append(frame)
    frames.append(s.shapes.title.text_frame)
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    s = deck.slides.add_slide(deck.slide_layouts[5])
    s.shapes.title.text = HEADINGS[lang][5]
    frames.append(s.shapes.title.text_frame)
    group = s.shapes.add_group_shape()
    for k in range(3):
        g = group.shapes.add_textbox(Inches(0.5 + 3.1 * k), Inches(1.8), Inches(2.9), Inches(1.4))
        g.text_frame.word_wrap = True
        g.text_frame.text = statement(rng, lang, used)
        frames.append(g.text_frame)
    if lang in LATIN:
        s.shapes.add_picture(io.BytesIO(text_png([LABELS[lang][4]] + wrap(LABELS[lang][5], 40))), Inches(0.5), Inches(4.2),
                             width=Inches(5))
    s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
    if lang == 'ar':
        for frame in frames:
            rtl_frame(frame)
    out = io.BytesIO(); deck.save(out)
    return out.getvalue()


def long_pptx(rng, lang, used, slides=30):
    from pptx import Presentation
    deck = Presentation()
    frames = [master_text(deck, f'{rng.choice(COMPANIES)} · {LABELS[lang][0]}')]
    for k in range(slides):
        s = deck.slides.add_slide(deck.slide_layouts[1])
        s.shapes.title.text = f'{HEADINGS[lang][k % len(HEADINGS[lang])]} {k + 1}'
        body = s.placeholders[1].text_frame
        body.text = statement(rng, lang, used)
        for _ in range(2):
            body.add_paragraph().text = statement(rng, lang, used)
        s.notes_slide.notes_text_frame.text = statement(rng, lang, used)
        frames += [s.shapes.title.text_frame, body, s.notes_slide.notes_text_frame]
    if lang == 'ar':
        for frame in frames:
            rtl_frame(frame)
    out = io.BytesIO(); deck.save(out)
    return out.getvalue()


# PII -----------------------------------------------------------------------------------------------
def pii_letter(rng, lang, key):
    from docx import Document
    l = pii.L[lang]
    doc = Document()
    org = key.neg(rng.choice(pii.ORGS))
    sender, recipient, editor = pii.person(rng, lang), pii.person(rng, lang), pii.person(rng, lang)
    doc.core_properties.author = editor          # document properties: cleared by masking
    doc.sections[0].header.paragraphs[0].text = f"{org} · {l['ref']}: {key.pii('Identifiers', pii.ident(rng))}"
    doc.add_paragraph(key.pii('Names', sender)).runs[0].bold = True
    doc.add_paragraph(key.pii('Addresses', pii.address(rng, lang)))
    p = doc.add_paragraph(f"{l['phone']}: ")
    p.add_run(key.pii('Phone numbers', pii.phone(rng, lang)))
    doc.add_paragraph(f"{l['email']}: {key.pii('Email addresses', pii.email(rng, sender))}")
    doc.add_paragraph(key.pii('Names', recipient))
    run = doc.add_paragraph(f"{l['cust']}: ").add_run(key.pii('Identifiers', pii.ident(rng)))
    doc.add_comment(run, text=f"{l['ec']}: {key.pii('Phone numbers', pii.phone(rng, lang))}", author=editor, initials='X')
    doc.add_paragraph(f"{l['bank']}: {key.pii('Bank accounts', pii.iban(rng, pii.COUNTRY[lang]))}")
    tracked(doc, key.neg(pii.date(rng)), key.pii('Names', pii.person(rng, lang)) + ' ', author=editor)
    doc.add_paragraph(f"{l['code']}: {key.neg(pii.product(rng))} · {l['amount']}: {key.neg(pii.money(rng, pii.CUR[lang]))}")
    doc.add_paragraph(sender)
    return save_docx(doc), editor


def pii_form(rng, lang, key):
    from docx import Document
    l = pii.L[lang]
    doc = Document()
    doc.add_heading(key.neg(rng.choice(pii.ORGS)), 1)
    table = doc.add_table(rows=0, cols=2)
    table.style = 'Table Grid'
    who = pii.person(rng, lang)
    for label, value in ((l['name'], key.pii('Names', who)), (l['emp'], key.pii('Identifiers', pii.ident(rng))),
                         (l['email'], key.pii('Email addresses', pii.email(rng, who))), (l['addr'], key.pii('Addresses', pii.address(rng, lang))),
                         (l['dept'], key.neg('Logistics')), (l['start'], key.neg(pii.date(rng)))):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, value
    text_box(doc, f"{l['ec']}: {key.pii('Phone numbers', pii.phone(rng, lang))}")
    return save_docx(doc), None


def pii_contacts(rng, lang, key):
    from evaluation.office.make_office import contacts_pptx
    return contacts_pptx(rng, lang, key), None


def pii_roster(rng, lang, key):
    from pptx import Presentation
    from pptx.util import Inches
    l = pii.L[lang]
    deck = Presentation()
    presenter = pii.person(rng, lang)
    master_text(deck, f"{l['owner']}: {key.pii('Names', presenter)} · {key.pii('Email addresses', pii.email(rng, presenter))}")
    for k in range(3):
        s = deck.slides.add_slide(deck.slide_layouts[5])
        s.shapes.title.text = f"{l['shift']} {k + 1}"
        box = s.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(9), Inches(3))
        box.text_frame.word_wrap = True
        box.text_frame.text = f"{key.pii('Names', pii.person(rng, lang))} · {l['ext']} {rng.randint(100, 999)}"
        box.text_frame.add_paragraph().text = f"{l['member']}: {key.pii('Identifiers', pii.ident(rng))}"
        s.notes_slide.notes_text_frame.text = f"{l['phone']}: {key.pii('Phone numbers', pii.phone(rng, lang))}"
    out = io.BytesIO(); deck.save(out)
    return out.getvalue(), None


# Main ----------------------------------------------------------------------------------------------
def legacy(out):
    """form.doc and briefing.ppt from their .docx/.pptx (LibreOffice), added to the manifest."""
    soffice = shutil.which('soffice')
    if not soffice:
        raise SystemExit('LibreOffice (soffice) is needed for the 97-2003 copies.')
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    added = []
    for doc in list(manifest['documents']):
        name = doc['file']
        target = {'form.docx': ('doc', 'doc:MS Word 97'), 'briefing.pptx': ('ppt', 'ppt:MS PowerPoint 97')}.get(name.split('-')[-1])
        if not target:
            continue
        # <name>-97.doc: its own stem, so its results do not share the .docx's folder.
        new = name.rsplit('.', 1)[0] + '-97.' + target[0]
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / new.replace('.' + target[0], '.' + name.rsplit('.', 1)[1])
            shutil.copyfile(out / name, copy)
            subprocess.run([soffice, '-env:UserInstallation=' + Path(folder, 'p').as_uri(), '--headless', '--convert-to',
                            target[1], '--outdir', str(out), str(copy)], capture_output=True, timeout=300, check=True)
        if (out / new).exists():
            added.append(dict(doc, file=new, kind=doc['kind'] + '-97', format=target[0]))
    manifest['documents'] += [a for a in added if a['file'] not in {d['file'] for d in manifest['documents']}]
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(added), '97-2003 copies')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('out'); ap.add_argument('--set', choices=sorted(SEEDS))
    ap.add_argument('--big-pages', type=int, default=150); ap.add_argument('--big-slides', type=int, default=120)
    ap.add_argument('--legacy', action='store_true')
    args = ap.parse_args()
    out = Path(args.out)
    if args.legacy:
        return legacy(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEEDS[args.set])
    manifest = []

    def save(name, data, lang, kind, used, ext):
        (out / name).write_bytes(data)
        manifest.append({'file': name, 'lang': lang, 'kind': f'office-{kind}', 'format': ext, 'statements': sorted(used)})

    for lang in LANGS:
        for kind, build, ext in (('policy', policy_docx, 'docx'), ('form', form_docx, 'docx'), ('layout', layout_docx, 'docx'),
                                 ('briefing', briefing_pptx, 'pptx'), ('long', long_pptx, 'pptx')):
            used = set()
            data = build(random.Random(rng.random()), lang, used)
            save(f'{args.set}-{lang}-{kind}.{ext}', save_docx(data) if ext == 'docx' else data, lang, kind, used, ext)
        if lang in BIG:
            used = set()
            # About 2.2 pages per section at 11 pt.
            data = policy_docx(random.Random(rng.random()), lang, used, sections=max(4, round(args.big_pages / 2.2)))
            save(f'{args.set}-{lang}-bigpolicy.docx', save_docx(data), lang, 'bigpolicy', used, 'docx')
            used = set()
            data = long_pptx(random.Random(rng.random()), lang, used, slides=args.big_slides)
            save(f'{args.set}-{lang}-bigdeck.pptx', data, lang, 'bigdeck', used, 'pptx')
    (out / 'manifest.json').write_text(json.dumps({'set': args.set, 'documents': manifest}, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
    key = {'_meta': {'seed': SEEDS[args.set], 'generator': 'evaluation/office/make_office_mass.py',
                     'categories': ['Names', 'Email addresses', 'Phone numbers', 'Addresses', 'Identifiers', 'Bank accounts']}}
    (out / 'pii').mkdir(exist_ok=True)
    for lang in ('en', 'de', 'fr', 'es', 'ko'):
        for kind, build, ext in (('letter', pii_letter, 'docx'), ('form', pii_form, 'docx'),
                                 ('contacts', pii_contacts, 'pptx'), ('roster', pii_roster, 'pptx')):
            k = Key()
            data, author = build(random.Random(rng.random()), lang, k)
            name = f'{args.set}-pii-{lang}-{kind}'
            (out / 'pii' / f'{name}.{ext}').write_bytes(data)
            key[name] = {'kind': f'{kind} / {ext}', 'language': lang, 'variant': ext, 'pages': 1, 'file': f'{name}.{ext}',
                         'expected': k.expected, 'optional': [], 'negatives': sorted(set(k.negatives)),
                         'metadata_names': [author] if author else []}
    (out / 'pii' / 'answer_key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(manifest), 'translation documents,', len(key) - 1, 'PII documents ->', out)


if __name__ == '__main__':
    main()
