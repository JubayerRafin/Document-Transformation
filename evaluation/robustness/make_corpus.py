"""Generate a varied, synthetic PDF corpus for robustness testing (never for tuning).

    python -m evaluation.robustness.make_corpus OUT_DIR [--set dev|heldout] [--count N]
        [--fonts DIR] [--long PAGES] [--sources en,ko,ja,zh,ar,hi,bn]

Each document mixes a document kind (report, invoice, form, contract, slides, newsletter, manual,
paper, timetable, letter) with a page set-up (size, orientation, rotation, columns, fonts), a source
language and optionally a scanned copy (rendered, skewed, noisy, JPEG) or an edge case (encrypted,
damaged, blank or image-only pages). Values (IDs, emails, phone numbers, amounts, dates) are random
per seed, so nothing can be memorised. `--set heldout` uses different seeds: measure on it, never
fix against its individual files. A manifest.json lists every file with what it exercises.

Fonts: Noto / Source Han / GoNotoKurrent files (BabelDOC downloads them to ~/.cache/babeldoc/fonts).
"""
import argparse
import io
import json
import random
import string
from pathlib import Path

import pymupdf

KINDS = ['report', 'invoice', 'form', 'contract', 'slides', 'newsletter', 'manual', 'paper', 'timetable', 'letter']
SIZES = {'A4': (595, 842), 'Letter': (612, 792), 'A5': (420, 595), 'A3': (842, 1191), 'Legal': (612, 1008)}

