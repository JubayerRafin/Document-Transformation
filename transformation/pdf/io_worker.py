"""Minimal spawn targets for the pinned vendor's PDF I/O subprocesses.

Keep the same MuPDF calls, flags and exit status without importing BabelDOC's
layout/translation stack in a child that only subsets fonts or saves a PDF.
The vendor still owns timeout, cancellation, fallback and output checks.
"""
import os


def subset_fonts(pdf_path,output_path):
    try:
        import pymupdf
        with pymupdf.open(pdf_path) as pdf:
            pdf.subset_fonts(fallback=False)
            pdf.save(output_path)
    except Exception:
        os._exit(1)
    os._exit(0)


def save_clean(pdf_path,output_path,garbage=1,deflate=True,clean=True,deflate_fonts=True,linear=False):
    try:
        import pymupdf
        with pymupdf.open(pdf_path) as pdf:
            pdf.save(output_path,garbage=garbage,deflate=deflate,clean=clean,
                     deflate_fonts=deflate_fonts,linear=linear)
    except Exception:
        os._exit(1)
    os._exit(0)


def install():
    from babeldoc.format.pdf.document_il.backend import pdf_creater
    pdf_creater._subset_fonts_process=subset_fonts
    pdf_creater._save_pdf_clean_process=save_clean
