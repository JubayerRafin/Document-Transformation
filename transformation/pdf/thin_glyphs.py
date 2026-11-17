"""Keep thin punctuation glyphs (underscore, period, hyphen) inside their word in BabelDOC.

BabelDOC reads a character's position from its drawn outline. An underscore's outline is a thin
strip below the baseline, so BabelDOC 0.6.4 (seen 2026-11-16):
  - split it onto a line of its own: "sk_live_key" became "sklive key" with "_ _" after the
    paragraph, and the underscores were drawn apart from the word;
  - with that fixed alone, took the outline lying outside the font box for a formula and made a
    placeholder of each underscore and the digit after it ("sk{v1}live{v2}f9a"), which sent the
    translation model into a loop.
Before BabelDOC groups a page's characters into paragraphs, a punctuation glyph whose outline is
much flatter than its font box gets the vertical extent of the letters and digits on its line
(their median outline) as its outline, so it is read, ordered and translated as ordinary text.
Letters, digits, symbols and math are unchanged.

Not the font box itself: it reaches well above the visible letters, and on 2026-11-17 that put the
hyphens of "HR-2024-00417" into the page-layout model's title region just above the line, which
split the ID into three paragraphs ("Document ID: HR", "- -", "2024") in every language.
"""
import unicodedata
from collections import defaultdict
from statistics import median

THIN = 0.25  # outline height below this share of the font box height
FALLBACK = 0.7  # share of the font box height from its bottom, when the line has no letters


def thin_punctuation(char):
    text, box = char.char_unicode, char.box
    visual = char.visual_bbox.box if char.visual_bbox is not None else None
    if not text or len(text) != 1 or box is None or visual is None: return False
    if unicodedata.category(text)[0] != 'P': return False
    font_height = box.y2 - box.y
    return font_height > 0 and (visual.y2 - visual.y) < THIN * font_height


def _line(box):
    """Characters of one line and size share their font box's vertical extent."""
    return round(box.y * 2) / 2, round(box.y2 * 2) / 2


def align(page):
    """Give thin punctuation on the page the vertical extent of the letters on its line; returns how many."""
    chars = page.pdf_character or []
    letters = defaultdict(list)
    for char in chars:
        text = char.char_unicode
        if text and len(text) == 1 and text.isalnum() and char.box is not None and char.visual_bbox is not None:
            visual = char.visual_bbox.box
            letters[_line(char.box)].append((visual.y, visual.y2))
    count = 0
    for char in chars:
        if thin_punctuation(char):
            visual, box = char.visual_bbox.box, char.box
            seen = letters.get(_line(box))
            if seen:
                bottom, top = median(y for y, _ in seen), median(y2 for _, y2 in seen)
            else:
                bottom, top = box.y, box.y + FALLBACK * (box.y2 - box.y)
            char.visual_bbox.box = type(visual)(x=visual.x, y=bottom, x2=visual.x2, y2=top)
            count += 1
    return count


def install(finder=None):
    """Patch BabelDOC's ParagraphFinder to align each page first; returns True when installed."""
    if finder is None:
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder as finder
    if getattr(finder, '_transformx_thin_glyphs', False): return True
    original = finder.process_page

    def process_page(self, page):
        align(page)
        return original(self, page)

    finder.process_page = process_page
    finder._transformx_thin_glyphs = True
    return True
