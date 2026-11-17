import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from transformation.layout.line_breaks import mark_date_units, widen_page_footers, install_date_line_breaks, join_split_dates, heading_slots
from transformation.llm.ollama_native import translate_paragraph


def units(text, split=False):
    return [NS(try_get_unicode=lambda c=c: c, can_break_line_cache=None,
               box=NS(y=10 if not split or i < 8 else 0)) for i,c in enumerate(text)]


class LineBreakTests(unittest.TestCase):
    def test_heading_expands_right_without_moving_down(self):
        change={'page':0,'paragraph_id':'h','reason':'text_above_image_shift','after':[72,414,194,430]}
        page=NS(page_number=0,cropbox=NS(box=NS(x2=612)),pdf_paragraph=[])
        slot=heading_slots(NS(page=[page]),[change])[(1,'h')]['bbox']
        self.assertEqual(slot,[72,414,316,430])
        page.pdf_paragraph=[NS(debug_id='neighbor',box=NS(x=240,y=413,x2=300,y2=440))]
        self.assertEqual(heading_slots(NS(page=[page]),[change])[(1,'h')]['bbox'],[72,414,238,430])

    def test_heading_respects_page_and_image_boundaries(self):
        change={'page':0,'paragraph_id':'h','reason':'text_above_image_shift','after':[72,414,194,430]}
        page=NS(page_number=0,cropbox=NS(box=NS(x2=250)),pdf_paragraph=[])
        self.assertEqual(heading_slots(NS(page=[page]),[change])[(1,'h')]['bbox'][2],248)
        images={0:[NS(box=NS(x=215,y=410,x2=250,y2=450))]}
        self.assertEqual(heading_slots(NS(page=[page]),[change],images)[(1,'h')]['bbox'][2],213)

    def test_adjacent_date_continuation_joins_without_losing_text(self):
        p=NS(unicode='Approval date: 2024-03-',box=NS(x=72,y=100,x2=500,y2=110),
             layout_label='text',debug_id='parent',pdf_paragraph_composition=[
                 NS(pdf_same_style_unicode_characters=NS(unicode='Approval date: 2024-03-'))])
        q=NS(unicode='20',box=NS(x=72,y=88,x2=82,y2=98),layout_label='text',debug_id='tail')
        page=NS(page_number=1,pdf_paragraph=[p,q])
        self.assertEqual(len(join_split_dates(NS(page=[page]))),1)
        self.assertEqual(p.unicode,'Approval date: 2024-03-20')
        self.assertEqual(p.pdf_paragraph_composition[0].pdf_same_style_unicode_characters.unicode,p.unicode)
        self.assertEqual(page.pdf_paragraph,[p])

    def test_invalid_date_or_table_does_not_join(self):
        for label,month in [('text','02'),('table','03')]:
            p=NS(unicode=f'Date: 2024-{month}-',box=NS(x=72,y=100,x2=500,y2=110),layout_label=label)
            q=NS(unicode='31',box=NS(x=72,y=88,x2=82,y2=98),layout_label='text')
            page=NS(page_number=1,pdf_paragraph=[p,q])
            self.assertEqual(join_split_dates(NS(page=[page])),[])
            self.assertEqual(len(page.pdf_paragraph),2)

    def test_date_marks_hyphens_nonbreaking(self):
        values=units('2024-03-20')
        self.assertTrue(mark_date_units(values))
        self.assertTrue(all(u.can_break_line_cache is False for u in values))
        self.assertFalse(mark_date_units(units('ordinary text')))

    def test_split_date_rejected_even_in_fallback(self):
        class Layout:
            def _layout_typesetting_units(self, values, *args, **kwargs):
                self.english=kwargs['use_english_line_break']
                return values, True
        install_date_line_breaks(Layout)
        obj=Layout()
        _, fits=obj._layout_typesetting_units(units('2024-03-20',True),None,1,1,None,False)
        self.assertFalse(fits)
        self.assertTrue(obj.english)
        _, fits=obj._layout_typesetting_units(units('2024-03-20'),None,1,1,None)
        self.assertTrue(fits)

    def test_footer_only_expands_in_clear_space(self):
        p=NS(box=NS(x=290,y=20,x2=310,y2=30),unicode='제 1 페이지',debug_id='footer')
        page=NS(page_number=0,cropbox=NS(box=NS(x=0,y=0,x2=600,y2=800)),pdf_paragraph=[p])
        self.assertEqual(len(widen_page_footers(NS(page=[page]))),1)
        self.assertEqual(p.box.x2-p.box.x,72)
        p.box.x,p.box.x2=290,310
        page.pdf_paragraph.append(NS(box=NS(x=320,y=20,x2=350,y2=30),unicode='nearby text'))
        self.assertEqual(widen_page_footers(NS(page=[page])),[])

    def test_context_does_not_trigger_hidden_substitution(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('translated',{})) as chat:
            result,_=translate_paragraph('unused','model','Title','Korean',document_context='"Title" means job position.')
            self.assertEqual(result,'translated')
            chat.assert_called_once()
            translate_paragraph('unused','model','Title','Korean',document_context='Document title.')
            translate_paragraph('unused','model','Title','German',document_context='"Title" means job position.')
            self.assertEqual(chat.call_count,3)
