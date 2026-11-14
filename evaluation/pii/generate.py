"""Generate the synthetic PII evaluation set with an answer key built from construction.

All people, numbers and addresses are fictitious (".example" domains, 555 phone ranges,
generated IDs). The answer key lists, per document:
  expected  - [category, value, where] that must be masked ("text" layer or "image" pixels)
  optional  - values whose masking is neither required nor penalised (ambiguous by policy)
  negatives - values that must stay readable (products, prices, dates, organisations)
Categories follow transformation/validation/privacy.py CATEGORY_DEFINITIONS.

This set is for measurement only: never tune detection against it.

usage: python -m evaluation.pii.generate <output_dir> [--seed 20261112]
"""
import argparse, io, json, random, re, string
from pathlib import Path

import pymupdf

W, H = 612, 792
NAMES = {
    'en': (['Olivia', 'Marcus', 'Priya', 'Daniel', 'Grace', 'Ethan', 'Fatima', 'Liam', 'Hannah', 'Jamal', 'Chloe', 'Victor',
            'Nadia', 'Owen', 'Sofia', 'Ravi', 'Amara', 'Thomas', 'Mei', 'Isaac', 'Bronwyn', 'Kwame', 'Yusuf', 'Ingrid'],
           ['Whitfield', 'Okonkwo', 'Ramanathan', 'Kowalski', 'Delgado', 'Ashworth', 'Nakamura', 'Brennan', 'Haddad', 'Lindqvist',
            'Mbeki', 'Castellano', 'Fairbanks', 'Osei', 'Petrova', 'Gallagher', 'Iyer', 'Sutherland', 'Achebe', 'Moreau',
            'Abernathy', 'Thorvaldsen', 'Quintero', 'Szymanski']),
    'de': (['Lukas', 'Johanna', 'Felix', 'Annika', 'Tobias', 'Miriam', 'Jannik', 'Leonie', 'Matthias', 'Svenja'],
           ['Brandt', 'Vogel', 'Hoffmann', 'Krüger', 'Lehmann', 'Seidel', 'Wagenknecht', 'Pohl', 'Ziegler', 'Baumgartner']),
    'fr': (['Camille', 'Julien', 'Élodie', 'Mathis', 'Inès', 'Bastien', 'Margaux', 'Thibault', 'Anaïs', 'Rémi'],
           ['Lefèvre', 'Girard', 'Rousseau', 'Fontaine', 'Chevalier', 'Marchand', 'Delacroix', 'Benoît', 'Morel', 'Faure']),
    'es': (['Lucía', 'Mateo', 'Valentina', 'Diego', 'Carmen', 'Alejandro', 'Ximena', 'Rodrigo', 'Paloma', 'Iker'],
           ['Herrera', 'Navarro', 'Iglesias', 'Ortega', 'Castillo', 'Romero', 'Echeverría', 'Saavedra', 'Villalobos', 'Cuesta']),
    'ko': (['민준', '서연', '지호', '수아', '도윤', '하은', '예준', '지유', '시우', '채원'],
           ['김', '이', '박', '최', '정', '강', '조', '윤', '장', '임']),
}
ORGS = ['Harborline Logistics', 'Quillmark Analytics', 'Bluefern Health', 'Stonebridge Mutual', 'Larkspur Systems',
        'Kestrel Freight', 'Orchardview Clinic', 'Meridian Ledger Bank', 'Tidewater Supplies', 'Cobaltine Software']
DOMAIN = {'en': 'example', 'de': 'example', 'fr': 'example', 'es': 'example', 'ko': 'example'}
STREETS = {
    'en': [('{n} Juniper Lane', 'Apt {u}', 'Springfield, IL 62704'), ('{n} Copperfield Road', 'Suite {u}', 'Bristol BS8 4QT'),
           ('{n} Harrow Avenue', 'Unit {u}', 'Austin, TX 78704'), ('{n} Marlowe Street', 'Flat {u}', 'Manchester M14 5RX')],
    'de': [('Lindenstraße {n}', '', '{p} Berlin'), ('Am Mühlbach {n}', '', '{p} Freiburg'), ('Kastanienweg {n}', '', '{p} Leipzig')],
    'fr': [('{n} rue des Tilleuls', '', '{p} Lyon'), ('{n} avenue Gambetta', '', '{p} Bordeaux'), ('{n} rue du Moulin', '', '{p} Nantes')],
    'es': [('Calle de Alcalá {n}', '{u}º B', '{p} Madrid'), ('Avenida del Puerto {n}', '{u}º A', '{p} Valencia')],
    'ko': [('서울특별시 마포구 월드컵북로 {n}', '{u}호', ''), ('부산광역시 해운대구 센텀중앙로 {n}', '{u}호', '')],
}
FONT = {'ko': 'korea'}


