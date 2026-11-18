"""Pages with a /Rotate value are turned into unrotated pages that look the same.

BabelDOC 0.6.4 placed a rotated page's translated text and graphics outside the visible page: a
form page with rotation 270 came out blank (2026-11-19), and the OCR path refuses rotated scans.
PyMuPDF's remove_rotation() keeps every page's appearance pixel for pixel (checked for 90, 180 and
270) and its text, so every later step sees only unrotated pages.
"""
import pymupdf


def unrotated(data):
    """PDF bytes with rotated pages unrotated; the same bytes when no page is rotated or the file
    cannot be read here (later checks report that)."""
    if not isinstance(data, (bytes, bytearray)) or data[:5] != b'%PDF-':
        return data   # later checks refuse anything that is not PDF bytes
    try:
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            if pdf.needs_pass or not any(page.rotation for page in pdf):
                return data
            for page in pdf:
                if page.rotation:
                    page.remove_rotation()
            return pdf.tobytes(garbage=1, deflate=True)
    except Exception:
        return data
