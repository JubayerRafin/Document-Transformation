"""BabelDOC setup reuse: fonts verified and parsed once per process, memory sampling off.
Uses stand-ins for BabelDOC's modules, so BabelDOC itself is not needed."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from transformation.pdf import babeldoc_speed


class FontAssetTests(unittest.TestCase):
    def test_each_font_is_verified_once_until_the_file_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            font = Path(folder, 'Font.ttf'); font.write_bytes(b'font-1')
            calls = []
            def original(name):
                calls.append(name); return font, {'ascent': 1}
            assets = SimpleNamespace(get_font_and_metadata=original)
            babeldoc_speed._font_assets(assets)
            for _ in range(3): self.assertEqual(assets.get_font_and_metadata('Font.ttf'), (font, {'ascent': 1}))
            self.assertEqual(calls, ['Font.ttf'])
            font.write_bytes(b'font-two')                  # replaced on disk: verify again
            os.utime(font, ns=(1, 1))
            assets.get_font_and_metadata('Font.ttf')
            self.assertEqual(calls, ['Font.ttf', 'Font.ttf'])
            babeldoc_speed._font_assets(assets)            # installing twice does not wrap twice
            assets.get_font_and_metadata('Font.ttf')
            self.assertEqual(len(calls), 2)


class ParsedFontTests(unittest.TestCase):
    def test_a_font_file_is_parsed_once_and_other_constructions_pass_through(self):
        made = []
        class Font:
            def __init__(self, *args, **kwargs): made.append((args, kwargs))
        fontmap = SimpleNamespace(pymupdf=SimpleNamespace(Font=Font, Rect='rect'))
        babeldoc_speed._parsed_fonts(fontmap)
        a = fontmap.pymupdf.Font(fontfile='/fonts/a.ttf'); b = fontmap.pymupdf.Font(fontfile='/fonts/a.ttf')
        c = fontmap.pymupdf.Font(fontfile='/fonts/c.ttf')
        self.assertIs(a, b); self.assertIsNot(a, c)
        other = fontmap.pymupdf.Font('helv'); fontmap.pymupdf.Font(fontbuffer=b'x')
        self.assertEqual(len(made), 4)
        self.assertEqual(fontmap.pymupdf.Rect, 'rect')     # everything else unchanged
        # BabelDOC checks isinstance(font, pymupdf.Font) through the same module.
        self.assertIsInstance(a, fontmap.pymupdf.Font); self.assertIsInstance(other, fontmap.pymupdf.Font)
        self.assertNotIsInstance('text', fontmap.pymupdf.Font)


class LayoutModelTests(unittest.TestCase):
    def test_the_layout_model_file_is_verified_and_hashed_once_until_it_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'model.onnx'
            model.write_bytes(b'weights')
            calls = []
            def verify():
                calls.append(1)
                return model
            assets, doclayout = SimpleNamespace(get_doclayout_onnx_model_path=verify), SimpleNamespace()
            babeldoc_speed._model_path(assets, doclayout)
            self.assertEqual([assets.get_doclayout_onnx_model_path() for _ in range(3)], [model] * 3)
            self.assertIs(doclayout.get_doclayout_onnx_model_path, assets.get_doclayout_onnx_model_path)
            self.assertEqual(len(calls), 1)
            from transformation.pdf.layout_service import model_digest
            first = model_digest(model)
            self.assertEqual(model_digest(model), first)
            model.write_bytes(b'other weights!')
            os.utime(model, ns=(1, 2))
            assets.get_doclayout_onnx_model_path()
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(model_digest(model), first)


class MemoryMonitorTests(unittest.TestCase):
    def test_memory_monitor_starts_no_thread(self):
        started = []
        class MemoryMonitor:
            def __enter__(self): started.append(1); return self
            def __exit__(self, *exc): pass
        babeldoc_speed._quiet_memory_monitor(SimpleNamespace(MemoryMonitor=MemoryMonitor))
        with MemoryMonitor() as monitor: self.assertIsInstance(monitor, MemoryMonitor)
        self.assertEqual(started, [])


if __name__ == '__main__':
    unittest.main()
