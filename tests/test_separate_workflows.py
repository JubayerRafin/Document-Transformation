import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from transformation.workflows import run_translation,run_pii,run_workflow,check_models
from transformation.llm.ollama_native import translate_paragraph
from transformation.llm.translategemma import messages


def fixture():
    with pymupdf.open() as pdf:
        page=pdf.new_page(); page.insert_text((40,60),'Contact: maya@example.org')
        return pdf.tobytes()


class WorkflowTests(unittest.TestCase):
    def test_missing_upload_is_rejected_before_creating_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'runs'
            for invalid in (None,b'', 'not bytes'):
                with self.assertRaisesRegex(ValueError,'No document bytes'):
                    list(run_translation(invalid,'sample.pdf',root=root))
                self.assertFalse(root.exists())

    def test_babeldoc_formula_field_source_roundtrips_and_folds_layout(self):
        from transformation.llm.protected_text import protect_values,restore_values
        from transformation.llm.translategemma import normalize_field_layout
        source='Document ID: ZZ-2031-{v12}Classification: Internal'
        safe,tokens=protect_values(source)
        self.assertIn('{v12}',tokens.values())
        self.assertEqual(restore_values(safe,tokens),source)
        target='문서 ID: ZZ-2031-{v12}\n분류: 내부'
        self.assertEqual(normalize_field_layout(source,target),'문서 ID: ZZ-2031-{v12} 분류: 내부')

    def test_whole_automatic_names_and_ids_do_not_request_contextual_translation(self):
        with patch('transformation.llm.ollama_native.chat') as chat:
            for text in ['EMP-2031-0912','Maya Min-jun','{v19}']:
                value,_=translate_paragraph('unused','translategemma:12b',text,'Korean')
                self.assertEqual(value,text)
            chat.assert_not_called()

    def test_multiline_field_layout_normalization_retains_text_and_rejects_extra_fields(self):
        from transformation.llm.translategemma import normalize_field_layout
        source='ID: ZZ-091 | Classification: Internal'
        self.assertEqual(normalize_field_layout(source,'ID: ZZ-091\n분류: 내부'),'ID: ZZ-091 | 분류: 내부')
        self.assertEqual(normalize_field_layout(source,'ID: ZZ-091 |\n분류: 내부'),'ID: ZZ-091 | 분류: 내부')
        extra='ID: ZZ-091\n분류: 내부\n설명: 새로운 내용'
        self.assertEqual(normalize_field_layout(source,extra),extra)
        self.assertEqual(normalize_field_layout('Warning','경고\n새로운 내용'),'경고\n새로운 내용')

    def test_gemma_retry_uses_original_literals_and_still_validates(self):
        prompts=[]
        def response(endpoint,model,prompt):
            prompts.append(prompt[0]['content'])
            return ('잘못된 번역' if len(prompts)==1 else '연락처: maya@example.org'),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=response):
            result,_=translate_paragraph('unused','translategemma:12b','Contact: maya@example.org','Korean')
        self.assertEqual(result,'연락처: maya@example.org')
        self.assertIn('ZXKEEP',prompts[0])
        self.assertNotIn('ZXKEEP',prompts[1])
        self.assertIn('maya@example.org',prompts[1])

    def test_gemma_plain_prose_prompt_does_not_introduce_markers(self):
        self.assertNotIn('ZXKEEP',messages('Synthetic test document.','Korean')[0]['content'])

    def test_translation_visual_analysis_uses_translation_model_without_qwen(self):
        from io import BytesIO
        payload=b'{"models":[{"name":"translategemma:12b"}]}'
        with patch('urllib.request.urlopen',side_effect=lambda *a,**kw: BytesIO(payload)):
            check_models('Translation only')
            check_models('Translation only',visual_analysis=True)

    def test_visual_pipeline_routes_selected_translation_model(self):
        with tempfile.TemporaryDirectory() as directory,patch('transformation.analysis.document_analysis.analyze_pdf',side_effect=ValueError('test stop')) as analysis:
            last=list(run_translation(fixture(),'a.pdf',root=Path(directory),visual_analysis=True,
                translation_endpoint='http://127.0.0.1:11435/v1'))[-1]
        self.assertEqual(last['status'],'failed')
        self.assertEqual(analysis.call_args.args[1:4],('http://127.0.0.1:11435/v1','translategemma:12b',True))

    def test_pii_requests_schema_with_only_selected_semantic_categories(self):
        from transformation.validation.privacy import detect
        with patch('transformation.llm.ollama_native.chat',return_value=('{"findings":[]}',{})) as call:
            detect([{'id':'r','text':'A person'}],['Names'],'unused','qwen3.5:9b')
        schema=call.call_args.kwargs['response_format']
        self.assertEqual(schema['properties']['findings']['items']['properties']['category']['enum'],['Names'])

    def test_pii_preflight_does_not_require_translation_model(self):
        from io import BytesIO
        with patch('urllib.request.urlopen',return_value=BytesIO(b'{"models":[{"name":"qwen3.5:9b"}]}')) as request:
            check_models('PII only')
            self.assertEqual(request.call_count,1)

    def test_translation_preflight_reports_missing_requested_model(self):
        from io import BytesIO
        with patch('urllib.request.urlopen',return_value=BytesIO(b'{"models":[]}')):
            with self.assertRaisesRegex(ValueError,'ollama pull translategemma:12b'): check_models('Translation only')

    def test_entrypoints_select_distinct_operations_and_models(self):
        with patch('transformation.workflows.run_pipeline',return_value=iter([])) as call:
            list(run_translation(b'x','a.pdf'))
            self.assertTrue(call.call_args.kwargs['translate']); self.assertFalse(call.call_args.kwargs['redact'])
            self.assertEqual(call.call_args.kwargs['translation_model'],'translategemma:12b')
            list(run_pii(b'x','a.pdf'))
            self.assertFalse(call.call_args.kwargs['translate']); self.assertTrue(call.call_args.kwargs['redact'])
            self.assertEqual(call.call_args.kwargs['pii_model'],'qwen3.5:9b')

    def test_pii_only_removes_source_without_calling_translation_engine(self):
        with tempfile.TemporaryDirectory() as directory,patch('transformation.pdf.document_runner.run_document') as engine:
            last=list(run_pii(fixture(),'a.pdf',pii_categories=['Email addresses'],root=Path(directory),table_strategy='native'))[-1]
            self.assertEqual(last['status'],'generated',last.get('error'))
            engine.assert_not_called()
            with pymupdf.open(last['output_pdf']) as pdf: self.assertNotIn('maya@example.org',pdf[0].get_text())
            self.assertEqual(last['workflow'],'pii')

    def test_combined_masks_before_translation_and_routes_specialists(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); engine_dir=root/'fake-engine'; engine_dir.mkdir()
            def engine(data,filename,**options):
                self.assertEqual(options['model'],'translategemma:12b')
                self.assertNotIn('pii_model',options)
                with pymupdf.open(stream=data,filetype='pdf') as pdf: self.assertNotIn('maya@example.org',pdf[0].get_text())
                self.assertNotIn('maya@example.org',json.dumps(options['external_context']))
                output=engine_dir/'output.pdf'; output.write_bytes(data)
                yield {'status':'generated','run_dir':str(engine_dir),'output_pdf':str(output)}
            with patch('transformation.pdf.document_runner.run_document',side_effect=engine):
                last=list(run_workflow(fixture(),'a.pdf','Translation + PII',pii_categories=['Email addresses'],root=root/'jobs',table_strategy='native'))[-1]
            self.assertEqual(last['status'],'generated',last.get('error'))
            self.assertEqual(last['models']['pii']['model'],'qwen3.5:9b')

    def test_gemma_uses_translation_template_and_exact_protection(self):
        def response(endpoint,model,prompt):
            self.assertEqual(model,'translategemma:12b'); self.assertEqual(len(prompt),1)
            self.assertEqual(prompt[0]['role'],'user')
            source=prompt[0]['content'].split('\n\n\n',1)[1]
            return source,{}
        with patch('transformation.llm.ollama_native.chat',side_effect=response):
            result,_=translate_paragraph('unused','translategemma:12b','Contact Maya Chen at maya@example.org','Korean',['Maya Chen'])
        self.assertEqual(result,'Contact Maya Chen at maya@example.org')

    def test_gemma_rejects_missing_placeholders(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('Translation without original identifier',{})) as call:
            with self.assertRaises(ValueError):
                translate_paragraph('unused','translategemma:12b','Contact maya@example.org','Korean')
            self.assertEqual(call.call_count,4)

    def test_gemma_explicit_source_and_no_silent_long_input_truncation(self):
        prompt=messages('Guten Tag','Korean',source='de')[0]['content']
        self.assertIn('German (de) to Korean (ko)',prompt)
        with self.assertRaisesRegex(ValueError,'split it upstream'): messages('x'*1501,'Korean')


if __name__=='__main__': unittest.main()
