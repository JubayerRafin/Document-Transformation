import json
import unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from extraction.models import Document,Page,Region
from extraction.source_cache import extract
from tests.temp_workspace import temporary_workspace


class CacheTests(unittest.TestCase):
    def test_pipeline_cache_option_stays_out_of_pdf_worker_arguments(self):
        from transformation.pipeline import run_pipeline
        with temporary_workspace() as root:
            source,doc=self.fixture(root)
            def engine(data,name,**options):
                folder=Path(options['root'])/'fixture';folder.mkdir(parents=True)
                output=folder/'output.pdf';output.write_bytes(data)
                yield {'status':'generated','run_dir':str(folder),'output_pdf':str(output)}
            with patch('extraction.source_cache.extract',return_value=(doc,{'status':'hit'})) as cache,\
                 patch('extraction.figures.enrich_figures'),\
                 patch('transformation.pdf.document_runner.run_document',side_effect=engine) as worker:
                result=list(run_pipeline(source.read_bytes(),'unseen.pdf',root=root/'runs',table_strategy='docling',
                    source_extraction_cache=root/'cache',translation_model='translategemma:12b',source='en',target='ko'))[-1]
            self.assertEqual(result['status'],'generated',result.get('error'))
            self.assertEqual(result['source_extraction_cache']['status'],'hit')
            cache.assert_called_once()
            self.assertNotIn('source_extraction_cache',worker.call_args.kwargs)

    def fixture(self,root):
        source=root/'unseen.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page(width=200,height=100).insert_text((20,40),'Invoice 481')
            pdf.save(source)
        import hashlib
        document=Document(source.name,hashlib.sha256(source.read_bytes()).hexdigest(),'deep-doc','1',
            pages=[Page(1,200,100,0,regions=[Region('a','text',[20,20,120,50],'Invoice 481')])])
        return source,document

    def test_hit_skips_extractor_and_returns_independent_document(self):
        with temporary_workspace() as root:
            source,doc=self.fixture(root)
            with patch('extraction.coordinator.extract_pdf',return_value=doc) as worker:
                first,a=extract(source,root/'one','docling',{},root/'cache')
                first.pages[0].regions[0].text='later OCR mutation'
                second,b=extract(source,root/'two','docling',{},root/'cache')
            worker.assert_called_once()
            self.assertEqual((a['status'],b['status']),('miss','hit'))
            self.assertEqual(second.pages[0].regions[0].text,'Invoice 481')
            self.assertFalse(b['quality_approval_reused'])

    def test_source_strategy_or_analysis_change_is_not_a_hit(self):
        with temporary_workspace() as root:
            source,doc=self.fixture(root)
            with patch('extraction.coordinator.extract_pdf',return_value=doc) as worker:
                for strategy,analysis in [('docling',{}),('native',{}),('docling',{'visual_pages':[]})]:
                    _,status=extract(source,root/'out',strategy,analysis,root/'cache')
                    self.assertEqual(status['status'],'miss')
                source.write_bytes(source.read_bytes()+b'\n% changed source')
                import hashlib
                doc.source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()
                _,status=extract(source,root/'out','docling',{},root/'cache')
                self.assertEqual(status['status'],'miss')
            self.assertEqual(worker.call_count,4)

    def test_corrupt_cache_is_recomputed(self):
        with temporary_workspace() as root:
            source,doc=self.fixture(root)
            with patch('extraction.coordinator.extract_pdf',return_value=doc) as worker:
                extract(source,root/'out','docling',{},root/'cache')
                item=next((root/'cache').rglob('document.json'));payload=json.loads(item.read_text())
                payload['document']['pages'][0]['regions'][0]['text']='corrupted'
                item.write_text(json.dumps(payload))
                result,status=extract(source,root/'out','docling',{},root/'cache')
                self.assertEqual(status['status'],'miss')
                self.assertEqual(result.pages[0].regions[0].text,'Invoice 481')
                self.assertEqual(worker.call_count,2)

    def test_external_assets_are_not_published(self):
        with temporary_workspace() as root:
            source,doc=self.fixture(root);doc.pages[0].regions[0].asset='outside.png'
            with patch('extraction.coordinator.extract_pdf',return_value=doc):
                _,status=extract(source,root/'out','docling',{},root/'cache')
                self.assertEqual(status['status'],'bypass_not_published')
            self.assertFalse(list((root/'cache').rglob('document.json')))
