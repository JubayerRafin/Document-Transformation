import unittest

from transformation.llm.translation_guard import validate_candidate

SOURCE = 'Revenue rose by 8 percent this quarter, mainly because of new contracts.'


class PercentSignTests(unittest.TestCase):
    def test_a_percent_word_may_come_back_as_a_percent_sign(self):
        for target in ('이번 분기 매출은 8% 증가했습니다.', '매출은 8 ％ 증가했습니다.',
                       '本季度收入增长了8%。', 'Le chiffre a augmenté de 8 % ce trimestre.',
                       '이번 분기 매출은 8 퍼센트 증가했습니다.'):
            validate_candidate(SOURCE, target)

    def test_a_changed_or_added_percentage_is_still_rejected(self):
        for target in ('매출은 9% 증가했습니다.', '매출은 8%, 그리고 5% 증가했습니다.'):
            with self.assertRaises(ValueError):
                validate_candidate(SOURCE, target)

    def test_a_source_that_already_has_a_percent_sign_is_compared_as_before(self):
        with self.assertRaises(ValueError):
            validate_candidate('Costs fell 8% and margins 8 percent.', '비용은 8% 감소했습니다.')


if __name__ == '__main__':
    unittest.main()
