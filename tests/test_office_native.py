"""Word and PowerPoint in their own format (transformation/office/native.py), without models."""
import io
import zipfile

import pytest

docx = pytest.importorskip('docx')
pptx = pytest.importorskip('pptx')

from transformation.office import native  # noqa: E402
from transformation.office.package_ir import extract_package, write_package  # noqa: E402
from extraction.coordinator import text_regions  # noqa: E402


def word_document():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = 'Quarterly report'
    p = d.add_paragraph()
    p.add_run('Key results: ').bold = True
    p.add_run('revenue rose this quarter.')
    p = d.add_paragraph()
    for piece in ('All ship', 'ments must be ', 'inspected.'):
        p.add_run(piece)
    p = d.add_paragraph()
    p.add_run('Name')
    p.add_run().add_tab()
    p.add_run('Olivia Grant')
    p = d.add_paragraph('Read the ')
    rel = p.part.relate_to('https://example.com', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
                           is_external=True)
    link = OxmlElement('w:hyperlink'); link.set(qn('r:id'), rel)
    r = OxmlElement('w:r'); t = OxmlElement('w:t'); t.text = 'safety guide'; r.append(t); link.append(r)
    p._p.append(link)
    p.add_run(' first.')
    out = io.BytesIO(); d.save(out)
    return out.getvalue()


def translate(data, translator, filename='a.docx'):
    ir = extract_package(data, filename)
    regions = text_regions(ir)
    *_, result = list(native.translate_regions(regions, translator, workers=2))
    out, _ = write_package(data, filename.rsplit('.', 1)[1], result['updates'], [])
    return out, result


def paragraphs(data):
    return docx.Document(io.BytesIO(data)).paragraphs


def test_formatting_travels_with_markers():
    out, result = translate(word_document(), lambda text: text.upper())
    p = paragraphs(out)
    # The space after the label may move to the next run; the page looks the same.
    assert [(r.text.strip(), bool(r.bold)) for r in p[0].runs if r.text.strip()] == [('KEY RESULTS:', True), ('REVENUE ROSE THIS QUARTER.', False)]
    assert p[0].text == 'KEY RESULTS: REVENUE ROSE THIS QUARTER.'
    assert p[1].text == 'ALL SHIPMENTS MUST BE INSPECTED.'
    assert result['notes']['formatting_kept'] >= 2 and result['notes']['formatting_simplified'] == 0


def test_tab_separated_pieces_are_translated_apart():
    seen = []
    out, _ = translate(word_document(), lambda text: seen.append(text) or text.upper())
    assert 'Name' in seen and 'Olivia Grant' in seen and not any('NameOlivia' in s for s in seen)
    xml = zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode()
    assert '<w:tab' in xml and 'OLIVIA GRANT' in xml


def test_link_keeps_its_text_and_target():
    out, _ = translate(word_document(), lambda text: text.upper())
    xml = zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode()
    assert '<w:hyperlink' in xml and 'SAFETY GUIDE' in xml.split('<w:hyperlink', 1)[1].split('</w:hyperlink>')[0]


def test_lost_markers_put_the_whole_translation_in_the_main_run():
    out, result = translate(word_document(), lambda text: text.replace('**', '').upper())
    p = paragraphs(out)
    assert p[0].text == 'KEY RESULTS: REVENUE ROSE THIS QUARTER.'
    assert result['notes']['formatting_simplified'] >= 1


def test_failed_pieces_keep_source_and_are_reported():
    def translator(text):
        if 'inspected' in text:
            raise ValueError('check failed')
        return text.upper()
    out, result = translate(word_document(), translator)
    assert paragraphs(out)[1].text == 'All shipments must be inspected.'
    assert result['errors'] and result['errors'][0]['reason'] == 'check failed'


def test_identical_pieces_are_translated_once():
    calls = []
    d = docx.Document()
    for _ in range(5):
        d.add_paragraph('Total due')
    out = io.BytesIO(); d.save(out)
    translate(out.getvalue(), lambda text: calls.append(text) or text.upper())
    assert calls == ['Total due']