# ---------------------------------------------------------------- text pools (synthetic content)
EN = {
    'company': ['Northwind Logistics', 'Halcyon Biotech', 'Bluepeak Energy', 'Orchid Retail Group', 'Meridian Health Partners',
                'Copperleaf Software', 'Granite Harbor Bank', 'Silverline Transit', 'Juniper Foods', 'Atlas Engineering'],
    'person': ['Olivia Grant', 'Daniel Okafor', 'Mei Lin Zhao', 'Ravi Menon', 'Sofia Alvarez', 'Thomas Becker',
               'Aiko Tanaka', 'Hassan Karimi', 'Emily Roberts', 'Lucas Moreau', 'Grace Kim', 'Arjun Patel'],
    'city': ['Rotterdam', 'Busan', 'Austin', 'Lyon', 'Nagoya', 'Pune', 'Leeds', 'Valencia', 'Porto', 'Calgary'],
    'sentences': [
        'Revenue grew steadily during the quarter, driven mainly by new contracts in the logistics segment.',
        'Operating costs remained within budget, although energy prices increased in the second month.',
        'The project team completed the migration of the customer database ahead of schedule.',
        'All shipments must be inspected on arrival and any damage reported within two business days.',
        'The committee recommends extending the pilot programme to three additional regional offices.',
        'Employees should complete the annual security training before the end of the fiscal year.',
        'Delivery times improved after the warehouse introduced automated sorting for small parcels.',
        'The supplier confirmed that the replacement parts will be available from the central depot.',
        'Customer satisfaction scores rose for the third consecutive quarter, reaching a new high.',
        'Maintenance work on the northern line will cause short delays on weekday evenings.',
        'The board approved the revised budget after a detailed review of capital expenditure.',
        'Any changes to the agreement must be made in writing and signed by both parties.',
        'Please keep this document for your records and contact us if any details are incorrect.',
        'The new policy applies to all contractors working on company premises from next month.',
        'Our analysis shows that most support requests concern password resets and invoice copies.',
        'Results were consistent across all test sites, with a variation of less than five percent.',
        'The device should be stored in a dry place at a temperature between 5 and 30 degrees.',
        'Participants were asked to rate each statement on a scale from one to five.',
        'The audit found no material weaknesses in the internal control environment.',
        'Training sessions will be recorded and made available on the internal learning portal.',
    ],
    'headings': ['Executive Summary', 'Background', 'Key Findings', 'Financial Overview', 'Next Steps', 'Risks and Mitigation',
                 'Scope of Work', 'Methodology', 'Results', 'Recommendations', 'Payment Terms', 'Contact Information'],
    'items': ['Pallet wrap, 500 mm', 'Thermal labels (box of 1,000)', 'Barcode scanner', 'Safety gloves, size L',
              'Cloud storage, 1 TB annual plan', 'On-site training (half day)', 'Replacement battery pack',
              'Ergonomic office chair', 'Network switch, 24-port', 'Consulting hours'],
}
OTHER = {  # short native paragraphs for non-English sources (synthetic, written for this corpus)
    'ko': ['이번 분기 매출은 물류 부문의 신규 계약에 힘입어 꾸준히 증가했습니다.',
           '모든 화물은 도착 즉시 검사해야 하며 손상이 있으면 영업일 기준 2일 이내에 보고해야 합니다.',
           '위원회는 시범 사업을 세 곳의 지역 사무소로 확대할 것을 권고합니다.',
           '직원은 회계연도가 끝나기 전에 연례 보안 교육을 이수해야 합니다.',
           '고객 만족도는 3분기 연속 상승하여 사상 최고치를 기록했습니다.',
           '계약 내용의 변경은 반드시 서면으로 작성하고 양 당사자가 서명해야 합니다.'],
    'ja': ['今四半期の売上は、物流部門の新規契約により着実に増加しました。',
           'すべての貨物は到着時に検査し、損傷があれば2営業日以内に報告してください。',
           '委員会は試験プログラムを3つの地域事務所に拡大することを推奨します。',
           '従業員は会計年度末までに年次セキュリティ研修を修了する必要があります。',
           '顧客満足度は3四半期連続で上昇し、過去最高を記録しました。',
           '契約内容の変更は書面で行い、双方が署名しなければなりません。'],
    'zh': ['本季度收入稳步增长，主要得益于物流业务的新合同。',
           '所有货物到达后必须立即检查，如有损坏须在两个工作日内报告。',
           '委员会建议将试点项目扩大到另外三个地区办事处。',
           '员工应在财政年度结束前完成年度安全培训。',
           '客户满意度连续第三个季度上升，创下新高。',
           '对协议的任何修改必须以书面形式作出并由双方签字。'],
    'ar': ['نما الإيراد بشكل مطرد خلال الربع بفضل العقود الجديدة في قطاع الخدمات اللوجستية.',
           'يجب فحص جميع الشحنات عند وصولها والإبلاغ عن أي تلف خلال يومي عمل.',
           'توصي اللجنة بتوسيع البرنامج التجريبي ليشمل ثلاثة مكاتب إقليمية إضافية.',
           'يجب على الموظفين إكمال التدريب الأمني السنوي قبل نهاية السنة المالية.',
           'ارتفعت نسبة رضا العملاء للربع الثالث على التوالي لتبلغ مستوى قياسيًا.'],
    'hi': ['इस तिमाही में लॉजिस्टिक्स क्षेत्र के नए अनुबंधों के कारण राजस्व में लगातार वृद्धि हुई।',
           'सभी शिपमेंट की जाँच पहुँचने पर की जानी चाहिए और किसी भी क्षति की सूचना दो कार्यदिवसों में दी जानी चाहिए।',
           'समिति पायलट कार्यक्रम को तीन और क्षेत्रीय कार्यालयों तक बढ़ाने की सिफारिश करती है।',
           'कर्मचारियों को वित्तीय वर्ष समाप्त होने से पहले वार्षिक सुरक्षा प्रशिक्षण पूरा करना होगा।'],
    'bn': ['লজিস্টিকস বিভাগের নতুন চুক্তির ফলে এই প্রান্তিকে আয় ধারাবাহিকভাবে বেড়েছে।',
           'সব চালান পৌঁছানোর পর পরীক্ষা করতে হবে এবং কোনো ক্ষতি দুই কর্মদিবসের মধ্যে জানাতে হবে।',
           'কমিটি পাইলট কর্মসূচিটি আরও তিনটি আঞ্চলিক অফিসে সম্প্রসারণের সুপারিশ করছে।',
           'কর্মীদের অর্থবছর শেষ হওয়ার আগে বার্ষিক নিরাপত্তা প্রশিক্ষণ সম্পন্ন করতে হবে।'],
}
OTHER_HEADINGS = {'ko': ['요약', '주요 결과', '재무 현황', '다음 단계'], 'ja': ['概要', '主な結果', '財務状況', '今後の予定'],
                  'zh': ['摘要', '主要发现', '财务概况', '后续步骤'], 'ar': ['ملخص', 'النتائج الرئيسية', 'الوضع المالي', 'الخطوات التالية'],
                  'hi': ['सारांश', 'मुख्य निष्कर्ष', 'वित्तीय स्थिति', 'अगले कदम'], 'bn': ['সারসংক্ষেপ', 'প্রধান ফলাফল', 'আর্থিক অবস্থা', 'পরবর্তী পদক্ষেপ']}
