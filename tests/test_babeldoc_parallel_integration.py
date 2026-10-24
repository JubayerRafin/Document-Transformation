"""Run in the pinned BabelDOC environment; no Ollama/model requests."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace as NS,MethodType
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('babeldoc'),'Pinned BabelDOC environment required')
class BabelDOCParallelTests(unittest.TestCase):
    def test_real_vendor_method_preserves_paragraph_order_and_mapping(self):
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraph,Page,Box
        from babeldoc.translator.translator import OpenAITranslator
        from transformation.pdf.translation_concurrency import install
        # Subclasses keep this test's hooks separate from other vendor tests.
        class Translator(ILTranslator):pass
        class Engine(OpenAITranslator):pass
        install(Translator,Engine)
        engine=object.__new__(Engine);engine.ignore_cache=True
        engine.translate_call_count=0;engine.translate_cache_call_count=0
        barrier=Barrier(2)
        def translate(self,text,*args,**kwargs):barrier.wait(timeout=3);return 'translated '+text
        engine.do_translate=MethodType(translate,engine)
        translator=object.__new__(Translator)
        translator.use_as_fallback=False;translator.support_llm_translate=False
        translator.translation_config=NS(raise_if_cancelled=lambda:None)
        translator.translate_engine=engine
        translator.pre_translate_paragraph=MethodType(lambda self,p,*args:(p.unicode,None),translator)
        translator.post_translate_paragraph=MethodType(lambda self,p,t,i,value:setattr(p,'unicode',value),translator)
        paragraphs=[PdfParagraph(debug_id=str(i),unicode='source '+str(i),box=Box(x=i,y=0,x2=i+1,y2=1)) for i in range(2)]
        page=Page(page_number=0,pdf_paragraph=paragraphs)
        tracker=lambda:NS(new_llm_translate_tracker=lambda:NS())
        with patch('babeldoc.translator.translator._translate_rate_limiter.wait'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(translator.translate_paragraph,p,page,pbar=NS(advance=lambda:None),tracker=tracker()) for p in paragraphs]
                for f in futures:f.result()
        self.assertEqual([p.unicode for p in page.pdf_paragraph],['translated source 0','translated source 1'])
        self.assertEqual([p.debug_id for p in page.pdf_paragraph],['0','1'])
        self.assertEqual([p.box.x for p in page.pdf_paragraph],[0,1])
