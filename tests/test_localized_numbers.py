import unittest
from unittest.mock import patch

from transformation.llm.translation_guard import validate_candidate
from transformation.llm.translategemma import translate


class LocalizedNumberTests(unittest.TestCase):
    def test_print_side_counts_can_be_lexicalized_without_losing_meaning(self):
        for source,target in [
            ('Automatic 2-sided printing or with Alternative Letterhead','자동 양면 인쇄 또는 대체 편지머리 사용.'),
            ('1-sided printing','단면 인쇄'),
            ('Choose 1-sided printing or 2-sided printing','단면 인쇄 또는 양면 인쇄 선택'),
        ]:
            with self.subTest(source=source):validate_candidate(source,target)

    def test_wrong_side_counts_or_other_omitted_values_still_fail(self):
        for source,target,values in [
            ('1-sided printing','양면 인쇄',()),
            ('2-sided printing','단면 인쇄',()),
            ('2-sided printing','삼양면 인쇄',()),
            ('2-sided printing','양면 인쇄 및 양면 인쇄',()),
            ('2 copies','양면 인쇄',()),
            ('2-sided printing; 2 copies','양면 인쇄',()),
            ('2-sided printing; code AB-21','양면 인쇄; 코드 AB-22',()),
            ('2-sided printing; cost USD120','양면 인쇄; 가격 USD121',()),
            ('2-sided printing','양면 인쇄',('2',)),
            ('2-sided printing','양면 인쇄',('2-sided printing',)),
        ]:
            with self.subTest(source=source,target=target),self.assertRaises(ValueError):validate_candidate(source,target,values)

    def test_adapter_accepts_print_equivalence_on_existing_second_attempt(self):
        with patch('transformation.llm.ollama_native.chat',side_effect=[('자동 양면 인쇄',{}),('자동 양면 인쇄',{})]) as chat:
            result,_=translate('http://127.0.0.1:11434/v1','translategemma:12b','Automatic 2-sided printing','Korean')
        self.assertEqual(result,'자동 양면 인쇄')
        self.assertEqual(chat.call_count,2)

    def test_month_words_can_become_calendar_numbers(self):
        for source,target in [
            ('Summer (Jun-Aug)','여름 (6월-8월)'),
            ('Summer (Jun-Aug)','여름 (6~8월)'),
            ('Autumn (Sep-Oct)','가을 (9월-10월)'),
            ('Spring (Mar-Apr)','봄 (3월-4월)'),
            ('Spring (Mar-May)','봄 (3월-5월)'),
            ('Fall (Oct-Nov)','가을 (10월-11월)'),
            ('India · October-November','인도 · 10월-11월'),
            ('Mexico · Early November','멕시코 · 11월 초'),
            ('Japan · MarchApril','일본 · 3월~4월'),
            ('Iceland · June','아이슬란드 · 6월'),
            ('December to February','12月から2月'),
        ]:
            with self.subTest(source=source):validate_candidate(source,target)

    def test_wrong_added_duplicated_and_misclassified_months_fail(self):
        for source,target in [
            ('June','7월'),('June','6월과 6월'),('June','6월과 06월'),
            ('Jun-Aug','8월-6월'),('June','-6월'),
            ('Summer','여름 6월'),('May I enter?','5월에 입장?'),
            ('June','6개'),('Five items','5월'),('Five items','6개'),
        ]:
            with self.subTest(source=source,target=target),self.assertRaises(ValueError):validate_candidate(source,target)

    def test_written_quantities_can_become_digits(self):
        for source,target in [
            ('Five short slides built to test layout-preserving translation.','레이아웃 보존 번역을 테스트하기 위한 5개의 짧은 슬라이드입니다.'),
            ('Twenty-seven sensors','27개의 센서'),
            ('One hundred and forty-two entries','142개 항목'),
            ('Three thousand two hundred files','3200개 파일'),
            ('Five','5'),('Zero failures','오류 0개'),
        ]:
            with self.subTest(source=source):validate_candidate(source,target)

    def test_existing_literals_and_explicit_values_remain_exact(self):
        for source,target,values in [
            ('June; cost USD120; code AB-9912','6월; 가격 USD121; 코드 AB-9912',()),
            ('June; code AB-9912','6월; 코드 AB-9913',()),
            ('Five; total 42','5개; 합계 43',()),
            ('Five items','5개',('Five',)),
            ('June holiday','6월 휴일',('June',)),
            ('Five slides','5',()),
            ('Five','',()),
            ('June; id 006','6월; ID 6',()),
        ]:
            with self.subTest(source=source),self.assertRaises(ValueError):validate_candidate(source,target,values)
        validate_candidate('June; USD120; AB-9912; five items','6월; USD120; AB-9912; 5개')

    def test_adapter_accepts_valid_localization_on_first_attempt(self):
        with patch('transformation.llm.ollama_native.chat',return_value=('6월',{})) as chat,patch('transformation.llm.ollama_native.record_progress'):
            result,_=translate('http://127.0.0.1:11434/v1','translategemma:12b','June','Korean')
        self.assertEqual(result,'6월');self.assertEqual(chat.call_count,1)


