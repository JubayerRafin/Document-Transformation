"""
Stage 1 — Extraction & Markdown Compilation
Exports the four main classes for use by pipeline.py

Team DocuForge | Woosong University Capstone 2026
"""

from importlib import import_module

# Keep independent extraction branches usable when an optional engine is absent.
# Algorithms in the original modules remain unchanged.
_MODULES = {
    'ExtractedElement': 'extracted_element', 'TextExtractor': 'text_extractor',
    'ImageExtractor': 'image_extractor', 'TableExtractor': 'table_extractor',
    'MarkdownCompiler': 'markdown_compiler',
}


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module('.' + _MODULES[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "ExtractedElement",
    "TextExtractor",
    "ImageExtractor",
    "TableExtractor",
    "MarkdownCompiler",
]
