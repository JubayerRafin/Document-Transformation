"""Companies and brands the PII model lists as people (Canon and Samsung manuals, 2026-11-23)."""
import unittest

from transformation.validation.pii_document import person_names


class PersonNameTests(unittest.TestCase):
    def test_companies_brands_and_short_listed_words_are_not_people(self):
        units = [{'text': 'Samsung Smart TV. ' * 12 + 'Contact John Smith. Smith will call. ' + 'Smith. ' * 10}]
        names = ['CANON INC', 'Adobe Systems Incorporated', 'SunSoft, Inc', 'Samsung', 'Yes', 'John Smith', 'Smith', 'Kim']
        self.assertEqual(person_names(names, units, sweep=True), ['John Smith', 'Smith'])
        # The model's own findings in context keep a short name.
        self.assertEqual(person_names(['Kim', 'Samsung'], units, known=['John Smith']), ['Kim'])

    def test_a_repeated_surname_of_a_named_person_stays(self):
        units = [{'text': 'Becker signed. ' * 12 + 'Thomas Becker, director.'}]
        self.assertEqual(person_names(['Becker'], units, known=['Thomas Becker']), ['Becker'])
        self.assertEqual(person_names(['Becker'], units), [])          # with no full name it reads as a brand


if __name__ == '__main__':
    unittest.main()
