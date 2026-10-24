import tempfile
import unittest
from pathlib import Path
import pymupdf
from transformation.validation.redaction import redact_selected
from transformation.office.office import to_pdf
from transformation.analysis.structure import structure_regions


class ProductExtensionTests(unittest.TestCase):
    def test_selected_redaction_removes_text_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.pdf'
            target=Path(folder)/'redacted.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((30,30),'Contact: test@example.org')
                pdf.save(source)
            before=source.read_bytes()
            report=redact_selected(source,target,['test@example.org'])
            self.assertEqual(report['matched_regions'],1)
            self.assertEqual(source.read_bytes(),before)
            with pymupdf.open(target) as pdf:
                self.assertNotIn('test@example.org',pdf[0].get_text())
                self.assertIn('Contact',pdf[0].get_text())
            missing=Path(folder)/'missing.pdf'
            with self.assertRaises(ValueError):
                redact_selected(source,missing,['Not in document'])
            self.assertFalse(missing.exists())

    def test_office_missing_dependency_and_pdf_passthrough(self):
        self.assertEqual(to_pdf(b'unchanged','source.pdf'),b'unchanged')
        with self.assertRaisesRegex(ValueError,'requires LibreOffice'):
            to_pdf(b'input','source.docx',executable='nonexistent-executable')

    def test_structure_candidates_exclude_uncertain(self):
        regions=[dict(id='a',bbox=[0,0,50,10]),dict(id='b',bbox=[60,0,100,10]),
                 dict(id='c',bbox=[0,12,50,22]),dict(id='d',bbox=[0,24,50,34],metadata={'requires_review':True})]
        links=structure_regions(regions)['relationships']
        self.assertIn(dict(type='same_row_candidate',region_ids=['a','b']),links)
        self.assertIn(dict(type='paragraph_continuation_candidate',region_ids=['a','c']),links)
        self.assertFalse(any('d' in r['region_ids'] for r in links))
