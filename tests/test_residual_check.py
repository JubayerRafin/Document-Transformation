"""Values still in an output, found as words (Samsung and Cisco manuals, 2026-11-23)."""
import unittest

from transformation.validation.output_validation import residual_in


class ResidualTests(unittest.TestCase):
    def test_values_are_found_as_words_split_or_attached_to_a_particle(self):
        for value, text in (('Mat', 'Mat called'), ('Olivia Grant', 'Olivia\nGrant here'), ('105088', 'see 105088.html'),
                            ('EMP-2026-14033', 'EMP-2026-14033은 사번'), ('김민준', '담당자 김민준님'), ('أحمد', 'وأحمد'),
                            ('a@b.com', 'mail a@b.com')):
            self.assertTrue(residual_in(value, text), (value, text))

    def test_a_value_inside_a_longer_word_or_number_is_not_left_behind(self):
        for value, text in (('Mat', 'Format the disk'), ('105088', 'id 1050889'), ('Inc', 'Incorporated')):
            self.assertFalse(residual_in(value, text), (value, text))


if __name__ == '__main__':
    unittest.main()


class RepairTests(unittest.TestCase):
    def test_remaining_occurrences_are_masked_as_words_only(self):
        import pymupdf
        from transformation.validation.value_redaction import redact_remaining
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 80), "Licensed by Adobe to you. Format the disk. Adobe's terms, Adobe/PostScript.", fontsize=11)
            data = pdf.tobytes()
        out, masked = redact_remaining(data, [{'location': 'page 1', 'value': 'Adobe'}, {'location': 'page 1', 'value': 'Mat'}])
        with pymupdf.open(stream=out, filetype='pdf') as pdf:
            text = pdf[0].get_text()
        self.assertEqual(masked, 3)
        self.assertNotIn('Adobe', text)
        self.assertIn('Format', text)
