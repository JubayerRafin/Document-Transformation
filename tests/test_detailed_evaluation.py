import unittest
import hashlib,json,tempfile
from pathlib import Path
from copy import deepcopy
from tests.temp_workspace import temporary_workspace
from tools.evaluation.detailed import edit_counts, overlapping_scores, score_review,make_reviews,evaluate


def fixture():
    source={'units':[{'regions':[{'id':'r1','text':'Contact Maya','kind':'text'}]}]}
    review={'regions':[{'region_id':'r1','extracted_text':'Contact Maya','source_reference':'Contact Maya',
        'source_reviewed':False,'translation':'연락처 Maya','translation_reviewed':False,
        'reference_translation':None,'errors':[],'protected_literals':['Maya'],'pii':[]}],
        'source_coverage_reviewed':False,'missing_source_regions':[],
        'pii_ground_truth_reviewed':False,'pii_selected_categories':['Names'],
        'visual_reviewed':False,'layout_issues':[], 'privacy_reviewed':False,
        'privacy_residuals':[],'over_redactions':[],'human_accepted':False}
    return review,source


class DetailedTests(unittest.TestCase):
    def test_bundle_report_and_output_provenance_end_to_end(self):
        import pymupdf
        from tools.evaluation.dataset import prepare
        with temporary_workspace() as directory:
            root=Path(directory); source=root/'source.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page();page.insert_text((40,60),'Contact Maya');source.write_bytes(pdf.tobytes())
            config={'output_root':str(root/'batch'),'targets':['ko'],'documents':[{'id':'test','path':str(source)}],'pii_categories':['Names']}
            document=prepare(config)[0]
            run=root/'batch/translation/run';run.mkdir(parents=True)
            review,ir=fixture();ir_path=run/'source-ir.json';ir_path.write_text(json.dumps(ir))
            output=run/'output.pdf';output.write_bytes(source.read_bytes())
            mapping=[{'region_id':'r1','translated_text':'연락처 Maya','association':'spatial_and_text_checked'}]
            (run/'ir-translations.json').write_text(json.dumps(mapping),encoding='utf-8')
            manifest=run/'manifest.json'
            manifest.write_text(json.dumps({'source_ir':str(ir_path),'source_sha256':document['sha256'],'status':'generated','seconds':1,'output_file':str(output)}))
            (root/'batch/translation/index.json').write_text(json.dumps({'records':[{'document_id':'test','target':'ko','manifest':str(manifest),'source_sha256':document['sha256'],'gpu_verified':True}]}))
            make_reviews(config,'translation')
            rows=evaluate(config,'translation')
            self.assertEqual(rows[0]['details']['translation']['aligned_regions'],1)
            self.assertIsNone(rows[0]['details']['ocr']['cer'])
            self.assertEqual(rows[0]['acceptance'],'requires_review')
            ground=root/'batch/ground-truth/test.ko.json'
            original=ground.read_bytes();make_reviews(config,'translation');self.assertEqual(original,ground.read_bytes())
            output.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'output changed'):evaluate(config,'translation')

    def test_character_counts_include_case_and_empty_reference(self):
        self.assertEqual(edit_counts('JPEG','IPEG')['rate'],.25)
        self.assertEqual(edit_counts('a','aX')['insertions'],1)
        self.assertEqual(edit_counts('ab','a')['deletions'],1)
        self.assertIsNone(edit_counts('', 'X')['rate'])

    def test_overlap_uses_one_to_one_matching(self):
        gold=[{'region_id':'r','start':0,'end':4,'category':'Names'},
              {'region_id':'r','start':5,'end':9,'category':'Names'}]
        findings=[{'region_id':'r','spans':[[0,9]],'category':'Names'}]
        scores=overlapping_scores(gold,findings)
        self.assertEqual((scores['tp'],scores['fn']),(1,1))

    def test_unreviewed_documents_never_receive_ocr_or_pii_accuracy(self):
        review,source=fixture()
        result,_=score_review(review,source,phase='pii')
        self.assertIsNone(result['ocr']['cer']); self.assertIsNone(result['pii'])
        self.assertEqual(result['acceptance'],'requires_review')

    def test_invalid_pii_offsets_are_rejected(self):
        review,source=fixture(); review['regions'][0]['pii']=[{'start':0,'end':4,'value':'Maya','category':'Names'}]
        with self.assertRaisesRegex(ValueError,'Invalid ground-truth'): score_review(review,source,phase='pii')

    def test_stale_extraction_and_duplicates_are_rejected(self):
        review,source=fixture(); review['regions'][0]['extracted_text']='Changed'
        with self.assertRaisesRegex(ValueError,'stale'): score_review(review,source)
        review,source=fixture(); review['regions'].append(deepcopy(review['regions'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'): score_review(review,source)

    def test_acceptance_requires_full_review_and_no_major_errors(self):
        review,source=fixture()
        review.update(source_coverage_reviewed=True,visual_reviewed=True,human_accepted=True)
        review['regions'][0].update(source_reviewed=True,translation_reviewed=True)
        self.assertEqual(score_review(review,source)[0]['acceptance'],'human_accepted')
        review['regions'][0]['errors']=[{'category':'accuracy','severity':'major'}]
        self.assertEqual(score_review(review,source)[0]['acceptance'],'requires_revision')


if __name__=='__main__': unittest.main()
