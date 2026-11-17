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

        def reply(endpoint, model, messages):
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

    def test_unsupported_path_fails_before_subprocess(self):
        with patch("transformation.pdf.runner.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "single-paragraph"):
                list(run_pdf(b"", "test.pdf", python=sys.executable,
                             model="other-model", protected_values=["Kim Min-jun"]))
            launch.assert_not_called()

    def test_missing_token_retry_recovers_and_counts_both_calls(self):
        token = "ZXKEEPTESTX0XZ"
        with patch("transformation.llm.ollama_native.protect_values", return_value=(token, {token: "Kim Min-jun"})), \
             patch("transformation.llm.ollama_native.chat", side_effect=[
                 ("missing", {"prompt_eval_count": 10, "eval_count": 2}),
                 (token, {"prompt_eval_count": 20, "eval_count": 3}),
             ]) as call, self.assertLogs("transformation.llm.ollama_native", level="INFO") as logs:
            result, stats = translate_paragraph("http://localhost:11434", "test", "Kim Min-jun", "Korean")
        self.assertEqual(result, "Kim Min-jun")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(stats["prompt_eval_count"], 30)
        self.assertEqual(stats["eval_count"], 5)
        self.assertFalse(TRANSLATION_FAILED.is_set())
        self.assertIn("Required tokens", call.call_args.args[2][0]["content"])
        self.assertTrue(any('"actual": 0' in line for line in logs.output))

    def test_failed_retry_keeps_run_failed(self):
        token = "ZXKEEPTESTX0XZ"
        with patch("transformation.llm.ollama_native.protect_values", return_value=(token, {token: "Kim Min-jun"})), \
             patch("transformation.llm.ollama_native.chat", return_value=(token + " " + token, {})) as call, \
             self.assertLogs("transformation.llm.ollama_native", level="WARNING"):
            with self.assertRaises(PlaceholderIntegrityError) as caught:
                translate_paragraph("http://localhost:11434", "test", "Kim Min-jun", "Korean")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(caught.exception.issues[0]["actual"], 2)
        self.assertTrue(TRANSLATION_FAILED.is_set())

    def test_transport_failure_is_not_retried(self):
        with patch("transformation.llm.ollama_native.chat", side_effect=TimeoutError("timeout")) as call:
            with self.assertRaises(TimeoutError):
                translate_paragraph("http://localhost:11434", "test", "Hello", "Korean")
        self.assertEqual(call.call_count, 1)
        self.assertTrue(TRANSLATION_FAILED.is_set())


if __name__ == "__main__":
    unittest.main()
