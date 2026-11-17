import copy
import unittest
from transformation.ocr.source_lines import group_words


class SourceWordTests(unittest.TestCase):
    def fixture(self):
        return {'units':[{'number':1,'ocr_folder':'evidence','regions':[
            {'id':'a','kind':'text','text':'Beyond','bbox':[10,10,35,20],'metadata':{}},
            {'id':'b','kind':'text','text':'borders','bbox':[37,10,68,20],'metadata':{}}]}]}

    def test_measured_word_group_keeps_original_evidence(self):
        ir=self.fixture();before=copy.deepcopy(ir);derived,groups=group_words(ir)
        self.assertEqual(ir,before);self.assertEqual(len(groups),1)
        region=derived['units'][0]['regions'][0]
        self.assertEqual(region['text'],'Beyond borders')
        self.assertEqual(region['bbox'],[10,10,68,20])
        self.assertEqual(region['metadata']['source_word_region_ids'],['a','b'])

    def test_cell_fields_and_uncertain_values_never_join(self):
        for modifier in ('cell','field','uncertain','identifier','protected'):
            ir=self.fixture();r=ir['units'][0]['regions'][0];values=()
            if modifier=='cell':r['metadata']['cell_bbox']=[0,0,100,30]
            elif modifier=='field':r['text']='Name:'
            elif modifier=='uncertain':r['metadata']['requires_review']=True
            elif modifier=='identifier':r['text']='AB-991'
            else:values=('Beyond',)
            derived,groups=group_words(ir,values)
            self.assertIs(derived,ir);self.assertEqual(groups,[])

    def test_rows_and_wide_column_gaps_never_join(self):
        for box in ([37,25,68,35],[90,10,121,20]):
            ir=self.fixture();ir['units'][0]['regions'][1]['bbox']=box
            derived,groups=group_words(ir);self.assertIs(derived,ir);self.assertEqual(groups,[])

    def test_no_new_grouping_for_native_pages(self):
        ir=self.fixture();ir['units'][0].pop('ocr_folder')
        self.assertIs(group_words(ir)[0],ir)

    def test_ambiguous_branch_does_not_choose_a_neighbor(self):
        ir=self.fixture();ir['units'][0]['regions'].append({'id':'c','kind':'text','text':'other','bbox':[37,10,66,20],'metadata':{}})
        self.assertEqual(group_words(ir)[1],[])