FONT_FILES = {'en': ('NotoSans-Regular.ttf', 'NotoSans-Bold.ttf'), 'serif': ('NotoSerif-Regular.ttf', 'NotoSerif-Bold.ttf'),
              'ko': ('SourceHanSansKR-Regular.ttf', 'SourceHanSansKR-Bold.ttf'), 'ja': ('SourceHanSansJP-Regular.ttf', 'SourceHanSansJP-Bold.ttf'),
              'zh': ('SourceHanSansCN-Regular.ttf', 'SourceHanSansCN-Bold.ttf'), 'ar': ('GoNotoKurrent-Regular.ttf', 'GoNotoKurrent-Bold.ttf'),
              'hi': ('GoNotoKurrent-Regular.ttf', 'GoNotoKurrent-Bold.ttf'), 'bn': ('GoNotoKurrent-Regular.ttf', 'GoNotoKurrent-Bold.ttf')}
PALETTES = [('#1f3a5f', '#e8eef6'), ('#5b2a86', '#f1eaf7'), ('#0f5132', '#e7f4ec'), ('#7a2e0e', '#fbefe8'), ('#333333', '#f2f2f2')]


class Values:
    """Random, realistic literal values: the checker finds them in the source and expects them back."""

    def __init__(self, rng):
        self.rng = rng

    def code(self, prefix=None):
        prefix = prefix or ''.join(self.rng.choice(string.ascii_uppercase) for _ in range(self.rng.choice([2, 3])))
        return f'{prefix}-{self.rng.randint(2019, 2027)}-{self.rng.randint(1, 99999):05d}'

    def email(self, person):
        first, last = person.lower().split()[0], person.lower().split()[-1]
        return f'{first}.{last}@{self.rng.choice(["example.com", "corp.example", "mail.example.org"])}'

    def phone(self):
        return self.rng.choice([f'+82 10 {self.rng.randint(1000, 9999)} {self.rng.randint(1000, 9999)}',
                                f'+1 415 555 {self.rng.randint(1000, 9999)}', f'+44 20 7946 {self.rng.randint(100, 999)}',
                                f'+49 30 {self.rng.randint(100000, 999999)}'])

    def amount(self):
        currency = self.rng.choice(['USD', 'EUR', 'KRW', 'JPY', 'GBP'])
        value = self.rng.randint(120, 950000) * (1000 if currency in ('KRW', 'JPY') else 1)
        return f'{currency} {value:,}' + ('' if currency in ('KRW', 'JPY') else f'.{self.rng.randint(0, 99):02d}')

    def date(self):
        y, m, d = self.rng.randint(2023, 2027), self.rng.randint(1, 12), self.rng.randint(1, 28)
        return self.rng.choice([f'{y}-{m:02d}-{d:02d}', f'{d:02d}/{m:02d}/{y}'])

    def url(self):
        return f'https://{self.rng.choice(["portal", "docs", "support"])}.example.com/{self.rng.randint(100, 999)}'


def esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ---------------------------------------------------------------- document bodies (HTML for pymupdf.Story)
def paragraphs(rng, lang, n, values=None):
    out = []
    for _ in range(n):
        if lang == 'en':
            parts = rng.sample(EN['sentences'], rng.randint(2, 4))
            if values and rng.random() < 0.6:
                parts.append(rng.choice([f'Reference {values.code()} applies.', f'Contact {values.email(rng.choice(EN["person"]))} for details.',
                                         f'The total was {values.amount()} on {values.date()}.', f'Call {values.phone()} for support.']))
            out.append(' '.join(parts))
        else:
            parts = rng.sample(OTHER[lang], min(len(OTHER[lang]), rng.randint(2, 3)))
            if values and rng.random() < 0.5:
                parts.append(f'{values.code()} · {values.date()}')
            out.append(' '.join(parts))
    return out