class Doc:
    """A document under construction plus its answer key."""
    def __init__(self, rng, name, kind, lang):
        self.rng, self.name, self.kind, self.lang = rng, name, kind, lang
        self.pdf = pymupdf.open(); self.page = None; self.y = 0
        self.expected, self.optional, self.negatives = [], [], []
        self.font = FONT.get(lang, 'helv'); self.bold = 'korea' if lang == 'ko' else 'hebo'

    # answer key -----------------------------------------------------------
    def pii(self, category, value, where='text'):
        self.expected.append([category, value, where]); return value

    def neg(self, value): self.negatives.append(value); return value
    def opt(self, value): self.optional.append(value); return value

    # layout ---------------------------------------------------------------
    def new_page(self, top=60):
        self.page = self.pdf.new_page(width=W, height=H); self.y = top; return self.page

    def text(self, s, size=10, x=50, bold=False, gap=None):
        self.page.insert_text((x, self.y), s, fontsize=size, fontname=self.bold if bold else self.font)
        self.y += gap or size * 1.6

    def para(self, s, size=10, x=50, width=95):
        """Wrap at word boundaries by character count, so names may break across lines like real prose."""
        if self.lang == 'ko': width = int(width * .55)
        line = ''
        for word in s.split(' '):
            if line and len(line) + 1 + len(word) > width:
                self.text(line, size, x); line = word
            else:
                line = (line + ' ' + word).strip()
        if line: self.text(line, size, x)
        self.y += size * .5

    def table(self, widths, rows, h=19, size=8.5, x=50):
        for r, row in enumerate(rows):
            cx = x
            for w, cell in zip(widths, row):
                rect = pymupdf.Rect(cx, self.y + r * h, cx + w, self.y + (r + 1) * h)
                self.page.draw_rect(rect, color=(.35, .35, .35), fill=(.86, .89, .95) if r == 0 else None, width=.6)
                self.page.insert_text((cx + 4, self.y + r * h + 13), cell, fontsize=size,
                                      fontname=self.bold if r == 0 else self.font)
                cx += w
        self.y += len(rows) * h + 18

    def kv(self, pairs, size=10, x=50, sep=': '):
        for label, value in pairs: self.text(f'{label}{sep}{value}', size, x)
        self.y += 6

    def check(self):
        """Every expected/negative value must be present verbatim in the constructed text."""
        text = re.sub(r'\s+', '', ' '.join(p.get_text() for p in self.pdf)).casefold()
        for c, v, where in self.expected:
            if where == 'text' and re.sub(r'\s+', '', v).casefold() not in text:
                raise AssertionError(f'{self.name}: expected value not on page: {v!r}')
        for v in self.negatives:
            if re.sub(r'\s+', '', v).casefold() not in text:
                raise AssertionError(f'{self.name}: negative value not on page: {v!r}')


# fictitious data ---------------------------------------------------------
def person(rng, lang):
    first, last = NAMES[lang]
    return (rng.choice(last) + rng.choice(first)) if lang == 'ko' else f'{rng.choice(first)} {rng.choice(last)}'


def ascii_slug(s):
    import unicodedata
    plain = unicodedata.normalize('NFKD', s.lower().replace('ß', 'ss')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z]', '', plain)


def email(rng, name, org=None):
    parts = name.split()
    if len(parts) < 2: parts = ['user', str(rng.randint(10, 99))]
    local = rng.choice(['{f}.{l}', '{f0}{l}', '{f}_{l}', '{l}.{f0}']).format(
        f=ascii_slug(parts[0]), l=ascii_slug(parts[-1]), f0=ascii_slug(parts[0])[:1])
    host = ascii_slug(org or rng.choice(['mailbox', 'postbox', 'inboxly', 'netmail']))[:14]
    return f'{local}@{host}.example'


def phone(rng, lang):
    d = lambda n: ''.join(rng.choice(string.digits) for _ in range(n))
    return {'en': rng.choice([f'+1 {rng.randint(201, 989)} 555 {d(4)}', f'({rng.randint(201, 989)}) 555-{d(4)}',
                              f'+44 20 7946 {d(4)}']),
            'de': f'+49 30 5550 {d(4)}', 'fr': f'+33 1 55 50 {d(2)} {d(2)}', 'es': f'+34 912 555 {d(3)}',
            'ko': f'010-5550-{d(4)}'}[lang]


def address(rng, lang):
    street, unit, city = rng.choice(STREETS[lang])
    vals = {'n': rng.randint(2, 480), 'u': rng.randint(1, 30), 'p': rng.randint(10115, 99998)}
    return ', '.join(p.format(**vals) for p in (street, unit, city) if p)


