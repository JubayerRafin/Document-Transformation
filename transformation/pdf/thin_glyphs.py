"""Keep thin punctuation glyphs (underscore, period, hyphen) inside their word in BabelDOC.

BabelDOC reads a character's position from its drawn outline. An underscore's outline is a thin
strip below the baseline, so BabelDOC 0.6.4 (seen 2026-11-16):
  - split it onto a line of its own: "sk_live_key" became "sklive key" with "_ _" after the
    paragraph, and the underscores were drawn apart from the word;
  - with that fixed alone, took the outline lying outside the font box for a formula and made a
    placeholder of each underscore and the digit after it ("sk{v1}live{v2}f9a"), which sent the
    translation model into a loop.
Before BabelDOC groups a page's characters into paragraphs, a punctuation glyph whose outline is
much flatter than its font box gets the font box's vertical extent (which spans the line like any
letter) as its outline, so it is read, ordered and translated as ordinary text. Letters, digits,
symbols and math are unchanged.
"""
import unicodedata

THIN = 0.25  # outline height below this share of the font box height


def thin_punctuation(char):
    text, box = char.char_unicode, char.box
    visual = char.visual_bbox.box if char.visual_bbox is not None else None
    if not text or len(text) != 1 or box is None or visual is None: return False
    if unicodedata.category(text)[0] != 'P': return False
    font_height = box.y2 - box.y
    return font_height > 0 and (visual.y2 - visual.y) < THIN * font_height


def align(page):
    """Give thin punctuation on the page its font box's vertical extent; returns how many."""
    count = 0
    for char in page.pdf_character or []:
        if thin_punctuation(char):
            visual = char.visual_bbox.box
            char.visual_bbox.box = type(visual)(x=visual.x, y=char.box.y, x2=visual.x2, y2=char.box.y2)
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