def test_masking_across_runs_keeps_styles():
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run('Contact: ').bold = True
    p.add_run('Olivia ')
    p.add_run('Grant, +44 20 7946 0001')
    data = io.BytesIO(); d.save(data); data = data.getvalue()
    regions = text_regions(extract_package(data, 'a.docx'))
    updates = native.mask_updates(regions, ['Olivia Grant', '+44 20 7946 0001'], native.mask_label('label', {
        'Olivia Grant': '[NAME]', '+44 20 7946 0001': '[PHONE]'}))
    out, _ = write_package(data, 'docx', updates, ['Olivia Grant', '+44 20 7946 0001'])
    runs = paragraphs(out)[0].runs
    assert ''.join(r.text for r in runs) == 'Contact: [NAME], [PHONE]' and runs[0].bold
    with pytest.raises(ValueError):
        write_package(data, 'docx', {}, ['Olivia Grant'])  # still in the file: refused


def test_word_value_in_longer_word_is_not_a_residual():
    d = docx.Document(); d.add_paragraph('Format the drive.')
    data = io.BytesIO(); d.save(data)
    out, _ = write_package(data.getvalue(), 'docx', {}, ['Mat'])
    assert paragraphs(out)[0].text == 'Format the drive.'


def test_right_to_left_direction_both_ways():
    data = word_document()
    ids = [r['id'] for r in text_regions(extract_package(data, 'a.docx')) if r['id'].startswith('word/document.xml')]
    rtl = native.set_direction(data, 'docx', ids, True)
    xml = zipfile.ZipFile(io.BytesIO(rtl)).read('word/document.xml').decode()
    assert xml.count('<w:bidi') == len(ids) and '<w:rtl' in xml
    assert docx.Document(io.BytesIO(rtl)).paragraphs  # still a valid document
    back = native.set_direction(rtl, 'docx', ids, False)
    xml = zipfile.ZipFile(io.BytesIO(back)).read('word/document.xml').decode()
    assert '<w:bidi' not in xml and '<w:rtl' not in xml


