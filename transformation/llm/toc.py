"""Table-of-contents entries: translate the title, keep the leader dots and the page number.

A contents line reaches the translator as "Check the sender's fax machine......183". Models drop
the leader and glue the number to a sentence ("발신자의 팩스기를 확인하십시오.183"), so every contents page
of a manual lost its layout (HP printer manual, 2026-11-19). The title alone is translated; the
leader is kept, shortened or lengthened by the change in the title's width so the line keeps its
length, and the page number (or its placeholder) follows unchanged.
"""
import re
import unicodedata

LEADER = re.compile(r'^(?P<title>.*?\S)(?P<leader>(?:\s*[.·…_]\s*){6,})(?P<tail>(?:\{v\d+\}|[0-9ivxlcdmIVXLCDM]{1,6}|[A-Z]-\d{1,4})?)\s*$',
                    re.S)


def split(text):
    """(title, leader, tail) for a contents entry, or None."""
    match = LEADER.match(text or '')
    if not match or not any(c.isalpha() for c in match['title']):
        return None
    return match['title'], match['leader'], match['tail']


def width(text):
    """Approximate display width: wide (CJK) characters count twice."""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def rebuild(title, translated, leader, tail):
    dot = next((c for c in leader if not c.isspace()), '.')
    dots = sum(not c.isspace() for c in leader)
    dots = max(3, dots - (width(translated) - width(title)))
    return translated.rstrip(' .。') + ' ' + dot * dots + (' ' if tail else '') + tail
