import unittest
from types import SimpleNamespace as NS
from transformation.pdf.source_repairs import repair_page
from transformation.layout.line_breaks import mark_date_units


def para(text,x=10,x2=190,formula=None):
    return NS(unicode=text,box=NS(x=x,y=10,x2=x2,y2=20),vertical=False,
              pdf_style=NS(),debug_id=text,pdf_paragraph_composition=[NS(pdf_formula=formula)])


class SourceRepairs(unittest.TestCase):
    def test_numeric_formula_suffix_is_recovered(self):
        f=NS(pdf_curve=[],pdf_form=[],pdf_character=[NS(char_unicode=c) for c in '0093 |'])
        a,b=para('Document ID: SEC-2024-',10,95),para('0093 | Internal',96,190,formula=f)
        page=NS(page_number=0,pdf_paragraph=[a,b])
        fixed,_=repair_page(page,[((10,10,190,20),'Document ID: SEC-2024-0093 | Internal')],['SEC-2024-0093'])
        self.assertEqual(len(fixed),1)
        self.assertEqual(len(page.pdf_paragraph),1)

    def test_underscores_recovered_from_source(self):
        p=para('API key sk live abc KEY. _ _ _')
        page=NS(page_number=0,pdf_paragraph=[p])
        source='API key sk_live_abc_KEY.'
        fixed,records=repair_page(page,[((10,10,190,20),source)],['sk_live_abc_KEY'])
        self.assertEqual(fixed[id(p)],source)
        self.assertEqual(records[0]['status'],'repaired')

    def test_fragmented_header_is_merged(self):
        a,b=para('Document ID: SEC-2024-',10,95),para('0093 | Internal',96,190)
        page=NS(page_number=0,pdf_paragraph=[a,b])
        source='Document ID: SEC-2024-0093 | Internal'
        fixed,_=repair_page(page,[((10,10,190,20),source)],['SEC-2024-0093'])
        self.assertEqual(len(page.pdf_paragraph),1)
        self.assertEqual(next(iter(fixed.values())),source)

    def test_mismatch_does_not_overwrite(self):
        p=para('Different content')
        page=NS(page_number=0,pdf_paragraph=[p])
        fixed,records=repair_page(page,[((10,10,190,20),'API sk_live_KEY')],['sk_live_KEY'])
        self.assertFalse(fixed)
        self.assertEqual(p.unicode,'Different content')
        self.assertEqual(records[0]['status'],'requires_review')

    def test_real_formula_not_flattened(self):
        f=NS(pdf_curve=[],pdf_form=[],pdf_character=[NS(char_unicode='x')])
        p=para('API sk live KEY x',formula=f)
        page=NS(page_number=0,pdf_paragraph=[p])
        fixed,_=repair_page(page,[((10,10,190,20),'API sk_live_KEY x')],['sk_live_KEY'])
        self.assertFalse(fixed)

    def test_identifier_breaks_marked(self):
        value='sk_live_abc_KEY'
        units=[NS(try_get_unicode=lambda c=c:c,can_break_line_cache=None) for c in value]
        self.assertTrue(mark_date_units(units,[value]))
        self.assertTrue(all(u.can_break_line_cache is False for u in units))
