"""Retain measured source geometry for unchanged notation after fitting fails."""
import copy
import re


def is_source_atom(text):
    """Exclude ordinary prose; accept quantities, acronyms and structured IDs."""
    text=re.sub(r'\{v\d+\}', '0', text or '')
    words=re.findall(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*',text)
    residue=re.sub(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*','',text)
    if not words or re.search(r'[^\s×÷+\-*/=<>≤≥.,:;()[\]_%{}]',residue):return False
    return all(word.isdigit() or re.fullmatch(r'[A-Z]{2,8}\d*',word)
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
    result={}
    for page in document.page:
        neighbors={p.debug_id:copy.deepcopy(p.box) for p in page.pdf_paragraph if p.box}
        for p in page.pdf_paragraph:
            if not p.vertical and is_source_atom(p.unicode) and source_characters(p):
                result[(page.page_number+1,p.debug_id)]={'paragraph':copy.deepcopy(p),'neighbors':neighbors}
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
    return copy.deepcopy(chars)
