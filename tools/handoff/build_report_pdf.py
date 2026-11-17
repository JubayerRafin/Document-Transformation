"""Print the same handoff content to a portable PDF handbook."""
import html
import textwrap

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Preformatted

from tools.handoff.build_package import OUT, PAGES


def main():
    path = OUT / 'TransformX_PII_Handoff/TransformX_Code_and_PII_Handoff.pdf'
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='BodyHand', fontName='Helvetica', fontSize=10, leading=13, spaceAfter=7))
    styles.add(ParagraphStyle(name='TitleHand', fontName='Helvetica-Bold', fontSize=21, leading=25, spaceAfter=14, textColor=colors.black))
    styles.add(ParagraphStyle(name='HeadingHand', fontName='Helvetica-Bold', fontSize=13, leading=16, spaceAfter=9, spaceBefore=5, textColor=colors.black))
    styles.add(ParagraphStyle(name='CodeHand', fontName='Courier', fontSize=8, leading=10, spaceAfter=9, backColor=colors.HexColor('#F1F4F7'), borderPadding=7))
    styles.add(ParagraphStyle(name='CellHand', fontName='Helvetica', fontSize=9, leading=11))
    styles.add(ParagraphStyle(name='HeadCellHand', fontName='Helvetica-Bold', fontSize=9, leading=11))
    flow = []
    width = A4[0] - 90
    for number, (title, blocks) in enumerate(PAGES):
        if number:
            flow.append(PageBreak())
        flow.append(Paragraph(html.escape(title), styles['TitleHand']))
        for kind, content in blocks:
            if kind == 'p':
                flow.append(Paragraph(html.escape(content), styles['BodyHand']))
            elif kind == 'h':
                flow.append(Paragraph(html.escape(content), styles['HeadingHand']))
            elif kind == 'code':
                lines = []
                for line in content.splitlines():
                    lines.extend(textwrap.wrap(line, width=92, replace_whitespace=False, drop_whitespace=False) or [''])
                flow.append(Preformatted('\n'.join(lines), styles['CodeHand']))
            else:
                cells = [[Paragraph(html.escape(value), styles['HeadCellHand' if n == 0 else 'CellHand']) for value in row] for n, row in enumerate(content)]
                widths = [width*.38,width*.62] if len(content[0]) == 2 else [width*.50,width*.25,width*.25]
                table = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
                table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE6F1')),
                    ('GRID',(0,0),(-1,-1),.5,colors.HexColor('#D9D9D9')),
                    ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),
                    ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
                flow.extend([table,Spacer(1,8)])
    def footer(canvas, doc):
        canvas.setFont('Helvetica',8)
        canvas.drawString(45,25,'TransformX | PII teammate handoff | 17 October 2026')
        canvas.drawRightString(A4[0]-45,25,str(doc.page))
    SimpleDocTemplate(str(path),pagesize=A4,rightMargin=45,leftMargin=45,topMargin=40,bottomMargin=42,
                      title='TransformX Code and PII Handoff',author='TransformX').build(flow,onFirstPage=footer,onLaterPages=footer)
    start = path.parent/'START_HERE.txt'
    start.write_text(start.read_text(encoding='utf-8').replace('TransformX_Code_and_PII_Handoff.docx','TransformX_Code_and_PII_Handoff.pdf'),encoding='utf-8')
    print(path)


if __name__ == '__main__':
    main()
