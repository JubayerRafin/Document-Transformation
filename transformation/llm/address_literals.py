"""Postal addresses are kept exactly as written in translations (decided 2026-11-17).

Translation models localise addresses inconsistently: one Korean street address came back in four
different forms across eight languages ("Avenida Teheran, número 4", "طريق تهران، رقم 4", a house
number read as "4-illegal"). An address must stay usable for mail and is personal data, so it is
copied unchanged, like an email address.

Two sources, neither specific to a document:
  - field values whose label names an address (a table row "Home Address | ..." or a line
    "Mailing address: ..."), from the extraction IR;
  - street patterns in running text: a house number with a street-type word ("221B Baker Street",
    "12, Teheran-ro 4-gil", "Hauptstraße 5", "rue de Rivoli 10").
Each address and its comma-separated parts become protected literals, because page extraction
can split one address across lines.
"""
import re

LABEL = re.compile(r'(?i)\b(?:(?:home|postal|mailing|street|billing|shipping|residential|office|business|'
                   r'registered|permanent|current)\s+)?(?:address|addr\.?)\b|주소|住所|地址|العنوان|पता|ঠিকানা')
# Street-type words (English, Romance, German, Korean/Japanese romanisation) after or before a name.
STREET = (r'(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|'
          r'Way|Place|Pl\.?|Square|Sq\.?|Highway|Hwy\.?|Terrace|Parkway|Pkwy\.?)')
PATTERNS = [
    # 221B Baker Street / 1600 Pennsylvania Avenue NW
    re.compile(r'\b\d{1,5}[A-Za-z]?,?\s+(?:[A-Z][\w\'.-]*\s+){1,4}' + STREET + r'(?:\s+(?:NW|NE|SW|SE|N|S|E|W))?\b'),
    # Romanised East Asian streets: 12, Teheran-ro 4-gil / Sejong-daero 110 / Gangnam-gu
    re.compile(r'(?:\b\d{1,5},?\s+)?\b[A-Z][a-z]+-(?:ro|gil|daero|dong|gu|si|eup|myeon|ku|cho|machi)\b'
               r'(?:\s+\d{1,5}(?:-(?:gil|ro|beongil))?)?'),
    # Hauptstraße 5 / Lindenstr. 12
    re.compile(r'\b[A-ZÄÖÜ][\wäöüß-]*(?:straße|strasse|str\.|weg|platz|allee|gasse)\s+\d{1,5}[a-z]?\b'),
    # rue de Rivoli 10 / calle Mayor 5 / via Roma 1 / rua Augusta 200 / avenida Paulista 1578
    # (English "Avenue"/"Boulevard" are handled above; French ones are written in lower case here.)
    re.compile(r'\b(?:\d{1,5},?\s+)?(?:[Rr]ue|avenue|boulevard|[Cc]alle|[Aa]venida|[Vv]ia|[Vv]iale|[Rr]ua|[Pp]raça|[Pp]laza)\s+'
               r'(?:(?:de|del|da|do|dos|das|la|le|les|des|di)\s+)?[A-ZÀ-Ý][\w\'-]*(?:\s+\d{1,5})?'),
]


def street_addresses(text):
    """Street-address fragments found in running text."""
    found = []
    for pattern in PATTERNS:
        found.extend(m.group(0).strip(' ,') for m in pattern.finditer(text or ''))
    return found


def _parts(address):
    """The address and its comma-separated parts that are specific enough to protect."""
    address = ' '.join(address.split()).strip(' ,')
    parts = [address]
    pieces = [p.strip() for p in address.split(',') if p.strip()]
    # "12, Teheran-ro 4-gil": a bare house number belongs to the next part.
    merged = []
    for piece in pieces:
        if merged and re.fullmatch(r'\d{1,5}[A-Za-z]?', merged[-1]):
            merged[-1] = merged[-1] + ', ' + piece
        else:
            merged.append(piece)
    parts.extend(p for p in merged if len(p) >= 4 or any(c.isdigit() for c in p))
    return parts


def labelled_addresses(ir):
    """Address field values from table rows and 'Label: value' lines in the extraction IR."""
    found = []
    for unit in ir.get('units', []):
        for region in unit.get('regions', []):
            for row in region.get('cells') or []:
                cells = [str(c or '').strip() for c in row]
                for i, cell in enumerate(cells[:-1]):
                    if cell and len(cell) <= 40 and LABEL.search(cell) and cells[i + 1]:
                        found.append(cells[i + 1])
            text = region.get('text') or ''
            for line in text.split('\n'):
                label, colon, value = line.partition(':')
                if colon and value.strip() and len(label) <= 40 and LABEL.search(label):
                    found.append(value.strip())
    return found


def address_literals(ir):
    """Protected literals for every address in the document (deduplicated, longest first)."""
    texts = [r.get('text') or '' for u in ir.get('units', []) for r in u.get('regions', [])]
    texts += [str(c or '') for u in ir.get('units', []) for r in u.get('regions', [])
              for row in r.get('cells') or [] for c in row]
    addresses = labelled_addresses(ir) + [a for t in texts for a in street_addresses(t)]
    literals = []
    for address in addresses:
        for part in _parts(address):
            if part not in literals: literals.append(part)
    return sorted(literals, key=len, reverse=True)
