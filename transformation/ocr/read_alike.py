"""OCR lines flagged uncertain although the recognition models read the same characters.

The OCR marks a line for review when its readings disagree, including "LaserJet Pro X1" against
"LaserJet ProX1" or "Images" against "images".

read_alike (PII review queue): the characters are the same in every confident reading, from two or
more models, so the line is not "unreadable". Any differing character keeps it in front of a person.

reads_consistently (scanned-page rebuild for translation): the OCR also reads each line several
times (crops with different padding), and one of those readings can drop a separator or a letter
("Fomat") while the rest agree. The line is rebuilt and translated when confident readings from two
or more models give exactly its characters and outnumber the confident readings that differ;
otherwise its source pixels are kept, as before.

Both ignore spaces and letter case. A single model or no recorded readings keep a line uncertain.
"""


def characters(text):
    return ''.join(text.split()).casefold()


def _readings(region):
    meta = region.get('metadata', {})
    key = characters(region.get('text', ''))
    if not key or meta.get('ocr_unreadable'): return key, []
    readings = [c for c in meta.get('candidates', []) if isinstance(c, dict) and c.get('purpose') != 'redaction_geometry_only'
                and isinstance(c.get('text'), str) and c['text'].strip() and isinstance(c.get('score'), (int, float))]
    return key, [c for c in readings if c['score'] >= .85]


def _models(readings):
    return len({c.get('model') for c in readings if c.get('model')})


def read_alike(region):
    """True when two recognition models read the region's characters confidently and every confident
    reading agrees with its text, ignoring spaces and letter case."""
    key, confident = _readings(region)
    if not confident or any(characters(c['text']) != key for c in confident): return False
    return _models(confident) >= 2


def reads_consistently(region):
    """True when confident readings from two or more models give the region's characters (ignoring
    spaces and letter case) and outnumber the confident readings that differ."""
    key, confident = _readings(region)
    agree = [c for c in confident if characters(c['text']) == key]
    if not agree or len(agree) <= len(confident) - len(agree): return False
    return _models(agree) >= 2
