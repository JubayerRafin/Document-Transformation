"""Bounded plain-text table fitting for BabelDOC 0.6.4."""


def fitting_scales(units,minimum):
    """Search to the actual font floor, rather than an arbitrary 75% cutoff."""
    sizes=[]
    for unit in units:
        size=getattr(unit,'font_size',None)
        if size is not None:sizes.append(size)
        char=getattr(unit,'char',None)
        formula=getattr(unit,'formular',None)
        for char in ([char] if char is not None else [])+list(getattr(formula,'pdf_character',[]) or []):
            size=getattr(getattr(char,'pdf_style',None),'font_size',None)
            if size is not None:sizes.append(size)
    # Unknown geometry is still checked after rendering; do not guess its floor.
    if not sizes or any(size<=0 for size in sizes):return (1.0,.95,.90,.85,.80,.75)
    floor=max(minimum/size for size in sizes)
    if floor>=1:return (1.0,)
    scales=[round(1-index*.05,8) for index in range(20) if 1-index*.05>floor+1e-8]
    return tuple(scales+[floor])


def characters_fit(characters, bounds, min_font_size=7.0, check_visual=False, source_corner_floors=None):
    """Check rendered character boxes and font sizes."""
    left, bottom, right, top = bounds
    tolerance = 0.01

    if not characters:
        return False

    for char in characters:
        box = char.box
        style = char.pdf_style

        if box is None or style is None or style.font_size is None:
            return False

        floor=(source_corner_floors or {}).get(getattr(char,'pdf_character_id',None),min_font_size)
        if style.font_size < floor - tolerance:
            return False

        if not (
            left - tolerance <= box.x < box.x2 <= right + tolerance
            and bottom - tolerance <= box.y < box.y2 <= top + tolerance
        ):
            return False
        visual = getattr(getattr(char, "visual_bbox", None), "box", None)
        if check_visual and visual is not None and not (
            left - tolerance <= visual.x < visual.x2 <= right + tolerance
            and bottom - tolerance <= visual.y < visual.y2 <= top + tolerance
        ):
            return False

    return True


