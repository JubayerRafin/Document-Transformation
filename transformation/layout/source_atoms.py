"""Retain measured source geometry for unchanged notation after fitting fails."""
from transformation.pdf.ir_json import ir_copy
import copy
import re


def is_source_atom(text):
    """Exclude ordinary prose; accept quantities, acronyms and structured IDs."""
    text=re.sub(r'\{v\d+\}', '0', text or '')
    words=re.findall(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*',text)
    residue=re.sub(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*','',text)
    if not words or re.search(r'[^\s×÷+\-*/=<>≤≥.,:;()[\]_%{}]',residue):return False
    return all(word.isdigit() or re.fullmatch(r'[A-Z]{2,8}\d*',word) or re.fullmatch(r'\d+(?:-\d+)+',word)
               or ('-' in word and any(c.isdigit() for c in word) and any(c.isalpha() for c in word))
               for word in words)


def source_characters(paragraph):
    chars=[]
    for item in paragraph.pdf_paragraph_composition:
        if item.pdf_character:chars.append(item.pdf_character)
        elif item.pdf_line:chars.extend(item.pdf_line.pdf_character)
        elif item.pdf_same_style_characters:chars.extend(item.pdf_same_style_characters.pdf_character)
        elif item.pdf_formula:
            if item.pdf_formula.pdf_curve or item.pdf_formula.pdf_form:return []
            chars.extend(item.pdf_formula.pdf_character)
        else:return []
    return chars


def capture_source_atoms(document):
    """Every horizontal paragraph whose characters can be copied, keyed by (page, paragraph id): one
    the translation leaves exactly as it was is drawn with these characters where they were
    (preserved_source_characters). Only amounts, codes and dates were kept this way; a column of
    file types or codec names ("*.avi" / "*.mkv" / ..., "AC3" / "LPCM" / ...) that the translation
    left unchanged was re-typeset as wrapped text, items in the wrong rows and over each other
    (Samsung manual, about 25 pages, 2026-11-23)."""
    result={}
    for page in document.page:
        neighbors={p.debug_id:copy.deepcopy(p.box) for p in page.pdf_paragraph if p.box}
        for p in page.pdf_paragraph:
            if not p.vertical and (p.unicode or '').strip() and source_characters(p):
                result[(page.page_number+1,p.debug_id)]={'paragraph':ir_copy(p),'neighbors':neighbors}
    return result


def preserved_source_characters(entry,paragraph,page):
    if not entry:return None
    text=paragraph.unicode or ''
    formulas=[c.pdf_formula for c in paragraph.pdf_paragraph_composition if c.pdf_formula]
    for index,formula in enumerate(formulas,1):
        text=text.replace('{v'+str(index)+'}',''.join(c.char_unicode or '' for c in formula.pdf_character))
    if text!=entry['paragraph'].unicode:return None
    source=entry['paragraph'];chars=source_characters(source)
    crop=getattr(getattr(page,'cropbox',None),'box',None)
    if not chars or crop is None:return None
    def intersection(a,b):
        return max(0,min(a.x2,b.x2)-max(a.x,b.x))*max(0,min(a.y2,b.y2)-max(a.y,b.y))
    for char in chars:
        size=getattr(getattr(char,'pdf_style',None),'font_size',None)
        if size is None or size<=0:return None
        if (char.char_unicode or '').isspace():continue
        if getattr(char,'pdf_character_id',None) is None:return None
        box=getattr(getattr(char,'visual_bbox',None),'box',None)
        if box is None or not (crop.x<=box.x<box.x2<=crop.x2 and crop.y<=box.y<box.y2<=crop.y2):return None
        for other in page.pdf_paragraph:
            if other.debug_id==paragraph.debug_id or other.box is None:continue
            previous=entry['neighbors'].get(other.debug_id)
            if previous is None:return None
            # Existing source contact is not a newly introduced collision.
            if intersection(box,other.box)>intersection(box,previous)+.01:return None
    return ir_copy(reading_order(chars))


def reading_order(chars):
    """Characters line by line, top to bottom, each line left to right.

    A paragraph's characters are not always stored in that order: a date wrapped in a narrow cell
    ("2025-" / "07-13") kept "5-" in a formula after the next line, and drawn in that order the PDF's
    text read "202 07-13 5-", so "2025" could not be found or copied (German news, 2026-11-21)."""
    def box(c):
        return getattr(getattr(c,'visual_bbox',None),'box',None) or getattr(c,'box',None)
    placed=[c for c in chars if box(c) is not None]
    if len(placed)!=len(chars):return chars
    # A character belongs to a line when most of its height lies within the line's: a comma's small
    # box sits at the baseline, below the digits' middle, and must stay in "563,982,000" (2026-11-21).
    lines=[]
    for c in sorted(chars,key=lambda c:-(box(c).y2-box(c).y)):
        b=box(c);height=max(b.y2-b.y,.1)
        best=max(lines,key=lambda line:min(b.y2,line[1])-max(b.y,line[0]),default=None)
        if best is not None and min(b.y2,best[1])-max(b.y,best[0])>=.25*height:
            best[0],best[1]=min(best[0],b.y),max(best[1],b.y2);best[2].append(c)
        else:lines.append([b.y,b.y2,[c]])
    lines.sort(key=lambda line:-(line[0]+line[1])/2)
    return [c for *_,line in lines for c in sorted(line,key=lambda c:box(c).x)]
