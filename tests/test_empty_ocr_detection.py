import unittest
from tools.ocr.recognize_paddle_boxes import detector_polygons


class EmptyDetectionTests(unittest.TestCase):
    def test_empty_detection_is_a_valid_zero_box_result(self):
        self.assertEqual(detector_polygons([]).shape,(0,4,2))

    def test_malformed_nonempty_and_nonfinite_results_are_rejected(self):
        for value in (None,[[1,2]],[[[0,0],[1,0],[1,1],[float('nan'),1]]]):
            with self.assertRaises(ValueError):detector_polygons(value)

    def test_valid_quadrilateral_is_retained(self):
        value=[[[0,0],[10,0],[10,10],[0,10]]]
        self.assertEqual(detector_polygons(value).tolist(),value)


if __name__=='__main__':unittest.main()