class OrdinalWordTests(unittest.TestCase):
    def test_spelled_out_ordinals_may_become_digits(self):
        from transformation.llm.localized_numbers import ordinal_word_quantities
        from transformation.llm.translation_guard import validate_candidate
        self.assertEqual(dict(ordinal_word_quantities('the third quarter, the twenty-first day, the Second floor')),
                         {3: 1, 21: 1, 2: 1})
        source = ('Customer satisfaction rose for the third consecutive quarter. Costs stayed within budget, although '
                  'prices increased in the second month. The total was JPY 769,375,000 on 03/04/2027.')
        validate_candidate(source, '고객 만족도는 3분기 연속 상승했습니다. 2개월 차에 가격이 올랐지만 비용은 예산 범위 내였습니다. '
                                   '총액은 JPY 769,375,000이며 날짜는 03/04/2027입니다.')
        validate_candidate(source, 'Die Kundenzufriedenheit stieg im 3. Quartal in Folge. Im 2. Monat stiegen die Preise. '
                                   'Gesamt: JPY 769,375,000 am 03/04/2027.')

    def test_other_added_numbers_are_still_refused(self):
        from transformation.llm.translation_guard import validate_candidate
        source = 'Prices increased in the second month. The total was JPY 769,375,000.'
        with self.assertRaises(ValueError):   # a number with no source expression
            validate_candidate(source, '7개월 차에 가격이 올랐습니다. 총액은 JPY 769,375,000입니다.')
        with self.assertRaises(ValueError):   # one ordinal does not license two numbers
            validate_candidate(source, '2개월 차와 2분기에 가격이 올랐습니다. 총액은 JPY 769,375,000입니다.')
        with self.assertRaises(ValueError):   # protected values stay exact
            validate_candidate(source, '2개월 차에 가격이 올랐습니다. 총액은 JPY 769,375,001입니다.')


class CalendarTests(unittest.TestCase):
    """Months and quarters written in words or another notation by the translation (2026-11-19)."""

    def test_months_and_quarters_may_change_notation(self):
        from transformation.llm.translation_guard import validate_candidate
        for source, target in [
                ('11월 말까지 3분기 보고서를 제출하십시오.', 'Submit the third quarter report by the end of November.'),
                ('2026年11月に第3四半期の報告書を提出してください。', 'Submit the third quarter report in November 2026.'),
                ('请在11月底前提交第3季度报告。', 'Please submit the Q3 report before the end of November.'),
                ('Revenue rose in Q3 2025.', '2025년 3분기에 매출이 증가했습니다.'),
                ('Revenue rose in Q3 2025.', 'Der Umsatz stieg im 3. Quartal 2025.'),
                ('Revenue rose in Q3 2025.', '2025年第3四半期に売上が増加しました。'),
                ('11월 말까지 제출하십시오.', 'À soumettre avant la fin novembre.')]:
            validate_candidate(source, target)

    def test_wrong_or_missing_calendar_values_are_refused(self):
        from transformation.llm.translation_guard import validate_candidate
        for source, target in [
                ('11월 말까지 제출하십시오.', 'Submit by the end of October.'),
                ('11월 말까지 3분기 보고서를 제출하십시오.', 'Submit the report by the end of November.'),
                ('Revenue rose in Q3 2025.', '2025년 2분기에 매출이 증가했습니다.'),
                ('Revenue rose in Q3 2025.', '2025년 3분기 및 3분기에 매출이 증가했습니다.'),
                ('총액은 5,440,000원입니다.', 'The total is KRW 5,440.')]:
            with self.assertRaises(ValueError, msg=target):
                validate_candidate(source, target)


