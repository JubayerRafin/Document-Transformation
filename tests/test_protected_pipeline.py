import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pymupdf

from transformation.llm.ollama_native import install, translate_paragraph, TRANSLATION_FAILED
from transformation.llm.protected_text import PlaceholderIntegrityError
from transformation.pdf.runner import run_pdf


class ProtectedPipelineTests(unittest.TestCase):
    def setUp(self):
        TRANSLATION_FAILED.clear()

    def tearDown(self):
        TRANSLATION_FAILED.clear()

    def translator(self, values, context=""):
        class Translator:
            def __init__(self):
                self.lang_out = "ko"
                self.model = "qwen3.5:9b"
                self.prompt_token_count = NS(inc=lambda n: None)
                self.completion_token_count = NS(inc=lambda n: None)
                self.token_count = NS(inc=lambda n: None)

            def add_cache_impact_parameters(self, key, value):
                self.cache_key = (key, value)

        with patch.dict(sys.modules, {
            "babeldoc.translator.translator": NS(OpenAITranslator=Translator)
        }):
            install("http://localhost:11434", True, values, document_context=context)
            return Translator()

    def test_cache_changes_with_values_but_not_order_or_duplicates(self):
        first = self.translator(["Kim Min-jun", "2024-03-18"])
        same = self.translator(["2024-03-18", "Kim Min-jun", "Kim Min-jun"])
        changed = self.translator(["Lee Soo-jin"])
        self.assertEqual(first.cache_key, same.cache_key)
        self.assertNotEqual(first.cache_key, changed.cache_key)
        self.assertNotEqual(first.cache_key, self.translator([]).cache_key)

    def test_translator_protects_each_occurrence_and_restores(self):
        translator = self.translator(["Kim Min-jun", "2024-03-18"])
        source = "Kim Min-jun / Kim Min-jun / 2024-03-18 / privacy@example.com"

        def reply(endpoint, model, messages, **kwargs):
            protected = messages[1]["content"]
            for value in ("Kim Min-jun", "2024-03-18", "privacy@example.com"):
                self.assertNotIn(value, protected)
            return protected, {}

        with patch("transformation.llm.ollama_native.chat", side_effect=reply):
            self.assertEqual(translator.do_translate(source), source)

    def test_context_reaches_isolated_label_and_changes_cache(self):
        context = "Personnel form: Title refers to the employee's job role."
        translator = self.translator([], context)
        self.assertNotEqual(translator.cache_key, self.translator([]).cache_key)
        self.assertNotEqual(translator.cache_key, self.translator([], "Book catalog").cache_key)
        with patch("transformation.llm.ollama_native.chat", return_value=("translated", {})) as call:
            translator.do_translate("Title")
        messages = call.call_args.args[2]
        self.assertIn(context, messages[0]["content"])
        self.assertEqual(messages[1]["content"], "Title")

    def test_runner_sends_own_configuration_to_worker(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 40), 'Employee record: Kim Min-jun')
            data = pdf.tobytes()
        captured = {}

        def launch(command, **kwargs):
            captured.update(kwargs)
            captured["command"] = command
            output = Path(command[command.index("--output") + 1])
            (output / "source.ko.mono.pdf").write_bytes(data)
            return NS(poll=lambda: 0, returncode=0)

        with tempfile.TemporaryDirectory() as folder:
            with patch("transformation.pdf.runner.subprocess.check_output", return_value="0.6.4"), \
                 patch("transformation.pdf.runner.subprocess.Popen", side_effect=launch), \
                 patch("transformation.validation.quality.review_signals"):
                events = list(run_pdf(
                    data, "test.pdf", python=sys.executable, model="qwen3.5:9b",
                    root=Path(folder), protected_values=["Kim Min-jun"],
                    document_context="Employee onboarding form",
                    ignore_cache=True,
                ))
            self.assertEqual(events[-1]["status"], "generated")
            self.assertTrue(events[-1]["ignore_translation_cache"])
            self.assertIn("--ignore-cache", captured["command"])
            self.assertEqual(events[-1]["protected_values"], ["Kim Min-jun"])
            config = Path(captured["env"]["TRANSFORMX_PROTECTED_VALUES_FILE"])
            self.assertEqual(config.parent, Path(events[-1]["run_dir"]))
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), ["Kim Min-jun"])
            context_file = Path(captured["env"]["TRANSFORMX_DOCUMENT_CONTEXT_FILE"])
            self.assertEqual(json.loads(context_file.read_text(encoding="utf-8")), "Employee onboarding form")
            self.assertEqual(events[-1]["document_context"], "Employee onboarding form")

    def test_any_model_name_gets_the_protected_paragraph_path(self):
        # A model of any name (an OpenAI or gateway model) is not refused for protected values; the
        # input is still checked before anything starts.
        with patch("transformation.pdf.runner.subprocess.Popen") as launch:
            with self.assertRaisesRegex(Exception, "(?i)pdf|document|stream"):
                list(run_pdf(b"", "test.pdf", python=sys.executable,
                             model="gpt-5-mini", protected_values=["Kim Min-jun"]))
            launch.assert_not_called()

    def test_missing_value_retry_recovers_and_counts_both_calls(self):
        # Any chat model (not only TranslateGemma) goes through the checked path: a first answer that
        # drops a protected value is retried from the original text with the value named.
        with patch("transformation.llm.ollama_native.chat", side_effect=[
                 ("오늘 연락하세요", {"prompt_eval_count": 10, "eval_count": 2}),
                 ("오늘 Kim Min-jun에게 연락하세요", {"prompt_eval_count": 20, "eval_count": 3}),
             ]) as call:
            result, stats = translate_paragraph("http://localhost:11434", "test", "Contact Kim Min-jun today",
                                                "Korean", protected_values=("Kim Min-jun",))
        self.assertEqual(result, "오늘 Kim Min-jun에게 연락하세요")
        self.assertEqual(call.call_count, 2)
        self.assertEqual((stats["prompt_eval_count"], stats["eval_count"]), (30, 5))
        self.assertFalse(TRANSLATION_FAILED.is_set())
        retry = call.call_args.args[2]
        self.assertIn("failed exact preservation checks", retry[0]["content"])
        self.assertIn("Kim Min-jun", retry[0]["content"])

    def test_a_paragraph_that_keeps_losing_a_value_is_flagged_not_fatal(self):
        with patch("transformation.llm.ollama_native.chat", return_value=("오늘 연락하세요", {})):
            result, stats = translate_paragraph("http://localhost:11434", "test", "Contact Kim Min-jun today",
                                                "Korean", protected_values=("Kim Min-jun",))
        # The value is never lost: the paragraph comes back recovered or in its source language,
        # flagged for review, and the document does not fail.
        self.assertIn("Kim Min-jun", result)
        self.assertTrue(stats.get("kept_source_requires_review") or stats.get("recovery_review_required"))
        self.assertFalse(TRANSLATION_FAILED.is_set())

    def test_transport_failure_is_not_retried(self):
        with patch("transformation.llm.ollama_native.chat", side_effect=TimeoutError("timeout")) as call:
            with self.assertRaises(TimeoutError):
                translate_paragraph("http://localhost:11434", "test", "Hello", "Korean")
        self.assertEqual(call.call_count, 1)
        self.assertTrue(TRANSLATION_FAILED.is_set())


if __name__ == "__main__":
    unittest.main()
