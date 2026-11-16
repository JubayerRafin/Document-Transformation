"""Remove repeated per-stage setup work from BabelDOC without changing its output.

Measured 2026-11-16 on the RTX 4090 pod (one-page document, translations cached): BabelDOC spent
about 1.2 s before each of its seven stages building a new FontMapper. Each one re-hashed every
font file (sha3, in a new thread with a new event loop per font) and parsed every font again. A
background MemoryMonitor thread, which only logs peak memory at the end, spent several seconds of
CPU reading /proc smaps, competing with the work thread for the GIL.

install():
  - verifies each font file once per process (keyed by name, file size and modification time),
  - reuses the parsed pymupdf.Font for a font file (BabelDOC already shares one Font object
    across threads within a FontMapper),
  - keeps the MemoryMonitor from starting its sampling thread.
"""
import os
import threading

_lock = threading.Lock()


def _font_assets(assets):
    original = assets.get_font_and_metadata
    if getattr(original, '_transformx_cached', False): return
    verified = {}

    def get_font_and_metadata(font_file_name):
        with _lock:
            hit = verified.get(font_file_name)
        if hit:
            path, metadata, stamp = hit
            try:
                current = os.stat(path)
                if (current.st_size, current.st_mtime_ns) == stamp: return path, metadata
            except OSError:
                pass
        path, metadata = original(font_file_name)
        current = os.stat(path)
        with _lock:
            verified[font_file_name] = (path, metadata, (current.st_size, current.st_mtime_ns))
        return path, metadata

    get_font_and_metadata._transformx_cached = True
    assets.get_font_and_metadata = get_font_and_metadata


def _parsed_fonts(fontmap):
    pymupdf = fontmap.pymupdf
    if getattr(pymupdf, '_transformx_font_cache', False): return
    fonts = {}
    real = pymupdf.Font

    class FontType(type):
        """Constructs like pymupdf.Font (reusing a parsed file) and type-checks as pymupdf.Font."""
        def __instancecheck__(cls, obj): return isinstance(obj, real)
        def __subclasscheck__(cls, sub): return issubclass(sub, real)
        def __getattr__(cls, name): return getattr(real, name)

        def __call__(cls, *args, **kwargs):
            path = kwargs.get('fontfile')
            if args or set(kwargs) != {'fontfile'} or not path:
                return real(*args, **kwargs)
            with _lock:
                font = fonts.get(path)
                if font is None:
                    font = fonts[path] = real(fontfile=path)
            return font

    class Font(metaclass=FontType):
        pass

    class PyMuPDF:
        _transformx_font_cache = True
        def __getattr__(self, name): return getattr(pymupdf, name)

    module = PyMuPDF()
    module.Font = Font
    fontmap.pymupdf = module


def _quiet_memory_monitor(high_level):
    monitor = high_level.MemoryMonitor
    if getattr(monitor, '_transformx_quiet', False): return
    monitor.__enter__ = lambda self: self
    monitor.__exit__ = lambda self, *exc: None
    monitor._transformx_quiet = True


def install():
    from babeldoc.assets import assets
    from babeldoc.format.pdf.document_il.utils import fontmap
    from babeldoc.format.pdf import high_level
    _font_assets(assets)
    _parsed_fonts(fontmap)
    _quiet_memory_monitor(high_level)
    return True


def warm():
    """Preload server warm-up (transformation/preload.py): install the reuse above and verify every
    font file already in BabelDOC's cache once, so each forked job starts with verified fonts.
    Fonts not yet downloaded are left to the job; nothing here uses the network or the GPU."""
    install()
    from babeldoc.assets import assets
    from babeldoc.const import get_cache_file_path
    for name in getattr(assets, 'EMBEDDING_FONT_METADATA', {}):
        if get_cache_file_path(name, 'fonts').exists():
            assets.get_font_and_metadata(name)