class SpelledCountTests(unittest.TestCase):
    """Small counts written as the same number in words (Japanese clauses, 2026-11-19)."""
    source = '顧客満足度は3四半期連続で上昇し、損傷があれば2営業日以内に報告してください。'

    def test_the_same_count_in_words_is_accepted(self):
        from transformation.llm.translation_guard import validate_candidate
        validate_candidate(self.source, 'Customer satisfaction rose for three consecutive quarters; '
                                        'report damage within two business days.')

    def test_another_count_a_missing_count_or_a_count_inside_an_identifier_is_refused(self):
        from transformation.llm.translation_guard import validate_candidate
        for target in ('Customer satisfaction rose for four consecutive quarters; report damage within two business days.',
                       'Customer satisfaction rose for consecutive quarters; report damage within two business days.'):
            with self.assertRaises(ValueError):
                validate_candidate(self.source, target)
        with self.assertRaises(ValueError):
            validate_candidate('Ref INV-2025-3', 'Ref INV-2025-three')

    def test_a_count_written_as_the_same_ordinal_word_is_accepted(self):
        """Japanese scan, 2026-11-23: the right answer was refused three times and the paragraph kept in Japanese."""
        from transformation.llm.translation_guard import validate_candidate
        source = '主要生産ラインの保守点検は、毎月第1週末に予定されています。委員会は、試験プログラムをさらに3つの地域事務所に拡大することを推奨します。'
        validate_candidate(source, 'The maintenance and inspection of the main production line are scheduled for the first weekend '
                                   'of each month. The committee recommends expanding the testing program to 3 additional regional offices.')
        for target in ('The maintenance of the main production line is scheduled for the second weekend of each month. '
                       'The committee recommends expanding the testing program to 3 additional regional offices.',
                       'The maintenance of the main production line is scheduled for the weekend of each month. '
                       'The committee recommends expanding the testing program to 3 additional regional offices.'):
            with self.assertRaises(ValueError, msg=target):
                validate_candidate(source, target)


class LengthTests(unittest.TestCase):
    def test_an_answer_far_longer_than_its_source_is_refused(self):
        from transformation.llm.translation_guard import validate_candidate
        with self.assertRaises(ValueError):
            validate_candidate('Thi', 'Sure, please provide the Hindi text you would like me to translate. '
                                      'I will do my best to provide an accurate translation.')
        validate_candidate('概要', 'Overview')
        validate_candidate('従業員は会計年度末までに年次セキュリティ研修を修了する必要があります。',
                           'Employees must complete their annual security training by the end of the fiscal year.')


class GluedUnitTests(unittest.TestCase):
    """Cisco manual into Arabic, 2026-11-23: three paragraphs stayed in English over "240V" and "3in."."""

    def test_a_number_glued_to_its_unit_may_be_written_apart_with_the_unit_translated(self):
        from transformation.llm.translation_guard import validate_candidate
        validate_candidate('to 240V AC', 'إلى 240 فولت تيار متردد')
        validate_candidate('a minimum of 3in. (7.6 cm), and a minimum of 5 in. (12.7 cm)',
                           'بحد أدنى 3 بوصات (7.6 سم)، وبحد أدنى 5 بوصات (12.7 سم)')
        validate_candidate('a 42U rack', 'حامل بارتفاع 42 وحدة')

    def test_another_number_or_a_split_part_number_is_still_refused(self):
        from transformation.llm.translation_guard import validate_candidate
        for source, target in (('to 240V AC', 'إلى 230 فولت تيار متردد'),
                               ('Part DS-C48S-300AC', 'القطعة DS-C48S-300 AC')):
            with self.assertRaises(ValueError, msg=target):
                validate_candidate(source, target)


class ShortFragmentTests(unittest.TestCase):
    def test_a_chatty_answer_to_a_short_fragment_is_refused_and_real_short_translations_pass(self):
        """Canon manual, 2026-11-23: "You ca" came back as an apology asking for text."""
        from transformation.llm.translation_guard import validate_candidate
        with self.assertRaises(ValueError):
            validate_candidate('You ca', '申し訳ありませんが、翻訳するテキストが提供されていません。テキストを入力してください')
        for source, target in (('PIN', 'Persönliche Identifikationsnummer'), ('FAQ', 'Frequently Asked Questions'),
                               ('Favorite', 'お気に入り'), ('合計', 'Total amount'), ('ID', 'Identifikationsnummer')):
            validate_candidate(source, target)
