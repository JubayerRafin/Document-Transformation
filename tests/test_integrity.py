import unittest
from transformation.validation.integrity import compare_text


class IntegrityTests(unittest.TestCase):
    def test_missing_repeated_name_is_detected(self):
        result = compare_text(["Kim Min-jun; Kim Min-jun"], ["Kim Min-jun"], ["Kim Min-jun"])
        self.assertEqual(result["status"], "requires_review")
        self.assertEqual(result["protected_values"][0]["source_count"], 2)

    def test_added_email_is_detected(self):
        result = compare_text(["a@example.com"], ["a@example.com b@example.com"])
        self.assertEqual(result["findings"], 1)

    def test_exact_case_and_punctuation_matter(self):
        result = compare_text(["EMP-001 Kim Min-jun"], ["EMP001 kim Min-jun"], ["EMP-001", "Kim Min-jun"])
        self.assertEqual(result["findings"], 2)

    def test_whitespace_wrapping_is_allowed(self):
        result = compare_text(["Kim Min-jun"], ["Kim\nMin-jun"], ["Kim Min-jun"])
        self.assertEqual(result["status"], "counts_match")

    def test_email_domain_hyphen_is_preserved(self):
        result = compare_text(["a@example-\ncorp.com"], ["a@example-corp.com"])
        self.assertEqual(result["status"], "counts_match")
        self.assertEqual(result["emails"][0]["value"], "a@example-corp.com")

    def test_absent_source_value_needs_review(self):
        result = compare_text(["Other"], ["Other"], ["Kim"])
        self.assertEqual(result["protected_values"][0]["status"], "not_found_in_source")

    def test_substrings_are_not_counted(self):
        result = compare_text(["EMP-001"], ["EMP-0012"], ["EMP-001"])
        self.assertEqual(result["findings"], 1)

    def test_empty_check_does_not_claim_pass(self):
        self.assertEqual(compare_text(["Hello"], ["Bonjour"])["status"], "no_values_checked")

    def test_korean_particle_after_date_is_not_a_missing_value(self):
        result = compare_text(["on 2024-03-18"], ["2024-03-18\uc5d0"], ["2024-03-18"])
        self.assertEqual(result["status"], "counts_match")


if __name__ == "__main__":
    unittest.main()