def heading(rng, lang):
    return rng.choice(EN['headings'] if lang == 'en' else OTHER_HEADINGS[lang])


def table(rng, values, rows, cols, lang='en'):
    head = ['Item', 'Code', 'Quantity', 'Unit price', 'Total', 'Date', 'Owner'][:cols]
    body = ''
    for _ in range(rows):
        cells = [rng.choice(EN['items']), values.code(), str(rng.randint(1, 500)), values.amount(), values.amount(),
                 values.date(), rng.choice(EN['person'])][:cols]
        body += '<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in cells) + '</tr>'
    return '<table><tr>' + ''.join(f'<th>{h}</th>' for h in head) + f'</tr>{body}</table>'


def body_html(kind, rng, lang, values):
    """HTML for one document of `kind` (several pages' worth for long kinds)."""
    company, person = rng.choice(EN['company']), rng.choice(EN['person'])
    p = lambda n: ''.join(f'<p>{esc(t)}</p>' for t in paragraphs(rng, lang, n, values))
    h = lambda level=2: f'<h{level}>{esc(heading(rng, lang))}</h{level}>'
    if kind == 'report':
        return (f'<h1>{esc(company)} — Quarterly Report</h1><p class="meta">Document ID: {values.code()} | '
                f'Prepared by {esc(person)} | {values.date()}</p>' + h() + p(2) + h() + '<ul>'
                + ''.join(f'<li>{esc(t)}</li>' for t in paragraphs(rng, lang, 3)) + '</ul>'
                + h() + table(rng, values, rng.randint(4, 9), rng.randint(4, 6)) + h() + p(rng.randint(2, 5)))
    if kind == 'invoice':
        return (f'<h1>INVOICE</h1><p class="meta">Invoice no. {values.code("INV")}<br/>Date: {values.date()}<br/>'
                f'Due: {values.date()}</p><p><b>Bill to:</b> {esc(rng.choice(EN["company"]))}, {rng.randint(1, 300)} Harbour Road, '
                f'{rng.choice(EN["city"])}<br/>Attn: {esc(person)} · {values.email(person)} · {values.phone()}</p>'
                + table(rng, values, rng.randint(3, 12), 5)
                + f'<p class="right"><b>Subtotal:</b> {values.amount()}<br/><b>VAT 10%:</b> {values.amount()}<br/>'
                f'<b>Total due:</b> {values.amount()}</p><p class="small">Bank: Granite Harbor Bank · IBAN '
                f'DE{rng.randint(10, 99)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)} '
                f'{rng.randint(1000, 9999)} {rng.randint(10, 99)} · Payment terms: 30 days.</p>')
    if kind == 'form':
        fields = [('Full name', person), ('Employee ID', values.code('EMP')), ('Email', values.email(person)),
                  ('Phone', values.phone()), ('Start date', values.date()), ('Department', rng.choice(['Finance', 'Operations', 'Research'])),
                  ('Office', rng.choice(EN['city'])), ('Manager', rng.choice(EN['person'])), ('Annual salary', values.amount())]
        rows = ''.join(f'<tr><td class="label">{a}</td><td>{esc(b)}</td></tr>' for a, b in fields)
        boxes = ''.join(f'<p>☐ {esc(t)}</p>' for t in ['I agree to the terms above.', 'Send me a copy by email.', 'Remote work requested.'])
        return (f'<h1>Employee Onboarding Form</h1><p class="meta">Form {values.code("HR")}</p><table class="form">{rows}</table>'
                + h() + p(1) + boxes + '<p>Signature: ______________________ Date: ____________</p>')
    if kind == 'contract':
        clauses = ''.join(f'<p><b>{i}.{j}</b> {esc(t)}</p>' for i in range(1, rng.randint(3, 6)) for j, t in
                          enumerate(paragraphs(rng, lang, rng.randint(1, 3)), 1))
        return (f'<h1>Service Agreement</h1><p class="meta">Agreement {values.code("SA")} dated {values.date()}</p>'
                f'<p>This agreement is made between {esc(company)} (the "Provider") and {esc(rng.choice(EN["company"]))} '
                f'(the "Customer").</p>' + clauses + f'<p>Signed for the Provider: {esc(person)}</p>')
    if kind == 'slides':
        return ''  # drawn page by page (draw_slides)
    if kind == 'newsletter':
        return f'<h1>{esc(company)} News</h1><p class="meta">Issue {rng.randint(1, 60)} · {values.date()}</p>' + ''.join(
            h(3) + p(rng.randint(1, 3)) for _ in range(rng.randint(3, 6)))
    if kind == 'manual':
        steps = ''.join(f'<li>{esc(t)}</li>' for t in paragraphs(rng, lang, rng.randint(3, 6)))
        return (f'<h1>Installation Guide — Model {values.code("MX")}</h1>' + h() + p(1) + f'<ol>{steps}</ol>'
                + '<div class="warn"><b>Warning:</b> Disconnect the power supply before opening the cover. '
                f'Operating temperature: 5 °C to 40 °C. Weight: {rng.randint(2, 40)} kg.</div>'
                + f'<pre>sudo setup --device /dev/ttyUSB0 --rate {rng.choice([9600, 115200])}</pre>'
                + table(rng, values, 4, 4))
    if kind == 'paper':
        return (f'<h1>{esc(heading(rng, "en"))}: A Study of {esc(rng.choice(EN["city"]))} Freight Data</h1>'
                f'<p class="meta">{esc(person)}, {esc(rng.choice(EN["person"]))} · {values.email(person)}</p>'
                + '<h2>Abstract</h2>' + p(1) + '<h2>1. Introduction</h2>' + p(2)
                + '<p>The model is E = mc² and the error term ε ≈ 0.03 for x₁ ≥ 2 [1], [2].</p>'
                + '<h2>2. Results</h2>' + table(rng, values, 5, 4) + p(2)
                + '<h2>References</h2><p class="small">[1] A. Author, Journal of Logistics, 12(3), 2021.<br/>'
                '[2] B. Writer, Freight Review, 7, 2019.</p>')
    if kind == 'timetable':
        return (f'<h1>Timetable {values.code("TT")}</h1>'
                + '<table class="dense">' + ''.join('<tr>' + ''.join(
                    f'<td>{rng.randint(5, 23):02d}:{rng.choice([0, 15, 30, 45]):02d}</td>' for _ in range(10)) + '</tr>'
                    for _ in range(rng.randint(15, 35))) + '</table><p class="small">'
                + esc(rng.choice(EN['sentences'])) + '</p>')
    if kind == 'letter':
        return (f'<p>{esc(company)}<br/>{rng.randint(1, 300)} Market Street<br/>{rng.choice(EN["city"])}</p>'
                f'<p class="right">{values.date()}</p><p>Dear {esc(person)},</p>' + p(rng.randint(2, 4))
                + f'<p>Your reference: {values.code()}</p><p>Yours sincerely,<br/>{esc(rng.choice(EN["person"]))}</p>')
    raise ValueError(kind)


