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

Both ignore spaces and letter case. No recorded readings keep a line uncertain. A single model keeps a
line uncertain too, except in reads_consistently for a line of words only (below).

Korean, Japanese, Chinese, Arabic and Hindi are read by one recognition model. With the two-model rule
alone every uncertain line of those scans kept its source pixels, so a Korean page translated into
English kept Korean sentences whose five readings differed only in a final period or a comma
(2026-11-23). A line without digits or "@" whose readings all come from that one model is rebuilt
when three or more confident readings give the same letters, punctuation aside, and outnumber the
readings that differ. Lines with numbers, e-mail addresses and codes keep the two-model rule.
"""
import re
import unicodedata


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


def letters(text):
    """The text's characters without spaces, letter case or punctuation (words only)."""
    return ''.join(c for c in characters(text) if not unicodedata.category(c).startswith('P'))


def _one_model(region):
    """True when every recorded reading names the same single model (the language has one recognizer)."""
    names = {c.get('model') for c in region.get('metadata', {}).get('candidates', [])
             if isinstance(c, dict) and c.get('purpose') != 'redaction_geometry_only'}
    return len(names) == 1 and None not in names


def reads_consistently(region):
    """True when confident readings from two or more models give the region's characters (ignoring
    spaces and letter case) and outnumber the confident readings that differ; or, for a line of words
    only that one model reads, when three or more confident readings give its letters and outnumber
    the readings that differ."""
    if region.get('metadata', {}).get('recognition_decision') == 'digits_dropped_by_language_model':
        return False      # the readings agree, but all lack the line's numbers (recognize_paddle_boxes.dropped_digits)
    key, confident = _readings(region)
    agree = [c for c in confident if characters(c['text']) == key]
    if agree and len(agree) > len(confident) - len(agree) and _models(agree) >= 2:
        return True
    text = region.get('text', '')
    if not confident or re.search(r'[\d@]', text) or any(re.search(r'[\d@]', c['text']) for c in confident):
        return False
    if not _one_model(region) or not letters(text):
        return False
    same = [c for c in confident if letters(c['text']) == letters(text)]
    return len(same) >= 3 and len(same) > len(confident) - len(same)


def literal_line(region):
    """True when the line and two or more confident readings of it are each a literal that needs no
    translation (an e-mail address, code, number or date): a scan keeps such a line's source pixels
    without asking for a review of the reading."""
    from transformation.llm.policy import expected_unchanged
    text = region.get('text', '').strip()
    _, confident = _readings(region)
    if not text or len(confident) < 2 or not expected_unchanged(text): return False
    return all(expected_unchanged(c['text'].strip()) for c in confident)
