from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier,Lock
from types import SimpleNamespace as NS
import sys,time,unittest
from unittest.mock import patch
from transformation.pdf.translation_concurrency import install,_FLIGHTS
from transformation.pdf.runner import run_pdf


@dataclass
class Paragraph:
    debug_id:str
    unicode:str


class ParallelTranslationTests(unittest.TestCase):
    def test_private_paragraphs_context_and_stable_mapping(self):
        barrier=Barrier(2);context={'title':['source']}
        class Translator:
            def translate_paragraph(self,p,page,**kwargs):
                barrier.wait(timeout=3)
                kwargs['local_title_paragraph']['title'].append('private mutation')
                p.unicode='translated '+p.debug_id
        class Engine:
            def translate(self,text):return text
        install(Translator,Engine);install(Translator,Engine)
        paras=[Paragraph('alpha','original A'),Paragraph('beta','original B')]
        page=NS(pdf_paragraph=paras);translator=Translator()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(translator.translate_paragraph,p,page,local_title_paragraph=context) for p in paras]
            for f in futures:f.result()
        self.assertEqual([p.unicode for p in paras],['translated alpha','translated beta'])
        self.assertEqual([p.debug_id for p in page.pdf_paragraph],['alpha','beta'])
        self.assertEqual(context,{'title':['source']})

    def test_page_mutations_and_identifier_changes_fail_closed(self):
        from transformation.llm.ollama_native import TRANSLATION_FAILED
        class Translator:
            def translate_paragraph(self,p,page):page.pdf_paragraph.append(p)
        class Engine:
            def translate(self,text):return text
        install(Translator,Engine)
        p=Paragraph('id','original');page=NS(pdf_paragraph=[p])
        TRANSLATION_FAILED.clear()
        try:
            with self.assertRaisesRegex(RuntimeError,'page annotations'):Translator().translate_paragraph(p,page)
            self.assertTrue(TRANSLATION_FAILED.is_set());self.assertEqual(page.pdf_paragraph,[p])
            self.assertEqual(p.unicode,'original')
        finally:TRANSLATION_FAILED.clear()

    def test_duplicate_text_singleflight_reuses_existing_cache(self):
        class Translator:
            def translate_paragraph(self,*args):pass
        class Engine:
            def __init__(self):self.cache={};self.calls=0
            def translate(self,text):
                if text not in self.cache:
                    self.calls+=1;time.sleep(.01);self.cache[text]='result'
                return self.cache[text]
        install(Translator,Engine);engine=Engine()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(engine.translate,['same']*20)),['result']*20)
        self.assertEqual(engine.calls,1);self.assertFalse(_FLIGHTS)

    def test_different_text_is_not_globally_serialized(self):
        barrier=Barrier(2)
        class Translator:
            def translate_paragraph(self,*args):pass
        class Engine:
            def translate(self,text):barrier.wait(timeout=3);return text
        install(Translator,Engine);engine=Engine()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(engine.translate,['A','B'])),['A','B'])

    def test_parallel_capacity_must_be_declared(self):
        for options in [{'translation_workers':2},{'translation_workers':True},
                        {'translation_workers':3},{'ollama_parallelism':True}]:
            with self.assertRaises(ValueError):list(run_pdf(b'invalid','file.pdf',python=sys.executable,model='test',**options))
