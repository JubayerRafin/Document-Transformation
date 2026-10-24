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