def css(lang, family, palette, size, columns):
    dark, light = palette
    font = 'body' if lang == 'en' else lang
    direction = 'direction: rtl; text-align: right;' if lang == 'ar' else ''
    return f"""
@font-face {{font-family: body; src: url({FONT_FILES[family][0]});}}
@font-face {{font-family: body; src: url({FONT_FILES[family][1]}); font-weight: bold;}}
@font-face {{font-family: {lang}; src: url({FONT_FILES.get(lang, FONT_FILES['en'])[0]});}}
@font-face {{font-family: {lang}; src: url({FONT_FILES.get(lang, FONT_FILES['en'])[1]}); font-weight: bold;}}
* {{font-family: {font}, body; {direction}}}
body {{font-size: {size}pt; line-height: 1.35;}}
h1 {{font-size: {size * 1.9:.1f}pt; color: {dark}; margin-bottom: 4pt;}}
h2 {{font-size: {size * 1.35:.1f}pt; color: {dark}; margin-top: 10pt;}}
h3 {{font-size: {size * 1.15:.1f}pt; color: {dark};}}
.meta {{color: #666666; font-size: {size * 0.85:.1f}pt;}}
.small {{font-size: {size * 0.75:.1f}pt;}}
.right {{text-align: right;}}
table {{border-collapse: collapse; width: 100%; margin: 6pt 0;}}
th {{background-color: {dark}; color: white; padding: 3pt; font-size: {size * 0.9:.1f}pt; text-align: left;}}
td {{border: 0.5pt solid #999999; padding: 3pt; font-size: {size * 0.9:.1f}pt;}}
table.dense td {{font-size: {max(5.5, size * 0.65):.1f}pt; padding: 1pt;}}
table.form td.label {{background-color: {light}; font-weight: bold; width: 35%;}}
.warn {{background-color: #fff3cd; border: 1pt solid #c69500; padding: 5pt;}}
pre {{font-family: monospace; font-size: {size * 0.85:.1f}pt; background-color: #f4f4f4;}}
"""


