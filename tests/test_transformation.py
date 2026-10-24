import sys
import unittest
from transformation.pdf.runner import validate_options
from transformation.pdf.babeldoc_worker import constrain_caption_boxes
from types import SimpleNamespace as NS
from unittest.mock import patch
from io import BytesIO
import json
from transformation.llm.ollama_native import chat, install


class TransformationBoundaries(unittest.TestCase):
    def test_paragraph_mode_uses_plain_path_and_named_language(self):
        class FakeTranslator:
            def __init__(self):
                self.lang_out = "ko"
                self.lang_in = "de"
                self.model = "qwen2.5:3b"
                self.prompt_token_count = self.completion_token_count = self.token_count = NS(inc=lambda n: None)
            def add_cache_impact_parameters(self, *args):
                self.cache_key = args
        module = NS(OpenAITranslator=FakeTranslator)
        with patch.dict(sys.modules, {"babeldoc.translator.translator": module}):
            install("http://localhost:11434", paragraph_mode=True)
            translator = FakeTranslator()
            with patch('transformation.llm.ollama_native.translate_paragraph',return_value=('translated',{})) as translate:
                translator.do_translate('source paragraph')
                self.assertEqual(translate.call_args.kwargs['source_language'],'de')
            with patch("transformation.llm.ollama_native.chat", return_value=("translated", {})) as request:
                with self.assertRaises(NotImplementedError):
                    translator.do_llm_translate(None)
                request.assert_not_called()
                self.assertEqual(translator.do_translate("source paragraph"), "translated")
                messages = request.call_args.args[2]
                self.assertIn("Korean", messages[0]["content"])
                self.assertEqual(messages[1]["content"], "source paragraph")
                self.assertIn("paragraph", translator.cache_key[1])

    def test_llm_capability_probe_does_not_call_model(self):
        class FakeTranslator:
            def __init__(self):
                pass
            def add_cache_impact_parameters(self, *args):
                pass
        with patch.dict(sys.modules, {"babeldoc.translator.translator": NS(OpenAITranslator=FakeTranslator)}):
            install("http://localhost:11434")
            with patch("transformation.llm.ollama_native.chat") as request:
                self.assertIsNone(FakeTranslator().do_llm_translate(None))
                request.assert_not_called()

    def test_redaction_cannot_silently_run_as_translation(self):
        with self.assertRaisesRegex(ValueError, "not implemented"):
            validate_options(sys.executable, "http://localhost:11434/v1", "en", "ko", redact=True)

    def test_remote_endpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "local model"):
            validate_options(sys.executable, "https://example.com/v1", "en", "ko")

    def test_no_operation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Choose an available"):
            validate_options(sys.executable, "http://localhost:11434/v1", "en", "ko", translate=False)

    def test_identical_languages_rejected(self):
        with self.assertRaisesRegex(ValueError, "different"):
            validate_options(sys.executable, "http://localhost:11434/v1", "en", "en")

    def test_local_configuration_accepted(self):
        validate_options(sys.executable, "http://127.0.0.1:11434/v1", "en", "ko")

    def test_caption_edge_is_constrained_but_inside_figure_text_is_not(self):
        caption = NS(box=NS(x=10, y=10, x2=200, y2=30), layout_label="plain text", debug_id="caption")
        inside = NS(box=NS(x=20, y=40, x2=70, y2=60), layout_label="plain text", debug_id="inside")
        image = NS(box=NS(x=10, y=25, x2=80, y2=100))
        doc = NS(page=[NS(page_number=0, pdf_paragraph=[caption, inside], pdf_figure=[image])])
        changes = constrain_caption_boxes(doc)
        self.assertEqual(len(changes), 1)
        self.assertEqual(caption.box.y2, 23)
        self.assertEqual(inside.box.y2, 60)

    def test_substantial_overlap_requires_review_not_automatic_adjustment(self):
        para = NS(box=NS(x=10, y=10, x2=200, y2=60), layout_label="plain text", debug_id="body")
        image = NS(box=NS(x=10, y=25, x2=80, y2=100))
        doc = NS(page=[NS(page_number=0, pdf_paragraph=[para], pdf_figure=[image])])
        self.assertEqual(constrain_caption_boxes(doc), [])
        self.assertEqual(para.box.y2, 60)

    def test_native_transport_rejects_truncated_translation(self):
        response = BytesIO(json.dumps({"message": {"content": "partial"}, "done": True, "done_reason": "length"}).encode())
        with patch("transformation.llm.ollama_native.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "truncated"):
                chat("http://127.0.0.1:11434/v1", "test", [])

    def test_native_transport_sends_explicit_non_thinking_options(self):
        response = BytesIO(json.dumps({"message": {"content": "translated"}, "done": True, "done_reason": "stop"}).encode())
        with patch("transformation.llm.ollama_native.urllib.request.urlopen", return_value=response) as call:
            text, _ = chat("http://127.0.0.1:11434/v1", "test", [])
            payload = json.loads(call.call_args.args[0].data)
            self.assertFalse(payload["think"])
            self.assertEqual(payload["options"]["num_ctx"], 8192)
            self.assertEqual(text, "translated")
