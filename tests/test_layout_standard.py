import unittest
import numpy as np
from tools.evaluation.layout_standard import iou, match_boxes, ssim


class LayoutMetricTests(unittest.TestCase):
    def test_iou_identity_disjoint_symmetry(self):
        self.assertEqual(iou([0,0,10,10],[0,0,10,10]),1)
        self.assertEqual(iou([0,0,10,10],[20,20,30,30]),0)
        a,b=[0,0,10,10],[5,0,15,10]
        self.assertAlmostEqual(iou(a,b),1/3)
        self.assertEqual(iou(a,b),iou(b,a))

    def test_duplicate_prediction_is_not_double_credit(self):
        box=[0,0,10,10]
        metric=match_boxes([box],[box,box],.5)
        self.assertEqual((metric['tp'],metric['fp'],metric['fn']),(1,1,0))
        self.assertEqual(metric['precision'],.5)

    def test_missing_predictions_count_as_misses(self):
        metric=match_boxes([[0,0,10,10]],[],.75)
        self.assertEqual(metric['fn'],1)
        self.assertEqual(metric['recall'],0)
        self.assertEqual(metric['f1'],0)

    def test_empty_category_is_not_perfect_score(self):
        metric=match_boxes([],[],.5)
        self.assertIsNone(metric['f1'])

    def test_ssim_detects_erased_content(self):
        image=np.zeros((30,30),dtype=np.uint8)
        blank=np.full((30,30),255,dtype=np.uint8)
        self.assertEqual(ssim(image,image)['score'],1)
        self.assertLess(ssim(image,blank)['score'],.1)

    def test_ssim_does_not_resize_or_score_tiny_crops(self):
        self.assertIsNone(ssim(np.zeros((30,30),dtype=np.uint8),np.zeros((31,30),dtype=np.uint8))['score'])
        self.assertIsNone(ssim(np.zeros((5,5),dtype=np.uint8),np.zeros((5,5),dtype=np.uint8))['score'])
