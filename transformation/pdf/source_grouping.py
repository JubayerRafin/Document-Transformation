"""Regroup only source-proven paragraph continuations; preserve rich glyph data."""
import re
from copy import deepcopy


def composition_text(paragraph):
    chunks=[]
    for comp in getattr(paragraph,'pdf_paragraph_composition',[]) or []:
        group=getattr(comp,'pdf_same_style_unicode_characters',None)
        if group is not None:chunks.append(group.unicode or '');continue
        char=getattr(comp,'pdf_character',None)
        if char is not None:chunks.append(char.char_unicode or '');continue
        for name in ('pdf_same_style_characters','pdf_line'):
            group=getattr(comp,name,None)
            if group is not None:
                chunks.append(''.join(c.char_unicode or '' for c in group.pdf_character));break
        else:return None
    return ''.join(chunks)


def join_block_continuations(page,blocks):
    overrides={};records=[];removed=set()
    normalize=lambda text:re.sub(r'\s+','',text)
    for bounds,source in blocks:
        left,bottom,right,top=bounds
        if len(source)>1500 or source.count(':')>1:continue
        candidates=[p for p in page.pdf_paragraph if id(p) not in removed and p.box and
            p.layout_label!='table' and not getattr(p,'vertical',False) and
            left-1<=p.box.x and p.box.x2<=right+1 and bottom-1<=p.box.y and p.box.y2<=top+1]
        candidates.sort(key=lambda p:(-p.box.y2,p.box.x))
        if len(candidates)<2:continue
        texts=[composition_text(p) for p in candidates]
        if any(t is None for t in texts) or normalize(''.join(texts))!=normalize(source):continue
        # Only rescue a short continuation inside this same proven PDF block.
        # Preserve separate complete sentences, fields, tables and headings.
        if not any(len(t.split())<=4 and t.strip()[:1].islower() for t in texts[1:]):continue
        if any(t.rstrip().endswith(('.', '!', '?', ':')) for t in texts[:-1]):continue
        first=candidates[0]
        for following in candidates[1:]:
            first.pdf_paragraph_composition.extend(deepcopy(following.pdf_paragraph_composition))
            first.box.x=min(first.box.x,following.box.x);first.box.x2=max(first.box.x2,following.box.x2)
            first.box.y=min(first.box.y,following.box.y);first.box.y2=max(first.box.y2,following.box.y2)
            removed.add(id(following))
        combined=re.sub(r'\s+',' ',source).strip()
        first.unicode=combined;overrides[id(first)]=combined
        records.append({'page':page.page_number,'parent':first.debug_id,
            'source_region_ids':[p.debug_id for p in candidates],
            'basis':'same_source_pdf_block_exact_character_inventory','source_text':combined})
    page.pdf_paragraph[:]=[p for p in page.pdf_paragraph if id(p) not in removed]
    return overrides,records