def install_table_fitting(Typesetting):
    """Replace rendering only for paragraphs with an assigned table slot."""
    from importlib.metadata import version
    from babeldoc.format.pdf.document_il.il_version_1 import (
        Box,
        PdfParagraphComposition,
    )

    if version("babeldoc") != "0.6.4":
        raise RuntimeError("Table fitting requires BabelDOC 0.6.4.")

    if getattr(Typesetting, "_transformx_table_fitting_installed", False):
        return

    original_render = Typesetting.render_paragraph

    def render_in_slot(self, paragraph, page, fonts):
        key = (page.page_number + 1, paragraph.debug_id)
        slot = getattr(self, "_transformx_table_slots", {}).get(key)
        heading = False
        panel = False
        if slot is None:
            slot = getattr(self, "_transformx_heading_slots", {}).get(key)
            heading = slot is not None
        if slot is None:
            slot = getattr(self, "_transformx_panel_slots", {}).get(key)
            panel = slot is not None

        if slot is None:
            return original_render(self, paragraph, page, fonts)

        record = {
            "page": key[0],
            "paragraph_id": key[1],
            "text": paragraph.unicode,
            "slot": slot["bbox"],
            "status": "requires_review",
        }
        results = self._transformx_heading_fit_results if heading else self._transformx_table_fit_results
        if panel:
            results = self._transformx_panel_fit_results
        results.append(record)

        try:
            if paragraph.vertical:
                raise RuntimeError("Vertical table text needs separate handling.")

            units = self.create_typesetting_units(paragraph, fonts)

            if not units:
                raise RuntimeError("Empty table blocks require review.")
            # BabelDOC also represents grade symbols such as B+ as formulas.
            # Its layout engine relocates these characters; apply the same
            # containment/font checks below, without accepting vector graphics.
            for unit in units:
                formula = unit.formular
                if formula is not None and (
                    not formula.pdf_character or formula.pdf_curve or formula.pdf_form
                ):
                    raise RuntimeError("Non-text formulas inside a table slot require review.")

            if not any(getattr(unit,'font_size',None) or getattr(unit,'char',None) for unit in units):
                # Vendor layout takes the mode of unit font sizes, but formula
                # units do not advertise one. Seed that layout hint only from
                # their measured source glyphs; never change glyph styles.
                from statistics import median
                for unit in units:
                    sizes=[c.pdf_style.font_size for c in unit.formular.pdf_character
                           if c.pdf_style and c.pdf_style.font_size and c.pdf_style.font_size>0]
                    if not sizes:raise RuntimeError('Formula layout requires a measured source font size.')
                    unit.font_size=median(sizes)
                record['formula_layout_font_seeded_from_source']=True

            left, bottom, right, top = slot["bbox"]
            bounds=[slot['bbox']]
            cell=slot.get('single_block_cell_bbox') if not heading and not panel else None
            if cell and cell[0]<=left<right<=cell[2] and cell[1]<=bottom<top<=cell[3]:
                # Only one proven block occupies this cell. Borrow its unused
                # padding, never another block's slot or a neighboring cell.
                bounds.append([cell[0]+.25,cell[1]+.25,cell[2]-.25,cell[3]-.25])

            # Do not call BabelDOC's expanding-box fitting routine here.
            minimum=slot.get('min_font_size',10.0 if heading else 9.0 if panel else 7.0)
            corner_floors={}
            if not heading and not panel:
                # BabelDOC marks source subscripts/superscripts explicitly.
                # Preserve their actual source font size without permitting
                # smaller body text or any further shrink of the script glyph.
                for unit in units:
                    formula=unit.formular
                    if formula is None or getattr(formula,'is_corner_mark',False) is not True:
                        continue
                    for char in formula.pdf_character:
                        cid=getattr(char,'pdf_character_id',None)
                        size=getattr(getattr(char,'pdf_style',None),'font_size',None)
                        if cid is not None and size is not None and 0<size<minimum:
                            corner_floors[cid]=size
            scales=fitting_scales(units,minimum)
            record['minimum_font_size']=minimum
            if corner_floors:record['preserved_source_corner_font_sizes']=corner_floors
            record['attempts']=0
            layouts=[paragraph]
            if slot.get('source_locked_ocr') is True and getattr(paragraph,'first_line_indent',False):
                from copy import copy
                unindented=copy(paragraph);unindented.first_line_indent=False
                layouts.append(unindented)
            # Exhaust the unchanged normal path first. Only a failed, glyph-
            # verified OCR slot may retry without BabelDOC's inferred indent:
            # its measured source box already includes the source placement.
            for layout,candidate,scale in [(layout,candidate,scale) for layout in layouts for candidate in bounds for scale in scales]:
                record['attempts']+=1
                left,bottom,right,top=candidate
                bounded_box = Box(
                    x=left, y=bottom, x2=right, y2=top
                )
                placed, fits = self._layout_typesetting_units(
                    units,
                    bounded_box,
                    scale,
                    1.15 if panel else 1.3,
                    layout,
                    use_english_line_break=True,
                )

                if not fits:
                    continue

                characters = []
                unsupported_graphics = False

                for unit in placed:
                    chars, curves, forms = unit.render()
                    characters.extend(chars)
                    unsupported_graphics |= bool(curves or forms)

                if unsupported_graphics:
                    raise RuntimeError(
                        "Graphics inside a text slot require review."
                    )

                if not characters_fit(characters, candidate,
                                      min_font_size=minimum,
                                      source_corner_floors=corner_floors,
                                      check_visual=heading or panel or bool(corner_floors) or 'min_font_size' in slot or candidate!=slot['bbox']):
                    continue

                # Commit only after every rendered character passes.
                paragraph.box = bounded_box
                paragraph.scale = scale
                if layout is not paragraph:
                    paragraph.first_line_indent=False
                paragraph.pdf_paragraph_composition = [
                    PdfParagraphComposition(pdf_character=char)
                    for char in characters
                ]
                self._update_paragraph_render_order(paragraph)

                record.update(status="fitted", scale=scale,slot=candidate,
                              rendered_min_font_size=min(char.pdf_style.font_size for char in characters),
                              reused_cell_padding=candidate!=slot['bbox'],removed_inferred_indent=layout is not paragraph)
                return

            from transformation.layout.source_atoms import preserved_source_characters
            entry=getattr(self,'_transformx_source_atoms',{}).get(key)
            source_chars=preserved_source_characters(entry,paragraph,page)
            if source_chars:
                from copy import deepcopy
                paragraph.box=deepcopy(entry['paragraph'].box)
                paragraph.scale=1.0
                paragraph.pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in source_chars]
                self._update_paragraph_render_order(paragraph)
                record.update(status='preserved_source_atomic_geometry',scale=1.0,
                              source_geometry_retained=True,translation_unchanged=True,
                              rendered_min_font_size=min(c.pdf_style.font_size for c in source_chars))
                return

            raise RuntimeError(
                "Text cannot fit within its slot at the configured font limits."
            )

        except Exception as error:
            record["reason"] = str(error)
            raise RuntimeError(
                f"{'Panel' if panel else 'Heading' if heading else 'Table'} fitting requires review: page {key[0]}, "
                f"paragraph {key[1]}: {error}"
            ) from error

    Typesetting.render_paragraph = render_in_slot
    Typesetting._transformx_table_fitting_installed = True