def write_story(doc, html, user_css, archive, page_size, columns, margin):
    story = pymupdf.Story(html=f'<body>{html}</body>', user_css=user_css, archive=archive)
    w, h = page_size
    gap = 18
    col_w = (w - 2 * margin - gap * (columns - 1)) / columns
    rects = [pymupdf.Rect(margin + i * (col_w + gap), margin, margin + i * (col_w + gap) + col_w, h - margin) for i in range(columns)]
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    more, pages = True, 0
    while more and pages < 400:
        device = writer.begin_page(pymupdf.Rect(0, 0, w, h))
        for rect in rects:
            more, _ = story.place(rect)
            story.draw(device)
            if not more:
                break
        writer.end_page()
        pages += 1
    writer.close()
    with pymupdf.open(stream=buffer.getvalue(), filetype='pdf') as part:
        doc.insert_pdf(part)


def draw_slides(doc, rng, lang, values, fonts, palette, count):
    dark, light = palette
    regular = str(fonts / FONT_FILES['en' if lang == 'en' else lang][0])
    for index in range(count):
        page = doc.new_page(width=960, height=540)
        page.draw_rect(page.rect, color=None, fill=pymupdf.utils.getColor('white') if index else (0.12, 0.23, 0.37))
        page.insert_font(fontname='f0', fontfile=regular)
        title_color = (1, 1, 1) if index == 0 else (0.12, 0.23, 0.37)
        title = heading(rng, lang) if index else f'{rng.choice(EN["company"])} {rng.randint(2025, 2027)}'
        page.insert_textbox(pymupdf.Rect(60, 50, 900, 140), title, fontname='f0', fontsize=34, color=title_color)
        if index == 0:
            page.insert_textbox(pymupdf.Rect(60, 160, 900, 260), paragraphs(rng, lang, 1)[0][:120], fontname='f0', fontsize=18, color=(1, 1, 1))
            continue
        y = 160
        for text in paragraphs(rng, lang, rng.randint(2, 4), values):
            page.draw_circle((72, y + 10), 4, color=None, fill=(0.85, 0.35, 0.1))
            used = page.insert_textbox(pymupdf.Rect(90, y, 560, y + 90), text[:150], fontname='f0', fontsize=16, color=(0.15, 0.15, 0.15))
            y += 90
        # A simple bar chart drawn with vectors and labelled with text.
        base = 470
        for i in range(4):
            height = rng.randint(40, 220)
            page.draw_rect(pymupdf.Rect(620 + i * 70, base - height, 670 + i * 70, base), color=None, fill=(0.2, 0.4, 0.7))
            page.insert_text((620 + i * 70, base + 18), f'Q{i + 1}', fontname='f0', fontsize=12)
            page.insert_text((620 + i * 70, base - height - 6), f'{height}%', fontname='f0', fontsize=11)


def picture_with_text(rng, values, fonts):
    """A PNG of a label card with text baked into the pixels (text in images)."""
    tmp = pymupdf.open()
    page = tmp.new_page(width=360, height=150)
    page.draw_rect(page.rect, color=None, fill=(0.95, 0.93, 0.85))
    page.insert_font(fontname='f0', fontfile=str(fonts / FONT_FILES['en'][1]))
    page.insert_text((18, 50), rng.choice(['Fragile — handle with care', 'Price', 'Inspection passed', 'Store below 25 °C']), fontname='f0', fontsize=20)
    page.insert_text((18, 95), values.code(), fontname='f0', fontsize=24, color=(0.6, 0.1, 0.1))
    page.insert_text((18, 130), values.amount(), fontname='f0', fontsize=16)
    return page.get_pixmap(dpi=144).tobytes('png')


