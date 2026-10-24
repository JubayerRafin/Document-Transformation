import json
import unittest
from unittest.mock import patch
from transformation.llm.ollama_native import translate_paragraph


class StructuredTranslationTests(unittest.TestCase):
    def test_field_value_interpreted_independently_before_translation(self):
        responses=iter([('{"value_kind":"professional position"}',{'prompt_eval_count':8,'eval_count':4}),
                        ('{"label":"Position","value":"Research Engineer"}',{'prompt_eval_count':10,'eval_count':5})])
        def reply(endpoint,model,messages):
            if 'value_kind' in messages[0]['content']:
                self.assertEqual(messages[1]['content'],'Research Engineer')
            else:
                self.assertIn('professional position',json.loads(messages[1]['content'])['context'])
            return next(responses)
        with patch('transformation.llm.ollama_native.chat',side_effect=reply):
            text,stats=translate_paragraph('unused','model','Title: Research Engineer','English',
                structured=True,interpret_field=True)
        self.assertEqual(text,'Position: Research Engineer')
        self.assertEqual(stats['prompt_eval_count'],18)

    def test_timestamp_placeholder_retains_temporal_type(self):
        def reply(endpoint,model,messages):
            self.assertIn('timestamp (a time, not a location)',messages[0]['content'])
            source=json.loads(messages[1]['content'])['source']
            return json.dumps({'meaning':'recorded time','translation':source}),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=reply):
            output,_=translate_paragraph('unused','model','Recorded 2025-04-12 16:27 UTC','German',structured=True)
        self.assertEqual(output,'Recorded 2025-04-12 16:27 UTC')

    def test_scope_context_uses_structure_not_document_vocabulary(self):
        from transformation.llm.field_context import needs_scope_context, infer_field_context
        self.assertTrue(needs_scope_context('Permit VIS-9084'))
        self.assertTrue(needs_scope_context('registered only.'))
        self.assertFalse(needs_scope_context('Restricted access area'))
        self.assertFalse(needs_scope_context('VIS-9084'))
        self.assertFalse(needs_scope_context('Recorded 2025-04-12 16:27 UTC'))
        with patch('transformation.llm.ollama_native.chat') as call:
            _,report=infer_field_context('Column: INV-982; INV-983','unused','model')
            self.assertEqual(report['value_kind'],'identifier')
            self.assertEqual(report['review_status'],'structural_type')
            call.assert_not_called()

    def test_clock_is_not_a_field_separator(self):
        from transformation.llm.field_context import split_field
        self.assertIsNone(split_field('Captured 2025-04-12 16:27 UTC'))
        self.assertEqual(split_field('Role: Research Engineer IV'),('Role','Research Engineer IV'))
        self.assertEqual(split_field('Recorded at: 2025-04-12 16:27 UTC'),('Recorded at','2025-04-12 16:27 UTC'))

    def test_inferred_person_completes_an_elliptical_restriction(self):
        from transformation.llm.field_context import infer_scope
        response={'scope_hint':'The restriction concerns registered visitors.','actor':'visitor','actor_kind':'person',
                  'domain':'entry','omitted_subject':True,'resolved_source':'registered only by a visitor'}
        with patch('transformation.llm.ollama_native.chat',return_value=(json.dumps(response),{})):
            _,report=infer_scope('registered only.',[{'text':'Visitor entrance.'}],'unused','model')
        self.assertEqual(report['resolved_source'],'registered people only.')
        self.assertNotIn('by',report['resolved_source'])

    def test_value_interpretation_excludes_ambiguous_label(self):
        from transformation.llm.field_context import infer_field_context
        with patch('transformation.llm.ollama_native.chat',return_value=('{"value_kind":"professional role"}',{})) as call:
            context,report=infer_field_context('Role: Research Engineer IV','unused','model')
        self.assertEqual(call.call_args.args[2][1]['content'],'Research Engineer IV')
        self.assertIn('professional role',context)
        self.assertEqual(report['review_status'],'model_inferred')
        with patch('transformation.llm.ollama_native.chat') as call:
            self.assertEqual(infer_field_context('Standalone header','unused','model'),(None,None))
            call.assert_not_called()

    def test_field_structure_and_roman_placeholder_roundtrip(self):
        def reply(endpoint,model,messages):
            source=json.loads(messages[1]['content'])
            self.assertIn('field',source)
            self.assertNotIn(' IV',source['field']['value'])
            return json.dumps({'label':'Translated role','value':source['field']['value']}),{}
        with patch('transformation.llm.ollama_native.chat',side_effect=reply):
            output,_=translate_paragraph('unused','model','Role: Research Engineer IV','Korean',structured=True)
        self.assertEqual(output,'Translated role: Research Engineer IV')

    def test_invalid_json_retries_and_rejects(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('invalid',{})) as call:
            with self.assertRaisesRegex(ValueError,'structured translation'):
                translate_paragraph('unused','model','Column label','German',structured=True)
            self.assertEqual(call.call_count,2)

    def test_unchanged_abbreviation_retries_without_mapping(self):
        responses=[(json.dumps({'meaning':'A code label','translation':'ABC'}),{}),
                   (json.dumps({'meaning':'A code label','translation':'Translated code (ABC)'}),{})]
        with patch('transformation.llm.ollama_native.chat',side_effect=responses) as call:
            output,_=translate_paragraph('unused','model','ABC','German',structured=True)
        self.assertEqual(output,'Translated code (ABC)')
        self.assertEqual(call.call_count,2)

    def test_missing_field_placeholder_is_not_silently_restored(self):
        with patch('transformation.llm.ollama_native.chat',return_value=(json.dumps({'label':'Role','value':'Engineer'}),{})) as call:
            with self.assertRaises(ValueError):
                translate_paragraph('unused','model','Role: Engineer III','Korean',structured=True)
            self.assertEqual(call.call_count,2)
