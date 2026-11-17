import unittest
from unittest.mock import patch

from transformation.llm.role_bindings import explicit_bindings,validate_bindings
from transformation.llm.translation_guard import validate_candidate


class ContactBindingTests(unittest.TestCase):
    def test_generic_contact_can_be_name_email_or_phone(self):
        source=('Record for Maya Noor-rahman (Employee ID STAFF-3982). '
                'Contact: maya@example.org, mobile 020-3456-7890. '
                'Emergency contact: Omar Ali-rahman, 030-4567-8901.')
        target=('직원 ID STAFF-3982인 Maya Noor-rahman의 기록. '
                '담당자: maya@example.org, 연락처: 020-3456-7890. '
                '비상 연락처: Omar Ali-rahman, 연락처: 030-4567-8901.')
        validate_candidate(source,target)
        bindings=explicit_bindings(source)
        self.assertEqual(next(b['value'] for b in bindings if b['role']=='phone'),'020-3456-7890')
        self.assertEqual([b['value_type'] for b in bindings if b['role']=='contact'],['email','literal'])

    def test_distinct_phone_values_are_not_reduced_to_shared_prefix(self):
        source='Phone: 020-3456-7890; Email: info@example.org'
        validate_candidate(source,'전화번호: 020-3456-7890; 이메일: info@example.org')
        with self.assertRaisesRegex(ValueError,'relationship'):
            validate_candidate(source,'전화번호: info@example.org; 이메일: 020-3456-7890')
        with self.assertRaises(ValueError):
            validate_candidate(source,'전화번호: 020-3456-7891; 이메일: info@example.org')

    def test_international_phone_and_ambiguous_contact(self):
        rows=explicit_bindings('Phone: +44 (20) 3456-7890; Contact: desk@example.net')
        self.assertEqual(rows[0]['value'],'+44 (20) 3456-7890')
        self.assertEqual(rows[0]['value_type'],'phone')
        self.assertEqual(rows[1]['value_type'],'email')
        self.assertEqual(explicit_bindings('Contact: 2025-08-19')[0]['value_type'],'literal')

    def test_ambiguous_contact_does_not_bind_unlabeled_emergency_phone_to_mobile(self):
        source='Mobile: 020-3456-7890. Emergency contact: Omar Ali-rahman, 030-4567-8901.'
        target='휴대폰: 020-3456-7890. 비상 연락처: Omar Ali-rahman, 연락처: 030-4567-8901.'
        validate_candidate(source,target)
        validate_candidate(source,target.replace('연락처: 030-','전화번호: 030-'))

    def test_swapped_employee_and_document_ids_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'relationship'):
            validate_candidate('Document ID: DOC-72; Employee ID: EMP-38',
                               '문서 번호: EMP-38; 직원 번호: DOC-72')

    def test_repeated_name_is_not_silently_removed_or_accepted(self):
        source='Maya Noor-rahman (Employee ID STAFF-3982)'
        with self.assertRaises(ValueError):
            validate_candidate(source,'Maya Noor-rahman (직원 ID STAFF-3982), Maya Noor-rahman')

    def test_adapter_accepts_correct_placeholder_response_without_retry(self):
        from transformation.llm.protected_text import protect_values
        from transformation.llm.translategemma import translate
        source='Mobile: 020-3456-7890. Emergency contact: Omar Ali-rahman, 030-4567-8901.'
        safe,tokens=protect_values(source)
        candidate=safe.replace('Mobile:','휴대폰:').replace('Emergency contact:','비상 연락처:')
        with patch('transformation.llm.translategemma.protect_values',return_value=(safe,tokens)),patch(
                'transformation.llm.ollama_native.chat',return_value=(candidate,{})) as chat:
            result,_=translate('unused','translategemma:12b',source,'Korean')
            validate_candidate(source,result)
            self.assertEqual(chat.call_count,1)


if __name__=='__main__':unittest.main()