def scanned_copy(src, rng, dpi):
    """Rasterise every page (no text layer), with skew, noise, uneven light and JPEG, like a scanner."""
    from PIL import Image, ImageFilter
    import numpy as np
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if rng.random() < 0.6 else pymupdf.csRGB)
        image = Image.frombytes('L' if pix.n == 1 else 'RGB', (pix.width, pix.height), pix.samples)
        image = image.rotate(rng.uniform(-1.6, 1.6), expand=False, fillcolor=255 if pix.n == 1 else (255, 255, 255),
                             resample=Image.BICUBIC)
        array = np.asarray(image).astype('float32')
        gradient = np.linspace(rng.uniform(0.88, 1.0), 1.0, array.shape[1])
        array = array * (gradient if array.ndim == 2 else gradient[:, None]) + np.random.default_rng(rng.randint(0, 9999)).normal(0, rng.uniform(2, 9), array.shape)
        image = Image.fromarray(np.clip(array, 0, 255).astype('uint8'))
        if rng.random() < 0.3:
            image = image.filter(ImageFilter.GaussianBlur(0.6))
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=rng.randint(55, 85))
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=buffer.getvalue())
    return out


def make_document(index, rng, fonts, sources, scanned_share):
    values = Values(rng)
    kind = KINDS[index % len(KINDS)]
    lang = 'en' if rng.random() < 0.7 else rng.choice([s for s in sources if s != 'en'] or ['en'])
    size_name = rng.choice(list(SIZES)) if kind != 'slides' else '16:9'
    page_size = SIZES.get(size_name, (960, 540))
    landscape = kind != 'slides' and rng.random() < 0.15
    if landscape:
        page_size = (page_size[1], page_size[0])
    columns = 2 if kind == 'newsletter' or (kind in ('paper', 'report') and rng.random() < 0.3) else 1
    family = rng.choice(['en', 'serif'])
    palette = rng.choice(PALETTES)
    font_size = rng.choice([8, 9, 10, 10.5, 11, 12])
    doc = pymupdf.open()
    features = {'kind': kind, 'source_language': lang, 'page_size': size_name, 'landscape': landscape, 'columns': columns,
                'font_size': font_size}
    if kind == 'slides':
        draw_slides(doc, rng, lang, values, fonts, palette, rng.randint(3, 6))
    else:
        archive = pymupdf.Archive(str(fonts))
        html = body_html(kind, rng, lang, values)
        if kind in ('report', 'newsletter', 'manual') and rng.random() < 0.5:
            html += ''.join(f'<h2>{esc(heading(rng, lang))}</h2>' + ''.join(f'<p>{esc(t)}</p>' for t in paragraphs(rng, lang, 4, values))
                            for _ in range(rng.randint(1, 4)))
        write_story(doc, html, css(lang, family, palette, font_size, columns), archive, page_size, columns,
                    margin=rng.choice([36, 54, 72]))
    if rng.random() < 0.35:  # text inside a picture
        page = doc[rng.randrange(len(doc))]
        width = min(220, page.rect.width / 2.5)
        page.insert_image(pymupdf.Rect(page.rect.width - width - 30, page.rect.height - width * 0.42 - 40,
                                       page.rect.width - 30, page.rect.height - 40), stream=picture_with_text(rng, values, fonts))
        features['text_in_picture'] = True
    if rng.random() < 0.15 and len(doc) > 1:  # a rotated page
        doc[rng.randrange(len(doc))].set_rotation(rng.choice([90, 180, 270]))
        features['rotated_page'] = True
    if rng.random() < 0.15:  # annotations and a form field
        page = doc[0]
        page.add_highlight_annot(pymupdf.Rect(40, 40, 200, 60))
        page.add_text_annot((20, 20), 'Reviewer note: check the totals.')
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = 'approver'
        widget.field_value = rng.choice(EN['person'])
        widget.rect = pymupdf.Rect(40, page.rect.height - 60, 240, page.rect.height - 40)
        page.add_widget(widget)
        features['annotations'] = True
    if rng.random() < 0.08:  # a blank page in the middle
        doc.new_page(pno=rng.randrange(len(doc) + 1), width=page_size[0], height=page_size[1])
        features['blank_page'] = True
    if rng.random() < scanned_share and lang in ('en', 'ko', 'ja', 'zh'):
        scanned = scanned_copy(doc, rng, rng.choice([150, 200, 300]))
        doc.close()
        doc = scanned
        features['scanned'] = True
    return doc, features


