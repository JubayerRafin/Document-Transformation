import unittest

from transformation.llm.translategemma import fit_leader

SOURCE = "Check the sender's fax machine" + '.' * 120


class LeaderTests(unittest.TestCase):
    def test_the_leader_uses_the_source_character_and_never_grows(self):
        result = fit_leader(SOURCE, '발신자의 팩스기를 확인하십시오 ' + '·' * 130)
        self.assertTrue(result.startswith('발신자의 팩스기를 확인하십시오 .'))
        self.assertNotIn('·', result)
        self.assertLessEqual(result.count('.'), 120)

    def test_a_longer_translated_title_gets_a_shorter_leader(self):
        short = fit_leader(SOURCE, 'Fax prüfen ' + '.' * 120).count('.')
        long = fit_leader(SOURCE, 'Vérifier le télécopieur de l’expéditeur maintenant ' + '.' * 120).count('.')
        self.assertLess(long, short)
        self.assertGreaterEqual(fit_leader(SOURCE, 'x' * 400 + ' ' + '.' * 9).count('.'), 3)

    def test_lines_without_a_leader_are_unchanged(self):
        self.assertEqual(fit_leader('Total due: 5', '합계: 5'), '합계: 5')
        self.assertEqual(fit_leader(SOURCE, '발신자의 팩스기를 확인하십시오'), '발신자의 팩스기를 확인하십시오')
        self.assertEqual(fit_leader('Wait...', 'Attendez...'), 'Attendez...')


if __name__ == '__main__':
    unittest.main()
