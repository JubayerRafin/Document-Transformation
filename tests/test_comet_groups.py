import json
import tempfile
import unittest
from pathlib import Path
from tools.evaluation.detailed import group_metric_inputs


class GroupMetricTests(unittest.TestCase):
    def run_group(self, target='번역 문장', source='First second', duplicate=False):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'babeldoc-transformed-ir.json'
            p.write_text(json.dumps({'page':[{'pdf_paragraph':[{'unicode':'번역 문장'}]}]}),encoding='utf-8')
            group={'group_id':'g','region_ids':['a','b'],'source':source,'observed_translation':target,'translation_reviewed':True}
            review={'regions':[{'region_id':'a','source_reference':'First','source_reviewed':True},{'region_id':'b','source_reference':'second','source_reviewed':True}],
                    'translation_groups':[group,group] if duplicate else [group]}
            return group_metric_inputs(review,directory)

    def test_full_group_is_one_metric_pair(self):
        pairs,excluded,members=self.run_group()
        self.assertEqual(pairs[0]['src'],'First second')
        self.assertEqual(len(pairs),1)
        self.assertEqual(members,{'a','b'})
        self.assertFalse(excluded)

    def test_changed_target_is_excluded(self):
        pairs,excluded,_=self.run_group(target='Invented translation')
        self.assertFalse(pairs)
        self.assertEqual(len(excluded),1)

    def test_changed_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'source does not match'): self.run_group(source='Other source')

    def test_overlapping_groups_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'overlapping'): self.run_group(duplicate=True)
