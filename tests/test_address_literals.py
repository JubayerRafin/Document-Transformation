"""Postal addresses are protected literals in translations. No models needed."""
import unittest

from transformation.llm.address_literals import address_literals, street_addresses


class StreetPatternTests(unittest.TestCase):
    def test_common_street_forms_are_found(self):
        cases = {
            'Ship to 221B Baker Street, London': '221B Baker Street',
            'HQ: 1600 Pennsylvania Avenue NW, Washington': '1600 Pennsylvania Avenue NW',
            'Lives at 12, Teheran-ro 4-gil, Seoul': '12, Teheran-ro 4-gil',
            'Hauptstraße 5, 10115 Berlin': 'Hauptstraße 5',
            'Envoyez à 10 rue de Rivoli': '10 rue de Rivoli',
            'Calle Mayor 5, Madrid': 'Calle Mayor 5',
        }
        for text, address in cases.items():
            self.assertIn(address, street_addresses(text), text)

    def test_ordinary_prose_and_names_are_not_addresses(self):
        for text in ('The road ahead is long.', 'Kim Min-jun works in Seoul.', 'Avenue of approach: rate 3',
                     'Step 2: Drive the change', 'Version 1.2 ships in Q3.'):
            self.assertEqual(street_addresses(text), [], text)


class LabelledAddressTests(unittest.TestCase):
    def test_labelled_values_and_their_parts_are_protected_longest_first(self):
        ir = {'units': [{'regions': [
            {'cells': [['Field', 'Value'], ['Home Address', 'Flat 3, 9 Elm Grove, Leeds'], ['Start Date', '2024-03-18']]},
            {'text': 'Mailing address: PO Box 77, Springfield\nPhone: 555-0100'}]}]}
        literals = address_literals(ir)
        for expected in ('Flat 3, 9 Elm Grove, Leeds', 'Flat 3', '9 Elm Grove', 'Leeds', 'PO Box 77, Springfield'):
            self.assertIn(expected, literals)
        self.assertNotIn('2024-03-18', literals)
        self.assertNotIn('555-0100', literals)
        self.assertEqual(literals, sorted(literals, key=len, reverse=True))

    def test_a_bare_house_number_stays_with_its_street(self):
        ir = {'units': [{'regions': [{'cells': [['Address', '12, Teheran-ro 4-gil, Gangnam-gu']]}]}]}
        literals = address_literals(ir)
        self.assertIn('12, Teheran-ro 4-gil', literals)
        self.assertNotIn('12', literals)


if __name__ == '__main__':
    unittest.main()
