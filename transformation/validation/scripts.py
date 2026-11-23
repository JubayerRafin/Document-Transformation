"""Writing-system boundaries inside text that has no space between two scripts."""
import unicodedata


def script(char):
    """The writing system of a letter or digit: 'latin' for ASCII letters, digits and Latin letters
    with accents, else the first word of its Unicode name (ARABIC, DEVANAGARI, HANGUL, ...); None for
    other characters."""
    if not char.isalnum():
        return None
    if char.isascii():
        return 'latin'
    name = unicodedata.name(char, '')
    first = name.split(' ')[0].split('-')[0]
    if first == 'LATIN':
        return 'latin'
    return first or None


def changes(a, b):
    """True when two touching characters are letters or digits of different writing systems.

    PDFs of right-to-left text store an Arabic word and the number or code beside it with no space
    between them ("شخص618", "الباركودQTR-2027-09733"); there the change of script is the word
    boundary (Arabic invoice and report, 2026-11-23)."""
    x, y = script(a), script(b)
    return bool(x and y and x != y)
