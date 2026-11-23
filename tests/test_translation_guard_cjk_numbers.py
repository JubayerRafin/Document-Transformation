"""Numbers written in CJK conventions: full-width digits and Japanese era years."""
import unittest

from transformation.llm.translation_guard import era_years, validate_candidate


class CjkNumberTests(unittest.TestCase):
    def accepted(self, source, target):
        try:
            validate_candidate(source, target)
            return True
        except ValueError:
            return False

    def test_full_width_digits_are_the_same_numbers(self):
        # Japanese government deck, 2026-11-24: every attempt refused, the paragraph stayed Japanese.
        self.assertTrue(self.accepted('（１）相談窓口の設置・・・総合的な相談窓口', '(1) Establishment of consultation windows... Comprehensive consultation window'))
        self.assertTrue(self.accepted('（注１）公共データ。（注２）CSV、JSON等。', '(Note 1) Public data. (Note 2) CSV, JSON, etc.'))
        self.assertFalse(self.accepted('（１）相談窓口', '(2) Consultation window'))
        self.assertFalse(self.accepted('（１）相談窓口', 'Consultation window'))

    def test_an_era_year_may_become_its_western_year(self):
        self.assertTrue(self.accepted('平成28年12月14日に公布された。', 'Promulgated on December 14, 2016.'))
        self.assertTrue(self.accepted('平成28年12月14日に公布された。', 'Promulgated on December 14, 2016 (Heisei 28).'))
        self.assertTrue(self.accepted('平成28年12月14日に公布された。', 'Promulgated on December 14, Heisei 28.'))
        self.assertTrue(self.accepted('令和元年5月1日', 'May 1, 2019'))
        self.assertFalse(self.accepted('平成28年12月14日に公布された。', 'Promulgated on December 14, 2017.'))

    def test_era_variants(self):
        self.assertEqual(era_years('昭和64年'), ['1989年', '1989年（昭和64年）'])
        self.assertEqual(era_years('2016年'), [])


if __name__ == '__main__':
    unittest.main()
