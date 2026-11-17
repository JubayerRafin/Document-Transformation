import json
import unittest
from tests.temp_workspace import temporary_workspace
from tools.evaluation.ocr_ground_truth import prepare,evaluate,distance


class OCRReferenceTests(unittest.TestCase):
    def fixture(self,root):
        report=root/'evidence.json';annotations=root/'references.json'
        report.write_text(json.dumps({'documents':[{'document':'arbitrary-document','source_sha256':'a'*64,'after':[
            {'asset':'figure','transcripts':[dict(id='a',text='Code VX-19',bbox=[0,0,100,10],requires_review=True),
                                            dict(id='b',text='A B',bbox=[0,20,100,30],requires_review=False)]}]}]}))
        prepare(report,annotations)
        return report,annotations

    def test_unreviewed_or_unattributed_references_cannot_produce_scores(self):
        with temporary_workspace() as root:
            report,annotations=self.fixture(root)
            data=json.loads(annotations.read_text())
            self.assertIsNone(data['lines'][0]['reference'])
            with self.assertRaisesRegex(ValueError,'reference review'):evaluate(report,annotations)
            with self.assertRaises(FileExistsError):prepare(report,annotations)

    def test_exact_cer_wer_and_no_score_as_accuracy_percentage(self):
        with temporary_workspace() as root:
            report,annotations=self.fixture(root);data=json.loads(annotations.read_text())
            data.update(reviewed=True,reviewer='Test reviewer')
            for row,text in zip(data['lines'],['Code VX-18','A C']):row.update(reference=text,reviewed=True)
            annotations.write_text(json.dumps(data))
            result=evaluate(report,annotations)
            self.assertEqual(result['character_edits'],2)
            self.assertEqual(result['reference_characters'],13)
            self.assertEqual(result['cer'],2/13)
            self.assertEqual(result['wer'],.5)
            self.assertNotIn('accuracy',result)
            updated=json.loads(report.read_text())
            updated['documents'][0]['after'][0]['transcripts'][0]['text']='Code VX-18'
            report.write_text(json.dumps(updated))
            self.assertEqual(evaluate(report,annotations)['character_edits'],1)
            source=json.loads(report.read_text());source['documents'][0]['source_sha256']='b'*64
            report.write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError,'evidence changed'):evaluate(report,annotations)

    def test_unicode_punctuation_case_and_insertions_are_not_normalized_away(self):
        self.assertEqual(distance('VX-18','Vx18'),2)
        self.assertEqual(distance('', 'noise'),5)