def test_tracked_deletions_are_removed_before_masking():
    data = word_document()
    archive = zipfile.ZipFile(io.BytesIO(data))
    xml = archive.read('word/document.xml').decode()
    deleted = ('<w:del w:id="9" w:author="x" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>Daniel Okafor</w:delText></w:r></w:del>'
               '<w:ins w:id="10" w:author="x" w:date="2026-01-01T00:00:00Z"><w:r><w:t>kept</w:t></w:r></w:ins>')
    xml = xml.replace('</w:body>', '<w:p>' + deleted + '</w:p></w:body>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as target:
        for item in archive.infolist():
            target.writestr(item, xml if item.filename == 'word/document.xml' else archive.read(item))
    accepted, count = native.accept_revisions(out.getvalue(), 'docx')
    text = zipfile.ZipFile(io.BytesIO(accepted)).read('word/document.xml').decode()
    assert count == 2 and 'delText' not in text and 'Daniel Okafor' not in text and '>kept<' in text and '<w:ins' not in text


def test_author_properties_are_cleared():
    d = docx.Document(); d.core_properties.author = 'Olivia Grant'; d.core_properties.last_modified_by = 'Olivia Grant'
    data = io.BytesIO(); d.save(data)
    out = native.scrub_properties(data.getvalue(), 'docx')
    props = docx.Document(io.BytesIO(out)).core_properties
    assert props.author == '' and props.last_modified_by == ''


def test_powerpoint_runs_and_direction():
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    box = slide.shapes.add_textbox(0, 0, 3000000, 600000)
    p = box.text_frame.paragraphs[0]
    label = p.add_run(); label.text = 'Note: '; label.font.bold = True
    p.add_run().text = 'check the cable.'
    data = io.BytesIO(); deck.save(data); data = data.getvalue()
    out, result = translate(data, lambda text: text.upper(), 'a.pptx')
    shape = [s for s in pptx.Presentation(io.BytesIO(out)).slides[0].shapes if s.has_text_frame and s.text_frame.text.strip()][-1]
    runs = [(r.text.strip(), bool(r.font.bold)) for r in shape.text_frame.paragraphs[0].runs if r.text.strip()]
    assert runs == [('NOTE:', True), ('CHECK THE CABLE.', False)]
    ids = [r['id'] for r in text_regions(extract_package(out, 'a.pptx'))]
    rtl = native.set_direction(out, 'pptx', ids, True)
    xml = zipfile.ZipFile(io.BytesIO(rtl)).read('ppt/slides/slide1.xml').decode()
    assert 'rtl="1"' in xml and 'algn="r"' in xml
    pptx.Presentation(io.BytesIO(rtl))


def test_values_read_across_lines():
    texts = ['12 Harbour Road', 'Leeds LS1 4AB', 'Mail: sofia_mbeki@tidewatersuppl.example', 'Date: 2025-05-07']
    # An address of separate paragraphs is masked line by line ...
    assert native.office_values(['12 Harbour Road\nLeeds LS1 4AB'], texts) == [
        '12 Harbour Road\nLeeds LS1 4AB', '12 Harbour Road', 'Leeds LS1 4AB']
    # ... a value wrapped only in the drawing is matched whole, and short pieces are never masked alone.
    wrapped = ['sofia_mbeki@tide\nwatersuppl.exam\nple']
    assert native.office_values(wrapped, texts) == wrapped
    assert native.office_values(['FX-2618-1519\n07'], texts) == ['FX-2618-1519\n07', 'FX-2618-1519']
    masked = native.masked_runs([texts[2]], native.office_values(wrapped, texts), lambda v: '[EMAIL]')
    assert masked == ['Mail: [EMAIL]']


def test_powerpoint_text_has_no_word_attributes():
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = 'Operations'
    data = io.BytesIO(); deck.save(data)
    out, result = translate(data.getvalue(), lambda text: text.upper(), 'a.pptx')
    xml = zipfile.ZipFile(io.BytesIO(out)).read('ppt/slides/slide1.xml').decode()
    assert 'OPERATIONS' in xml and 'xml:space' not in xml


def test_master_prompts_are_not_translated():
    deck = pptx.Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = 'Plan'
    data = io.BytesIO(); deck.save(data)
    texts = [r['text'] for r in text_regions(extract_package(data.getvalue(), 'a.pptx'))]
    assert 'Plan' in texts and not any('Click to edit' in t for t in texts)


def test_table_reads_right_to_left_with_its_text():
    d = docx.Document()
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = 'Item'; table.rows[0].cells[1].text = 'Total'
    data = io.BytesIO(); d.save(data); data = data.getvalue()
    ids = [r['id'] for r in text_regions(extract_package(data, 'a.docx'))]
    xml = zipfile.ZipFile(io.BytesIO(native.set_direction(data, 'docx', ids, True))).read('word/document.xml').decode()
    assert '<w:bidiVisual' in xml


def test_piece_of_a_wrapped_address_masks_the_whole_address():
    # The drawing wrapped the address in a narrow cell; the detector returned two pieces.
    masked = native.masked_runs(['Mail: mathis.chevalier@stonebridgemut.example, then'],
                                ['mathis.chevalier', '@stonebridgemu'], lambda v: '[EMAIL]')
    assert masked == ['Mail: [EMAIL], then']
    assert native.masked_runs(['Format the drive'], ['Mat'], lambda v: '[X]') is None


def test_comment_authors_are_cleared_when_masking():
    d = docx.Document()
    run = d.add_paragraph('Total due').runs[0]
    d.add_comment(run, text='Check this', author='Olivia Grant', initials='OG')
    data = io.BytesIO(); d.save(data)
    out = native.scrub_properties(data.getvalue(), 'docx')
    xml = zipfile.ZipFile(io.BytesIO(out)).read('word/comments.xml').decode()
    assert 'Olivia Grant' not in xml and 'w:author="Reviewer"' in xml and 'w:initials="R"' in xml


def test_a_wrapped_value_line_inside_a_longer_token_is_not_masked():
    # Word and PowerPoint mass test, 2026-11-24: an ID cell read with the end of the next cell's date.
    texts = ['P-870-1828', 'Datum: 2025-09-04', '119 Juniper Lane', 'Springfield, IL 62704']
    assert native.office_values(['P-870-1828\n-09-04'], texts) == ['P-870-1828\n-09-04', 'P-870-1828']
    assert native.office_values(['119 Juniper Lane\nSpringfield, IL 62704'], texts) == [
        '119 Juniper Lane\nSpringfield, IL 62704', '119 Juniper Lane', 'Springfield, IL 62704']
