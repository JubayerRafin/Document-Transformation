"""Conservative review signals, not translation-quality acceptance scores."""
import json
from pathlib import Path
import re

import pymupdf


def review_signals(run):
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    unchanged = []
    tracker_errors = 0
    missing_tracking_text = 0
    for path in (run / "work").rglob("translate_tracking.json"):
        tracking = json.loads(path.read_text(encoding="utf-8"))
        for page in tracking.get("page", []):
            for paragraph in page.get("paragraph", []):
                original = paragraph.get("input", "")
                translated = paragraph.get("output", "")
                if not isinstance(original, str) or not isinstance(translated, str):
                    missing_tracking_text += 1
                elif original.strip() == translated.strip() and len(re.findall(r"[A-Za-z]{2,}", original)) >= 3:
                    unchanged.append(original)
                tracker_errors += sum(bool(t.get("has_error")) for t in paragraph.get("llm_translate_trackers", []))

    def geometry(path):
        overlaps, outside, markup = [], [], []
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc):
                for match in re.finditer(r"</?(?:code|style|b|i)(?:\s[^>]*)?>", page.get_text()):
                    markup.append({"page": i+1, "text": match.group(0)})
                images = [pymupdf.Rect(im["bbox"]) for im in page.get_image_info()]
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        for span in line["spans"]:
                            rect = pymupdf.Rect(span["bbox"])
                            if not span["text"].strip():
                                continue
                            if not (page.rect + (-1, -1, 1, 1)).contains(rect):
                                outside.append({"page": i+1, "text": span["text"], "bbox": list(rect)})
                            for image in images:
                                area = (rect & image).get_area()
                                if area > 2 and area / max(rect.get_area(), 1) > 0.03:
                                    overlaps.append({"page": i+1, "text": span["text"], "bbox": list(rect), "image_bbox": list(image), "overlap_area": round(area, 2)})
        return {"potential_image_text_overlaps": overlaps, "outside_page_spans": outside, "literal_markup_candidates": markup}

    result = {"review_status": "requires_human_review", "notes": "Signals can include legitimate image labels and proper nouns. They do not prove clipping, mistranslation or acceptance.", "unchanged_prose_candidates": unchanged, "tracker_error_entries": tracker_errors, "source_geometry": geometry(run / "source.pdf"), "output_geometry": geometry(manifest["output_pdf"])}
    from transformation.validation.integrity import check_run
    result["tracking_entries_without_text"] = missing_tracking_text
    result["final_pdf_integrity"] = check_run(run)
    (run / "quality-review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
