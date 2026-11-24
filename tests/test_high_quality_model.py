"""The optional higher-quality translation model: offered as a choice, its jobs run alone."""
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from transformation import settings
from tests.temp_workspace import temporary_workspace

HQ = {'TRANSFORMX_HIGH_QUALITY_TRANSLATION': 'http://127.0.0.1:8011/v1,translategemma:27b'}


class HighQualityChoiceTests(unittest.TestCase):
    def test_the_server_setting_adds_an_exclusive_choice(self):
        with patch.object(settings, 'find', return_value=None):
            choices = settings.model_choices('translation', environ=HQ)
            self.assertEqual([c['id'] for c in choices], ['default', 'high_quality'])
            self.assertEqual(choices[1]['label'], 'TranslateGemma 27B · higher quality, slower')
            self.assertEqual((choices[1]['endpoint'], choices[1]['model']), ('http://127.0.0.1:8011/v1', 'translategemma:27b'))
            self.assertEqual(settings.exclusive_choices(environ=HQ), {'high_quality'})
            self.assertEqual([c['id'] for c in settings.model_choices('pii', environ=HQ)], ['default'])
            self.assertEqual(settings.exclusive_choices(environ={}), set())


class ExclusiveJobTests(unittest.TestCase):
    def setUp(self):
        from tools.service import local_server as server
        self.tmp = temporary_workspace()
        self.store = server.Store(Path(self.tmp.__enter__()) / 'jobs')
        self.addCleanup(self.tmp.__exit__, None, None, None)
        self.created = 0

    def job(self, model=None):
        self.created += 1
        options = {'operation': 'translation', **({'translation_model': model} if model else {})}
        job = {'id': str(uuid.uuid4()), 'filename': 'a.pdf', 'status': 'queued', 'stage': 'queued', 'created': self.created,
               'updated': self.created, 'options': options, 'artifacts': [], 'events': [], 'metrics': {}}
        self.store.save(job)
        return job

    def test_a_high_quality_job_waits_for_running_jobs_and_runs_alone(self):
        exclusive = {'high_quality'}
        first = self.job(); hq = self.job('high_quality'); later = self.job()
        self.assertEqual(self.store.claim_next(exclusive)['id'], first['id'])
        self.assertIsNone(self.store.claim_next(exclusive))          # hq waits; later does not pass it
        first.update(status='succeeded'); self.store.save(first)
        self.assertEqual(self.store.claim_next(exclusive)['id'], hq['id'])
        self.assertIsNone(self.store.claim_next(exclusive))          # nothing runs beside it
        hq.update(status='succeeded'); self.store.save(hq)
        self.assertEqual(self.store.claim_next(exclusive)['id'], later['id'])

    def test_without_exclusive_models_two_jobs_run_side_by_side(self):
        self.job(); self.job('high_quality')
        self.assertIsNotNone(self.store.claim_next())
        self.assertIsNotNone(self.store.claim_next())


if __name__ == '__main__':
    unittest.main()
