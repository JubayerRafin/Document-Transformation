"""Render each sample page to a PNG for visual review against the drafts."""
import fitz  # pymupdf
from pathlib import Path

OUT = Path("annotation-workspace/review-renders")
OUT.mkdir(parents=True, exist_ok=True)

docs = {
    "Sample01": [1],
    "Sample03": [1, 2],
    "Sample04": [1, 2],
    "Sample05": [1],
    "Sample06": [1],
}

for name, pages in docs.items():
    doc = fitz.open(f"samples/provided/{name}.pdf")
    for p in pages:
        page = doc[p - 1]
        pix = page.get_pixmap(dpi=150)
        out_path = OUT / f"{name}-p{p}.png"
        pix.save(str(out_path))
        print(f"{out_path}  ({page.rect.width:.1f} x {page.rect.height:.1f} pt)")