def edge_cases(out, rng, fonts):
    """Files that must be refused cleanly or handled: encrypted, damaged, image-only, tiny page."""
    made = []
    base = pymupdf.open()
    page = base.new_page()
    page.insert_text((72, 100), 'Confidential memo. Reference SEC-2026-00417. Call +1 415 555 0142.', fontsize=12)
    data = base.tobytes()
    (out / 'edge-encrypted.pdf').write_bytes(base.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='owner'))
    made.append(('edge-encrypted.pdf', {'edge': 'encrypted', 'expect': 'refused'}))
    (out / 'edge-damaged.pdf').write_bytes(data[: len(data) // 2])
    made.append(('edge-damaged.pdf', {'edge': 'truncated file', 'expect': 'refused or repaired'}))
    tiny = pymupdf.open()
    tiny.new_page(width=200, height=120).insert_text((10, 60), 'Ticket TK-2026-55102, gate 14', fontsize=9)
    tiny.save(out / 'edge-tiny-page.pdf')
    made.append(('edge-tiny-page.pdf', {'edge': 'very small page'}))
    blank = pymupdf.open()
    blank.new_page()
    blank.save(out / 'edge-blank.pdf')
    made.append(('edge-blank.pdf', {'edge': 'blank page, no text'}))
    image_only = pymupdf.open()
    image_only.new_page(width=420, height=300).insert_image(pymupdf.Rect(30, 30, 390, 180), stream=picture_with_text(rng, Values(rng), fonts))
    image_only.save(out / 'edge-picture-only.pdf')
    made.append(('edge-picture-only.pdf', {'edge': 'only a picture with text'}))
    return made


def long_document(out, rng, fonts, pages, sources, scanned_pages):
    """A long document made of many generated documents, with some scanned pages mixed in."""
    doc = pymupdf.open()
    index = 0
    while len(doc) < pages:
        part, _ = make_document(index, rng, fonts, sources, scanned_share=scanned_pages)
        doc.insert_pdf(part, to_page=min(len(part), pages - len(doc)) - 1)
        part.close()
        index += 1
    name = f'long-{pages}-pages.pdf'
    doc.save(out / name, garbage=3, deflate=True)
    return name, {'edge': f'{pages} pages', 'pages': pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out')
    parser.add_argument('--set', choices=['dev', 'heldout'], default='dev')
    parser.add_argument('--count', type=int, default=40)
    parser.add_argument('--fonts', default=str(Path.home() / '.cache/babeldoc/fonts'))
    parser.add_argument('--sources', default='en,ko,ja,zh,ar,hi,bn')
    parser.add_argument('--scanned', type=float, default=0.25, help='share of documents turned into scans')
    parser.add_argument('--long', type=int, default=0, help='also write one document with this many pages')
    parser.add_argument('--edge', action='store_true', help='also write the edge-case files')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fonts = Path(args.fonts)
    sources = args.sources.split(',')
    seed = 1000 if args.set == 'dev' else 900000
    manifest = {}
    for index in range(args.count):
        rng = random.Random(seed + index)
        doc, features = make_document(index, rng, fonts, sources, args.scanned)
        name = f'{args.set}-{index:03d}-{features["kind"]}-{features["source_language"]}{"-scan" if features.get("scanned") else ""}.pdf'
        doc.save(out / name, garbage=3, deflate=True)
        features['pages'] = len(doc)
        doc.close()
        manifest[name] = features
    rng = random.Random(seed - 1)
    if args.edge:
        manifest.update(dict(edge_cases(out, rng, fonts)))
    if args.long:
        name, features = long_document(out, rng, fonts, args.long, ['en'], 0.1)
        manifest[name] = features
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'{len(manifest)} files in {out}')


if __name__ == '__main__':
    main()