def ident(rng, letters=None, digits=None, sep='-'):
    letters = letters if letters is not None else rng.randint(1, 3)
    digits = digits or rng.randint(5, 8)
    head = ''.join(rng.choice(string.ascii_uppercase) for _ in range(letters))
    body = ''.join(rng.choice(string.digits) for _ in range(digits))
    if rng.random() < .4: body = body[:len(body) // 2] + sep + body[len(body) // 2:]
    return f'{head}{sep}{body}' if head else body


def iban(rng, country='DE'):
    lengths = {'DE': 18, 'FR': 23, 'ES': 20, 'GB': 18, 'NL': 14}
    if country == 'GB': bban = 'NWBK' + ''.join(rng.choice(string.digits) for _ in range(14))
    elif country == 'NL': bban = 'ABNA' + ''.join(rng.choice(string.digits) for _ in range(10))
    else: bban = ''.join(rng.choice(string.digits) for _ in range(lengths[country]))
    numeric = ''.join(str(int(c, 36)) for c in bban + country + '00')
    raw = f'{country}{98 - int(numeric) % 97:02d}{bban}'
    return ' '.join(raw[i:i + 4] for i in range(0, len(raw), 4))


def product(rng):
    return rng.choice(['SKU-', 'PRD-', 'MDL-', 'ART-', '']) + ident(rng, rng.randint(1, 3), rng.randint(3, 5))


def money(rng, cur='USD'):
    return f'{cur} {rng.randint(3, 2400)}.{rng.choice(["00", "50", "95", "99"])}'


def date(rng):
    return f'2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}'


def secret(rng):
    return rng.choice(['sk_live_', 'tok_', 'ghp_', 'AKIA']) + ''.join(rng.choice(string.ascii_letters + string.digits) for _ in range(20))


L = {  # labels per language
    'en': dict(name='Full name', cust='Customer number', inv='Invoice number', date='Date', addr='Address', email='Email',
               phone='Phone', emp='Employee ID', dept='Department', title='Job title', mgr='Line manager', dob='Date of birth',
               nid='National ID', bank='Bank account (IBAN)', start='Start date', grade='Salary band', patient='Patient name',
               mrn='Medical record no.', policy='Policy number', insurer='Insurer', ec='Emergency contact', doctor='Attending physician',
               holder='Account holder', acct='Account number', period='Statement period', amount='Amount', desc='Description',
               qty='Qty', price='Unit price', total='Total due', code='Product code', present='Present', actions='Action items',
               owner='Owner', due='Due', ref='Our reference', member='Member number', ext='Extension', shift='Shift'),
    'de': dict(name='Name', cust='Kundennummer', inv='Rechnungsnummer', date='Datum', addr='Anschrift', email='E-Mail',
               phone='Telefon', emp='Personalnummer', dept='Abteilung', title='Position', mgr='Vorgesetzte(r)', dob='Geburtsdatum',
               nid='Steuer-ID', bank='Bankverbindung (IBAN)', start='Eintrittsdatum', grade='Gehaltsstufe', patient='Patient',
               mrn='Patientennummer', policy='Versicherungsnummer', insurer='Krankenkasse', ec='Notfallkontakt', doctor='Behandelnder Arzt',
               holder='Kontoinhaber', acct='Kontonummer', period='Zeitraum', amount='Betrag', desc='Beschreibung', qty='Menge',
               price='Einzelpreis', total='Gesamtbetrag', code='Artikelnummer', present='Anwesend', actions='Aufgaben',
               owner='Verantwortlich', due='Fällig', ref='Unser Zeichen', member='Mitgliedsnummer', ext='Durchwahl', shift='Schicht'),
    'fr': dict(name='Nom complet', cust='Numéro client', inv='Numéro de facture', date='Date', addr='Adresse', email='Courriel',
               phone='Téléphone', emp='Matricule', dept='Service', title='Poste', mgr='Responsable', dob='Date de naissance',
               nid='Numéro de sécurité sociale', bank='Coordonnées bancaires (IBAN)', start="Date d'entrée", grade='Échelon',
               patient='Patient', mrn='Numéro de dossier', policy='Numéro de contrat', insurer='Mutuelle', ec="Contact d'urgence",
               doctor='Médecin traitant', holder='Titulaire du compte', acct='Numéro de compte', period='Période', amount='Montant',
               desc='Désignation', qty='Qté', price='Prix unitaire', total='Total à payer', code='Référence produit',
               present='Présents', actions='Actions', owner='Responsable', due='Échéance', ref='Notre référence',
               member="Numéro d'adhérent", ext='Poste tél.', shift='Équipe'),
    'es': dict(name='Nombre completo', cust='Número de cliente', inv='Número de factura', date='Fecha', addr='Dirección',
               email='Correo electrónico', phone='Teléfono', emp='Número de empleado', dept='Departamento', title='Cargo',
               mgr='Responsable directo', dob='Fecha de nacimiento', nid='DNI', bank='Cuenta bancaria (IBAN)', start='Fecha de alta',
               grade='Nivel salarial', patient='Paciente', mrn='Número de historia clínica', policy='Número de póliza',
               insurer='Aseguradora', ec='Contacto de emergencia', doctor='Médico responsable', holder='Titular de la cuenta',
               acct='Número de cuenta', period='Periodo', amount='Importe', desc='Descripción', qty='Cant.', price='Precio unitario',
               total='Total a pagar', code='Código de producto', present='Asistentes', actions='Acciones', owner='Responsable',
               due='Fecha límite', ref='Nuestra referencia', member='Número de socio', ext='Extensión', shift='Turno'),
    'ko': dict(name='성명', cust='고객 번호', inv='청구서 번호', date='날짜', addr='주소', email='이메일', phone='연락처',
               emp='사번', dept='부서', title='직위', mgr='담당 관리자', dob='생년월일', nid='주민등록번호', bank='급여 계좌',
               start='입사일', grade='급여 등급', patient='환자명', mrn='진료 번호', policy='보험 증권 번호', insurer='보험사',
               ec='비상 연락처', doctor='담당 의사', holder='예금주', acct='계좌번호', period='조회 기간', amount='금액',
               desc='내역', qty='수량', price='단가', total='합계', code='상품 코드', present='참석자', actions='조치 사항',
               owner='담당자', due='기한', ref='문서 번호', member='회원 번호', ext='내선', shift='근무조'),
}
COUNTRY = {'en': 'GB', 'de': 'DE', 'fr': 'FR', 'es': 'ES', 'ko': 'NL'}
CUR = {'en': 'USD', 'de': 'EUR', 'fr': 'EUR', 'es': 'EUR', 'ko': 'KRW'}


# templates -------------------------------------------------------------
def invoice(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    org = d.neg(r.choice(ORGS)); d.new_page(); d.text(org.upper(), 17, bold=True); d.text('Invoice' if lang == 'en' else l['inv'], 12)
    who = person(r, lang)
    d.kv([(l['inv'], d.pii('Identifiers', ident(r))), (l['date'], d.neg(date(r))), (l['cust'], d.pii('Identifiers', ident(r))),
          (l['name'], d.pii('Names', who)), (l['addr'], d.pii('Addresses', address(r, lang))),
          (l['email'], d.pii('Email addresses', email(r, who))), (l['phone'], d.pii('Phone numbers', phone(r, lang)))])
    rows = [[l['code'], l['desc'], l['qty'], l['price'], l['amount']]]
    for item in r.sample(['Steel shelving unit', 'Cable organiser kit', 'Label printer', 'Desk lamp, LED', 'Packing tape (36 rolls)',
                          'Ergonomic chair', 'USB-C dock'], 3):
        rows.append([d.neg(product(r)), d.neg(item), str(r.randint(1, 12)), d.neg(money(r, CUR[lang])), money(r, CUR[lang])])
    d.table([100, 200, 40, 90, 90], rows)
    d.text(f"{l['total']}: {d.neg(money(r, CUR[lang]))}", 10)
    d.text(f"{l['bank']}: {d.pii('Bank accounts', iban(r, COUNTRY[lang]))}", 10)
    support = d.opt(f'billing@{ascii_slug(org)[:12]}.example')
    d.text(f'{support}', 9)


def hr_form(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    d.new_page(); d.text({'en': 'Employee Personal Data Sheet', 'de': 'Personalstammblatt', 'fr': 'Fiche de renseignements',
                          'es': 'Ficha de datos del empleado', 'ko': '인사 기록 카드'}[lang], 16, bold=True)
    who, boss = person(r, lang), person(r, lang)
    nid = ident(r, 0, 9) if lang != 'ko' else f'{r.randint(700101, 991231)}-{r.randint(1000000, 2999999)}'
    rows = [[l['name'], d.pii('Names', who)], [l['emp'], d.pii('Identifiers', ident(r))], [l['dob'], d.opt(date(r).replace('2025', str(r.randint(1961, 2001))))],
            [l['addr'], d.pii('Addresses', address(r, lang))], [l['email'], d.pii('Email addresses', email(r, who))],
            [l['phone'], d.pii('Phone numbers', phone(r, lang))], [l['nid'], d.pii('Identifiers', nid)],
            [l['bank'], d.pii('Bank accounts', iban(r, COUNTRY[lang]))],
            [l['dept'], d.neg(r.choice(['Finance', 'Logistics', 'Customer Care', 'Research', 'Procurement']))],
            [l['title'], d.neg(r.choice(['Senior Analyst', 'Warehouse Lead', 'Account Manager', 'Lab Technician']))],
            [l['start'], d.neg(date(r))], [l['grade'], d.neg(f'B{r.randint(2, 7)}')], [l['mgr'], d.pii('Names', boss)]]
    d.table([170, 330], rows, h=22, size=9.5)
    d.para({'en': f'Changes to these details must be confirmed in writing by {boss}.',
            'de': f'Änderungen bestätigt {boss} schriftlich.', 'fr': f'Toute modification doit être validée par {boss}.',
            'es': f'Cualquier cambio debe ser confirmado por {boss}.', 'ko': f'변경 사항은 {boss}에게 서면으로 확인받으십시오.'}[lang], 9)


def letter(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    org = d.neg(r.choice(ORGS)); who, officer = person(r, lang), person(r, lang)
    d.new_page(70); d.text(org, 14, bold=True); d.text(d.opt(address(r, lang)), 9); d.y += 20
    d.text(who, 10); d.pii('Names', who)
    # A postal block: each printed line of the recipient's address is expected separately.
    street, unit, city = r.choice(STREETS[lang]); vals = {'n': r.randint(2, 480), 'u': r.randint(1, 30), 'p': r.randint(10115, 99998)}
    for part in (street, unit, city):
        if part: d.text(d.pii('Addresses', part.format(**vals)), 10)
    d.y += 14; ref = d.pii('Identifiers', ident(r)); d.text(f"{l['ref']}: {ref}    {l['date']}: {d.neg(date(r))}", 10); d.y += 10
    last = who.split()[-1] if lang != 'ko' else who
    tel, mail, fee = d.pii('Phone numbers', phone(r, lang)), d.pii('Email addresses', email(r, officer, org)), d.neg(money(r, CUR[lang]))
    body = {'en': f'Dear {last}, thank you for your enquiry about your account. Your case has been assigned to {officer}, who can be '
                  f'reached on {tel} or at {mail}. '
                  f'Please quote reference {ref} in any correspondence. The annual fee of {fee} remains unchanged.',
            'de': f'Sehr geehrte(r) {last}, Ihr Anliegen wurde {officer} zugewiesen, erreichbar unter {tel} '
                  f'oder {mail}. Bitte geben Sie das Zeichen {ref} an. Die Jahresgebühr von '
                  f'{fee} bleibt unverändert.',
            'fr': f'Madame, Monsieur {last}, votre dossier a été confié à {officer}, joignable au {tel} '
                  f'ou à {mail}. Merci de rappeler la référence {ref}. La cotisation annuelle de '
                  f'{fee} reste inchangée.',
            'es': f'Estimado/a {last}: su expediente ha sido asignado a {officer}, que puede ser contactado en el '
                  f'{tel} o en {mail}. Indique la referencia '
                  f'{ref}. La cuota anual de {fee} no cambia.'}[lang]
    d.pii('Names', officer); d.para(body, 10.5, width=88); d.y += 20
    d.text({'en': 'Kind regards,', 'de': 'Mit freundlichen Grüßen', 'fr': 'Cordialement,', 'es': 'Atentamente,'}[lang], 10)
    d.y += 18; signer = d.pii('Names', person(r, lang)); d.text(signer, 10)
    d.text(d.neg(r.choice(['Customer Relations Manager', 'Head of Member Services', 'Claims Supervisor'])), 9)


def minutes(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    people = [person(r, lang) for _ in range(5)]
    for p in people: d.pii('Names', p)
    d.new_page(); d.text({'en': 'Steering Committee - Minutes', 'de': 'Lenkungsausschuss - Protokoll', 'fr': 'Comité de pilotage - Compte rendu',
                          'es': 'Comité de dirección - Acta', 'ko': '운영 위원회 회의록'}[lang], 15, bold=True)
    d.text(f"{l['date']}: {d.neg(date(r))}", 10)
    d.para(f"{l['present']}: " + ', '.join(people[:4]), 10)
    budget = d.neg(f"{CUR[lang]} {r.randint(2, 9)}{r.choice(['.2', '.5', '.8'])} million" if lang != 'ko' else f'{r.randint(3, 9)}억 원')
    text = {'en': f'{people[0]} opened the meeting and reviewed the budget of {budget}. {people[1]} reported that the vendor shortlist is '
                  f'final; {people[2]} raised concerns about the delivery timeline and asked {people[3]} to circulate the revised plan. '
                  f'{people[4]} will be invited to the next session as an external adviser.',
            'de': f'{people[0]} eröffnete die Sitzung und erläuterte das Budget von {budget}. {people[1]} berichtete über die Auswahl der '
                  f'Lieferanten; {people[2]} äußerte Bedenken zum Zeitplan und bat {people[3]}, den überarbeiteten Plan zu verteilen. '
                  f'{people[4]} wird zur nächsten Sitzung eingeladen.',
            'fr': f'{people[0]} a ouvert la séance et présenté le budget de {budget}. {people[1]} a confirmé la liste des fournisseurs ; '
                  f'{people[2]} a exprimé des réserves sur le calendrier et a demandé à {people[3]} de diffuser le plan révisé. '
                  f'{people[4]} sera invité à la prochaine réunion.',
            'es': f'{people[0]} abrió la sesión y presentó el presupuesto de {budget}. {people[1]} confirmó la lista de proveedores; '
                  f'{people[2]} expresó dudas sobre el calendario y pidió a {people[3]} que distribuyera el plan revisado. '
                  f'{people[4]} será invitado a la próxima sesión.',
            'ko': f'{people[0]} 위원장이 회의를 개회하고 예산 {budget}을 검토하였다. {people[1]} 위원은 공급업체 선정 결과를 보고하였으며, '
                  f'{people[2]} 위원은 일정에 대한 우려를 제기하고 {people[3]} 위원에게 수정 계획 배포를 요청하였다. '
                  f'{people[4]} 자문위원은 다음 회의에 초청될 예정이다.'}[lang]
    d.para(text, 10.5, width=90)
    d.new_page(); d.text(l['actions'], 13, bold=True)
    rows = [['#', l['desc'], l['owner'], l['due']]]
    tasks = {'en': ['Circulate revised plan', 'Confirm vendor contract', 'Book venue for workshop'],
             'de': ['Plan verteilen', 'Vertrag bestätigen', 'Workshop organisieren'],
             'fr': ['Diffuser le plan', 'Valider le contrat', 'Réserver la salle'],
             'es': ['Distribuir el plan', 'Confirmar el contrato', 'Reservar la sala'],
             'ko': ['수정 계획 배포', '계약 확정', '워크숍 장소 예약']}[lang]
    for i, task in enumerate(tasks):
        rows.append([str(i + 1), d.neg(task), people[i + 1], d.neg(date(r))])
    d.table([30, 230, 150, 90], rows)
    d.para({'en': f'Minutes taken by {people[0]}.', 'de': f'Protokoll: {people[0]}.', 'fr': f'Rédigé par {people[0]}.',
            'es': f'Acta redactada por {people[0]}.', 'ko': f'작성자: {people[0]}'}[lang], 9)


def bank_statement(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    bank = d.neg(r.choice(['Meridian Ledger Bank', 'Stonebridge Mutual', 'Northgate Savings']))
    who, payee = person(r, lang), person(r, lang)
    d.new_page(); d.text(bank, 16, bold=True)
    d.kv([(l['holder'], d.pii('Names', who)), (l['addr'], d.pii('Addresses', address(r, lang))),
          (l['acct'], d.pii('Bank accounts', iban(r, COUNTRY[lang]))), (l['period'], f'{d.neg(date(r))} - {date(r)}'),
          ('BIC', d.opt(r.choice(['MLDGGB2L', 'STBMDEFF', 'NGSVFRPP'])))])
    rows = [[l['date'], l['desc'], l['amount']]]
    for merchant in r.sample(['Tidewater Supplies', 'Larkspur Systems', 'City Water Utility', 'Grocer & Co', 'Railway Tickets Online'], 3):
        rows.append([date(r), d.neg(merchant), d.neg(money(r, CUR[lang]))])
    transfer_iban = iban(r, COUNTRY[lang])
    rows.append([date(r), f'Transfer {payee}', money(r, CUR[lang])]); d.pii('Names', payee)
    d.table([90, 300, 110], rows)
    d.text({'en': 'Beneficiary account', 'de': 'Empfängerkonto', 'fr': 'Compte bénéficiaire', 'es': 'Cuenta beneficiaria'}[lang]
           + f': {d.pii("Bank accounts", transfer_iban)}', 9.5)


def email_print(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    org = d.neg(r.choice(ORGS)); a, b, c = person(r, lang), person(r, lang), person(r, lang)
    d.new_page(); d.text(d.neg({'en': 'Re: Delivery schedule for order', 'de': 'AW: Lieferplan', 'fr': 'RE : Planning de livraison',
                                'es': 'RE: Calendario de entrega'}[lang]), 13, bold=True)
    ea, eb, ec = email(r, a, org), email(r, b), email(r, c, org)
    d.kv([('From', f'{d.pii("Names", a)} <{d.pii("Email addresses", ea)}>'), ('To', f'{d.pii("Names", b)} <{d.pii("Email addresses", eb)}>'),
          ('Cc', f'{d.pii("Names", c)} <{d.pii("Email addresses", ec)}>'), ('Sent', d.neg(date(r)))], 9.5)
    order, tel = d.neg(product(r)), d.pii('Phone numbers', phone(r, lang))
    body = {'en': f'Hi {b.split()[0]}, the pallets for item {order} will ship on Thursday. If anything changes, call me directly on '
                  f'{tel}. {c.split()[0]} is handling customs paperwork.',
            'de': f'Hallo {b.split()[0]}, die Paletten für Artikel {order} gehen am Donnerstag raus. Bei Änderungen erreichen Sie mich unter '
                  f'{tel}.',
            'fr': f'Bonjour {b.split()[0]}, les palettes pour l\'article {order} partiront jeudi. En cas de changement, appelez-moi au '
                  f'{tel}.',
            'es': f'Hola {b.split()[0]}, los palés del artículo {order} salen el jueves. Si algo cambia, llámame al '
                  f'{tel}.'}[lang]
    d.y += 8; d.para(body, 10.5, width=88); d.y += 10
    d.text(a, 10); d.text(d.neg(r.choice(['Logistics Coordinator', 'Operations Lead', 'Shipping Supervisor'])), 9); d.text(org, 9)


def medical(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    who, contact, doc = person(r, lang), person(r, lang), person(r, lang)
    d.new_page(); d.text(d.neg(r.choice(['Orchardview Clinic', 'Bluefern Health'])), 15, bold=True)
    d.text({'en': 'Patient Intake Form', 'de': 'Aufnahmebogen', 'fr': "Fiche d'admission", 'es': 'Formulario de ingreso'}[lang], 12)
    d.kv([(l['patient'], d.pii('Names', who)), (l['mrn'], d.pii('Identifiers', ident(r))),
          (l['dob'], d.opt(f'{r.randint(1940, 2010)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}')),
          (l['addr'], d.pii('Addresses', address(r, lang))), (l['phone'], d.pii('Phone numbers', phone(r, lang))),
          (l['insurer'], d.neg(r.choice(['Stonebridge Mutual', 'Harborline Health Plan']))), (l['policy'], d.pii('Identifiers', ident(r))),
          (l['ec'], f'{d.pii("Names", contact)}, {d.pii("Phone numbers", phone(r, lang))}'), (l['doctor'], d.pii('Names', doc))])
    rows = [['Code', l['desc'], l['date']], [d.neg('J45.909'), d.neg('Asthma, uncomplicated'), date(r)],
            [d.neg('Z00.00'), d.neg('General adult examination'), date(r)]]
    d.table([90, 280, 100], rows)


def roster(d):
    r, l, lang = d.rng, L[d.lang], d.lang
    org = d.neg(r.choice(ORGS)); d.new_page(); d.text(f'{org} - ' + {'en': 'Staff roster', 'de': 'Dienstplan', 'fr': 'Planning du personnel',
                                                                   'es': 'Cuadrante de personal', 'ko': '근무 명단'}[lang], 14, bold=True)
    rows = [[l['emp'], l['name'], l['dept'], l['email'], l['shift']]]
    for _ in range(7):
        who = person(r, lang)
        rows.append([d.pii('Identifiers', ident(r, r.randint(0, 2), 5)), d.pii('Names', who),
                     r.choice(['Finance', 'Logistics', 'Support', 'Research']), d.pii('Email addresses', email(r, who, org) if lang != 'ko' else
                     f'user{r.randint(100, 999)}@{ascii_slug(org)[:10]}.example'), r.choice(['A', 'B', 'C'])])
    d.table([80, 120, 80, 180, 45], rows, size=8)
    d.text(d.neg({'en': 'Shift A: 06:00-14:00', 'de': 'Schicht A: 06:00-14:00', 'fr': 'Équipe A : 06:00-14:00',
                  'es': 'Turno A: 06:00-14:00', 'ko': 'A조: 06:00-14:00'}[lang]), 9)


def no_pii(d):
    r, lang = d.rng, d.lang
    org = d.neg(r.choice(ORGS)); d.new_page()
    d.text(org + ' - ' + {'en': 'Product catalogue', 'de': 'Produktkatalog', 'fr': 'Catalogue produits', 'es': 'Catálogo de productos'}[lang], 15, bold=True)
    d.para(d.neg({'en': 'All prices exclude VAT and are valid until the end of the quarter.',
                  'de': 'Alle Preise verstehen sich zuzüglich MwSt.', 'fr': 'Tous les prix sont hors TVA.',
                  'es': 'Todos los precios son sin IVA.'}[lang]), 10)
    rows = [['SKU', 'Model', {'en': 'Description', 'de': 'Beschreibung', 'fr': 'Désignation', 'es': 'Descripción'}[lang], 'Price']]
    for item in r.sample(['Industrial fan 40 cm', 'LED panel 60x60', 'Pallet jack 2.5 t', 'Safety gloves (pair)', 'Barcode scanner',
                          'Thermal labels 100x150', 'Cable tray 3 m'], 5):
        rows.append([d.neg(product(r)), d.neg(ident(r, 2, 3)), d.neg(item), d.neg(money(r, CUR[lang]))])
    d.table([90, 80, 220, 90], rows)
    d.kv([('Firmware', d.neg(f'v{r.randint(1, 5)}.{r.randint(0, 9)}.{r.randint(0, 30)}')), ('Build', d.neg(f'{r.randint(1000, 9999)}-rc{r.randint(1, 4)}')),
          ('Warranty', d.neg(f'{r.randint(1, 5)} years')), ('Batch', d.neg(ident(r, 1, 6)))])


def it_incident(d):
    r, lang = d.rng, d.lang
    who, eng = person(r, lang), person(r, lang)
    d.new_page(); d.text(f"{'Incident report' if lang == 'en' else 'Störungsbericht'} {d.pii('Identifiers', 'INC-' + ident(r, 0, 6))}", 15, bold=True)
    user = d.pii('Identifiers', f'{ascii_slug(who.split()[0])[:1]}{ascii_slug(who.split()[-1])}{r.randint(1, 99)}')
    host = d.pii('Identifiers', f'{r.choice(["LT", "WS", "PC"])}-{r.choice(["FIN", "OPS", "HR"])}-{r.randint(100, 999)}')
    ip = d.pii('Identifiers', f'10.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(2, 254)}')
    key = d.pii('Identifiers', secret(r))
    d.kv([('Reported by' if lang == 'en' else 'Gemeldet von', d.pii('Names', who)), ('User name' if lang == 'en' else 'Benutzername', user),
          ('Device' if lang == 'en' else 'Gerät', host), ('IP address' if lang == 'en' else 'IP-Adresse', ip),
          ('Service', d.neg(r.choice(['payroll-export', 'report-scheduler', 'vpn-gateway']))),
          ('Version', d.neg(f'{r.randint(1, 9)}.{r.randint(0, 20)}.{r.randint(0, 9)}')), ('Severity', d.neg(f'P{r.randint(1, 4)}'))])
    d.para({'en': f'The on-call engineer {eng} found that an access key ({key}) had been committed to a shared script. The key was revoked '
                  f'and the account {user} was locked pending review.',
            'de': f'Der Bereitschaftsingenieur {eng} stellte fest, dass ein Zugriffsschlüssel ({key}) in einem Skript stand. Der Schlüssel '
                  f'wurde widerrufen und das Konto {user} gesperrt.'}[lang], 10, width=88)
    d.pii('Names', eng)


def badge_png(rng, lang, d, where='image'):
    """An ID badge raster: its text exists only in pixels."""
    who = person(rng, lang); staff = ident(rng); tel = phone(rng, lang); mail = email(rng, who)
    with pymupdf.open() as tmp:
        p = tmp.new_page(width=340, height=200)
        p.draw_rect(p.rect, color=None, fill=(.95, .97, 1)); p.draw_rect(pymupdf.Rect(0, 0, 340, 36), color=None, fill=(.15, .3, .55))
        p.insert_text((14, 24), rng.choice(ORGS).upper(), fontsize=12, fontname='hebo', color=(1, 1, 1))
        p.draw_rect(pymupdf.Rect(14, 50, 94, 150), color=(.5, .5, .5), fill=(.8, .82, .86))
        for i, s in enumerate([who, f'ID {staff}', tel, mail]):
            p.insert_text((108, 70 + i * 22), s, fontsize=11 if i == 0 else 9.5, fontname='hebo' if i == 0 else 'helv')
        png = p.get_pixmap(dpi=220).tobytes('png')
    for c, v in (('Names', who), ('Identifiers', staff), ('Phone numbers', tel), ('Email addresses', mail)): d.pii(c, v, where)
    return png


def image_report(d):
    r, lang = d.rng, d.lang
    d.new_page(); d.text({'en': 'Site access audit', 'de': 'Prüfung der Zutrittskontrolle', 'es': 'Auditoría de acceso'}[lang], 15, bold=True)
    d.para(d.neg({'en': 'Badges photographed during the quarterly audit are attached below.',
                  'de': 'Die bei der Quartalsprüfung fotografierten Ausweise sind unten angehängt.',
                  'es': 'Las tarjetas fotografiadas durante la auditoría trimestral se adjuntan abajo.'}[lang]), 10)
    for i in range(2):
        png = badge_png(r, lang, d)
        d.page.insert_image(pymupdf.Rect(60, d.y + i * 190, 400, d.y + i * 190 + 200), stream=png)
    d.y += 400
    d.text(f"Audit {d.neg(date(r))} - {d.neg('Gate 3')}", 10)


# render variants -------------------------------------------------------
def rasterize(pdf, rng, dpi=170, rotate_page=False, skew=True, crop=False):
    """Replace every page with an image of itself (a scan); text then exists only in pixels."""
    from PIL import Image, ImageFilter
    out = pymupdf.open()
    for page in pdf:
        img = Image.open(io.BytesIO(page.get_pixmap(dpi=dpi).tobytes('png'))).convert('L')
        if skew: img = img.rotate(rng.uniform(-.8, .8), expand=False, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(.35))
        buf = io.BytesIO(); img.save(buf, format='PNG'); png = buf.getvalue()
        if rotate_page:
            # Stored sideways (landscape media box) with /Rotate 90, so it displays upright like a fed-sideways scan.
            target = out.new_page(width=page.rect.height, height=page.rect.width)
            target.insert_image(target.rect, stream=png, rotate=90); target.set_rotation(90)
        else:
            target = out.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, stream=png)
        if crop: target.set_cropbox(pymupdf.Rect(24, 30, page.rect.width - 18, page.rect.height - 26))
    return out


PLAN = [  # (template, language, variant, count)
    (invoice, 'en', 'native', 2), (invoice, 'de', 'native', 1), (invoice, 'es', 'native', 1), (invoice, 'en', 'scan', 1), (invoice, 'fr', 'scan', 1),
    (hr_form, 'en', 'native', 1), (hr_form, 'fr', 'native', 1), (hr_form, 'ko', 'native', 1), (hr_form, 'de', 'native', 1),
    (hr_form, 'en', 'scan', 1), (hr_form, 'en', 'rotated_scan', 1),
    (letter, 'en', 'native', 1), (letter, 'de', 'native', 1), (letter, 'es', 'native', 1), (letter, 'en', 'scan', 1), (letter, 'fr', 'rotated_scan', 1),
    (minutes, 'en', 'native', 1), (minutes, 'fr', 'native', 1), (minutes, 'ko', 'native', 1), (minutes, 'en', 'scan', 1),
    (bank_statement, 'en', 'native', 1), (bank_statement, 'de', 'native', 1), (bank_statement, 'fr', 'native', 1),
    (bank_statement, 'es', 'native', 1), (bank_statement, 'en', 'scan', 1),
    (email_print, 'en', 'native', 2), (email_print, 'de', 'native', 1), (email_print, 'es', 'native', 1), (email_print, 'en', 'screenshot', 1),
    (medical, 'en', 'native', 1), (medical, 'es', 'native', 1), (medical, 'de', 'native', 1), (medical, 'en', 'scan', 1), (medical, 'en', 'rotated_scan', 1),
    (roster, 'en', 'native', 1), (roster, 'de', 'native', 1), (roster, 'ko', 'native', 1), (roster, 'en', 'scan', 1),
    (no_pii, 'en', 'native', 2), (no_pii, 'de', 'native', 1), (no_pii, 'fr', 'native', 1), (no_pii, 'en', 'scan', 1),
    (it_incident, 'en', 'native', 2), (it_incident, 'de', 'native', 1), (it_incident, 'en', 'screenshot', 1),
    (image_report, 'en', 'native', 1), (image_report, 'de', 'native', 1), (image_report, 'es', 'native', 1),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument('out'); ap.add_argument('--seed', type=int, default=20261112)
    args = ap.parse_args(); out = Path(args.out); (out / 'pdfs').mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed); key = {'_meta': {'seed': args.seed, 'generator': 'evaluation/pii/generate.py',
                                                      'categories': ['Names', 'Email addresses', 'Phone numbers', 'Addresses',
                                                                     'Identifiers', 'Bank accounts'],
                                                      'review_status': 'unreviewed'}}
    n = 0
    for template, lang, variant, count in PLAN:
        for _ in range(count):
            n += 1; name = f'E{n:02d}_{template.__name__}_{lang}_{variant}'
            d = Doc(random.Random(rng.random()), name, template.__name__, lang); template(d); d.check()
            pdf = d.pdf
            if variant in ('scan', 'rotated_scan', 'screenshot'):
                pdf = rasterize(d.pdf, d.rng, dpi=200 if variant == 'screenshot' else 170, rotate_page=variant == 'rotated_scan',
                                skew=variant != 'screenshot', crop=variant == 'screenshot')
                d.expected = [[c, v, 'image'] for c, v, _ in d.expected]
            pdf.save(out / 'pdfs' / f'{name}.pdf', garbage=3, deflate=True)
            key[name] = {'kind': f'{template.__name__} / {variant}', 'language': lang, 'variant': variant, 'pages': len(pdf),
                         'expected': d.expected, 'optional': sorted(set(d.optional)), 'negatives': sorted(set(d.negatives))}
    (out / 'answer_key.json').write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding='utf-8')
    docs = [k for k in key if not k.startswith('_')]
    print(f"{len(docs)} documents, {sum(key[k]['pages'] for k in docs)} pages, "
          f"{sum(len(key[k]['expected']) for k in docs)} expected values, {sum(len(key[k]['negatives']) for k in docs)} negatives -> {out}")


if __name__ == '__main__':
    main()
