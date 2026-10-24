import unittest
import hashlib
import numpy as np
from transformation.pdf.scan_reconstruction import background, rule_pixels, validate_corrections

class ScanReconstructionTests(unittest.TestCase):
    def test_rule_restore_does_not_restore_adjacent_letter_fragment(self):
        image = np.full((150,150,3),255,dtype=np.uint8)
        image[:,70] = 100
        image[40,69] = 0
        mask = rule_pixels(image)
        self.assertTrue(mask[10,70])
        self.assertFalse(mask[40,69])

    def test_corrections_require_exact_source_and_ocr(self):
        regions = [{'id': 'r1', 'text': 'bad OCR'}]
        item = {'id': 'r1', 'expected_ocr': 'bad OCR', 'action': 'replace', 'output_text': 'Correct text'}
        data = {'source_sha256': hashlib.sha256(b'source').hexdigest(), 'corrections': [item]}
        self.assertEqual(validate_corrections(data, b'source', regions)['r1'], item)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'other document', regions)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'source', [{'id': 'r1', 'text': 'changed OCR'}])
        data['corrections'].append(item)
        with self.assertRaises(ValueError):
            validate_corrections(data, b'source', regions)

    def test_crossing_rules_are_preserved(self):
        image = np.full((150, 150, 3), 255, dtype=np.uint8)
        image[:, 70] = 100
        image[80, :] = 100
        mask = rule_pixels(image)
        # Intersection may interrupt detection but each long segment remains.
        self.assertTrue(mask[10, 70])
        self.assertTrue(mask[80, 10])
        self.assertFalse(mask[10, 10])

    def test_uniform_background(self):
        color, agreement = background(np.full((25, 50, 3), 240, dtype=np.uint8))
        self.assertEqual(agreement, 1)
        self.assertEqual(color.tolist(), [240, 240, 240])

    def test_mixed_background_rejected(self):
        image = np.zeros((30, 30, 3), dtype=np.uint8)
        image[:, 15:] = 255
        self.assertLess(background(image)[1], .75)
