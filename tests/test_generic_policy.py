import unittest
from unittest.mock import patch
from transformation.llm.policy import validate_terminology, context_with_terms, nearby_context
from transformation.llm.ollama_native import translate_paragraph, paragraph_messages


class GenericPolicyTests(unittest.TestCase):
    def test_sparse_column_context_excludes_other_columns_and_uncertain(self):
        from transformation.llm.policy import column_examples
        header={'id':'h','text':'Category','bbox':[100,10,160,30]}
        value={'id':'v','text':'Equipment','bbox':[105,130,175,145]}
        unrelated={'id':'u','text':'999','bbox':[300,130,350,145]}
        uncertain={'id':'x','text':'Unknown','bbox':[105,60,175,75],'metadata':{'requires_review':True}}
        self.assertEqual([r['id'] for r in column_examples(header,[header,value,unrelated,uncertain])],['v'])

    def test_prompt_requires_header_translation_without_glossary(self):
        prompt=paragraph_messages('Category','German')[0]['content']
        self.assertIn('table column labels',prompt)
        self.assertIn('accompanying value',prompt)
        self.assertNotIn('직위',prompt)

    def test_product_names_must_occur_in_source(self):
        from transformation.llm.entities import product_names
        with patch('transformation.llm.ollama_native.chat',return_value=('{"product_names":["Orion Z8","Imagined Z9","Orion Z8"]}',{})):
            values,report=product_names('unused','model',[{'text':'Product: Orion Z8'}])
        self.assertEqual(values,['Orion Z8'])
        self.assertEqual(report['rejected_proposals'],1)
        with patch('transformation.llm.ollama_native.chat',return_value=('not JSON',{})):
            with self.assertRaises(ValueError):
                product_names('unused','model',[{'text':'Anything'}])
    def test_unchanged_values_not_prose(self):
        from transformation.llm.policy import expected_unchanged
        for text in ['USD 12,345','ABC-8394','user@example.org','2025-01-02']:
            self.assertTrue(expected_unchanged(text),text)
        for text in ['Contact user@example.org','Price USD 12,345','Title','authorized only.']:
            self.assertFalse(expected_unchanged(text),text)
    def test_labels_are_not_special_cased_in_any_language(self):
        for language in ['Korean','German','French']:
            for text in ['Title','Item','Badge ABC-123','authorized only.']:
                with patch('transformation.llm.ollama_native.chat', return_value=('model output',{})) as chat:
                    # Avoid identifier placeholders in this assertion.
                    if text.startswith('Badge'):text='Badge label'
                    self.assertEqual(translate_paragraph('unused','model',text,language,document_context='Title means job position')[0],'model output')
                    chat.assert_called_once()
            self.assertNotIn('직위',paragraph_messages('Title',language)[0]['content'])

    def test_terms_explicit_and_empty_default(self):
        self.assertEqual(context_with_terms('context',{}),'context')
        self.assertIn('Benutzer',context_with_terms('',{'User':'Benutzer'}))
        with self.assertRaises(ValueError):validate_terminology({'User':123})

    def test_context_excludes_uncertain_and_distant_columns(self):
        a={'id':'a','text':'A','bbox':[0,0,100,10]}
        b={'id':'b','text':'B','bbox':[0,12,100,22]}
        c={'id':'c','text':'C','bbox':[500,0,600,10]}
        d={'id':'d','text':'D','bbox':[0,24,100,34],'metadata':{'requires_review':True}}
        self.assertEqual([r['id'] for r in nearby_context(a,[a,b,c,d])],['b'])
