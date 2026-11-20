import tempfile
import unittest
from unittest.mock import patch

from extraction.deep_doc_adapter import DoclingAdapter


class SharedDoclingTests(unittest.TestCase):
    def test_pages_come_from_the_shared_models_when_they_answer(self):
        reply = {'page_results': [{'page': 1, 'status': 'completed', 'tables': 1}], 'errors': [],
                 'elements': [dict(page_num=1, y_coordinate=10.0, element_type='table', content='{}',
                                   bbox=[1, 2, 3, 4], matrix=[['a']], cell_records=[], source_engine='docling')]}
        with patch('extraction.docling_service.ping', return_value=True), \
             patch('extraction.docling_service.extract_page', return_value=reply), \
             patch('extraction.deep_doc_adapter._cached_docling_converter', side_effect=AssertionError('no local models')):
            adapter = DoclingAdapter({}, tempfile.mkdtemp())
            elements = adapter.extract_page('doc.pdf', 1)
        self.assertEqual([(e.page_num, e.element_type, e.bbox, e.source_engine) for e in elements],
                         [(1, 'table', [1, 2, 3, 4], 'docling')])
        self.assertEqual(adapter.page_results[0]['status'], 'completed')
        self.assertTrue(adapter.converter_timing['shared_service'])

    def test_a_failing_service_falls_back_to_loading_the_models_in_the_job(self):
        with patch('extraction.docling_service.ping', return_value=True), \
             patch('extraction.docling_service.extract_page', side_effect=ConnectionRefusedError()), \
             patch('extraction.deep_doc_adapter._cached_docling_converter', side_effect=ImportError('no docling here')):
            adapter = DoclingAdapter({}, tempfile.mkdtemp())
            self.assertTrue(adapter.available)
            self.assertEqual(adapter.extract_page('doc.pdf', 1), [])
        self.assertFalse(adapter._service)
        self.assertFalse(adapter.available)
        self.assertEqual(adapter.errors[0]['stage'], 'initialization')

    def test_without_a_service_the_models_load_in_the_job(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        local = MagicMock(return_value='converter')
        local.cache_info.return_value = SimpleNamespace(currsize=0)
        with patch('extraction.docling_service.ping', return_value=False),              patch('extraction.deep_doc_adapter._cached_docling_converter', local):
            adapter = DoclingAdapter({}, tempfile.mkdtemp())
        self.assertTrue(adapter.available)
        self.assertFalse(adapter._service)
        self.assertEqual(adapter._converter, 'converter')

if __name__ == '__main__':
    unittest.main()
