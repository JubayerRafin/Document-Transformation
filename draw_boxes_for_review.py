"""Overlay draft tables/figures boxes onto rendered pages for visual review."""
import json
import fitz
from pathlib import Path

OUT = Path("annotation-workspace/review-renders")

samples = ["Sample01", "Sample03", "Sample04", "Sample05", "Sample06"]

for name in samples:
    draft = json.loads(Path(f"annotation-workspace/drafts/{name}.json").read_text(encoding="utf-8"))
    doc = fitz.open(f"samples/provided/{name}.pdf")
    for page_data in draft["pages"]:
        pnum = page_data["number"]
        page = doc[pnum - 1]
        pix = page.get_pixmap(dpi=150)
        # Draw using pymupdf shape on a copy via new doc page? Simpler: draw directly on `page`
        shape = page.new_shape()
        for t in page_data.get("tables", []):
            r = fitz.Rect(*t["bbox"])
            shape.draw_rect(r)
            shape.finish(color=(0, 0, 1), width=1.5)
        for f in page_data.get("figures", []):
            r = fitz.Rect(*f["bbox"])
            shape.draw_rect(r)
            shape.finish(color=(1, 0, 0), width=1.5)
        shape.commit()
        pix = page.get_pixmap(dpi=150)
        out_path = OUT / f"{name}-p{pnum}-boxes.png"
        pix.save(str(out_path))
        print(out_path)
