"""Recover fragmented protected literals from verified selectable PDF blocks."""
from collections import Counter
import json
import os
from pathlib import Path
import re


def letters(text):
    return Counter(c for c in text if c.isalnum())


def literal_formula(formula, values):
    if formula.pdf_curve or formula.pdf_form:
        return False
    text=''.join(c.char_unicode or '' for c in formula.pdf_character)
    if not any(c.isalnum() for c in text):
        return True
    # A numeric identifier suffix can be misclassified as a formula (0093 |).
    # Accept only a literal numeric fragment present in a protected value.
    if not re.fullmatch(r'[0-9\s|_.-]+',text):
        return False
    fragment=text.strip(' |\t\r\n')
    return bool(fragment) and any(fragment in value for value in values)


def repair_page(page, blocks, protected_values):
    overrides, records = {}, []
    for bounds, text in blocks:
        values = [v for v in protected_values if v in text and re.search(r'[-_]', v)]
        if not values:
            continue
        left,bottom,right,top = bounds
        candidates = [p for p in page.pdf_paragraph if p.box and not p.vertical
                      and left-2 <= (p.box.x+p.box.x2)/2 <= right+2
                      and bottom-2 <= (p.box.y+p.box.y2)/2 <= top+2]
        if not candidates or all(any(v in (p.unicode or '') for p in candidates) for v in values):
            continue
        if letters(text) != letters(''.join(p.unicode or '' for p in candidates)):
            records.append({'page':page.page_number+1,'status':'requires_review',
                            'reason':'Source and native character inventories differ.'})
            continue
        # Only punctuation formulas may be absorbed; never flatten real maths/graphics.
        formulas = [c.pdf_formula for p in candidates for c in p.pdf_paragraph_composition if c.pdf_formula]
        if any(not literal_formula(f, values) for f in formulas):
            records.append({'page':page.page_number+1,'status':'requires_review',
                            'reason':'Unsupported formula in source block.'})
            continue
        anchor = min(candidates,key=lambda p:(-p.box.y2,p.box.x))
        if anchor.pdf_style is None:
            continue
        anchor.box.x=min(p.box.x for p in candidates)
        anchor.box.y=min(p.box.y for p in candidates)
        anchor.box.x2=max(p.box.x2 for p in candidates)
        anchor.box.y2=max(p.box.y2 for p in candidates)
        anchor.unicode=' '.join(text.split())
        overrides[id(anchor)]=anchor.unicode
        removed={id(p) for p in candidates if p is not anchor}
        page.pdf_paragraph[:]=[p for p in page.pdf_paragraph if id(p) not in removed]
        records.append({'page':page.page_number+1,'paragraph_id':anchor.debug_id,
                        'status':'repaired','reason':'verified_source_text',
                        'merged_blocks':len(candidates),'protected_values':values})
    return overrides,records


def install_source_repairs(protected_values):
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    import pymupdf
    original_translate=ILTranslator.translate
    original_input=ILTranslator.get_translate_input

    def translate(self,document):
        if getattr(self,'_transformx_locked_inputs',None):
            return original_translate(self,document)
        overrides,records={},[]
        from transformation.layout.panels import group_panels, PANEL_SLOTS
        PANEL_SLOTS.clear()
        panel_records=[];grouping_records=[]
        with pymupdf.open(self.translation_config.input_file) as pdf:
            for page in document.page:
                source=pdf[page.page_number]
                if source.rotation or source.cropbox != source.mediabox or source.mediabox.x0 or source.mediabox.y0:
                    continue
                blocks=[((b[0],source.rect.height-b[3],b[2],source.rect.height-b[1]),b[4])
                        for b in source.get_text('blocks') if b[6]==0]
                from types import SimpleNamespace
                from transformation.layout.line_breaks import join_split_dates
                date_changes=join_split_dates(SimpleNamespace(page=[page]))
                grouping_records.extend(date_changes)
                for change in date_changes:
                    parent=next(p for p in page.pdf_paragraph if p.debug_id==change['paragraph_id'])
                    overrides[id(parent)]=parent.unicode
                from transformation.pdf.source_grouping import join_block_continuations
                grouped,notes=join_block_continuations(page,blocks)
                overrides.update(grouped);grouping_records.extend(notes)
                grouped,notes=group_panels(page,source)
                overrides.update(grouped)
                panel_records.extend(notes)
                fixed,notes=repair_page(page,blocks,protected_values)
                overrides.update(fixed)
                records.extend(notes)
        self._transformx_source_overrides=overrides
        folder=os.environ.get('TRANSFORMX_RUN_DIR')
        if folder:
            Path(folder,'panel-grouping.json').write_text(json.dumps(panel_records,ensure_ascii=False,indent=2),encoding='utf-8')
            Path(folder,'source-repairs.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            Path(folder,'source-grouping.json').write_text(json.dumps(grouping_records,ensure_ascii=False,indent=2),encoding='utf-8')
            from dataclasses import asdict
            Path(folder,'babeldoc-source-ir.json').write_text(json.dumps(asdict(document),ensure_ascii=False),encoding='utf-8')
            external=os.environ.get('TRANSFORMX_EXTERNAL_IR_FILE')
            if external:
                from transformation.pdf.ir_bridge import map_document
                Path(folder,'ir-mapping.json').write_text(json.dumps(map_document(document,json.loads(Path(external).read_text(encoding='utf-8'))),ensure_ascii=False),encoding='utf-8')
        return original_translate(self,document)

    def get_input(self,paragraph,*args,**kwargs):
        locked=getattr(self,'_transformx_locked_inputs',{}).get(id(paragraph))
        if locked is not None: return self.TranslateInput(locked,[],paragraph.pdf_style)
        text=getattr(self,'_transformx_source_overrides',{}).get(id(paragraph))
        if text is not None:
            return self.TranslateInput(text,[],paragraph.pdf_style)
        return original_input(self,paragraph,*args,**kwargs)

    ILTranslator.translate=translate
    ILTranslator.get_translate_input=get_input
