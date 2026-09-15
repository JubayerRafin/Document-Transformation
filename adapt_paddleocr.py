"""Convert PaddleOCR PP-StructureV3's raw output into the shared prediction
envelope described in docs/PREDICTION_FORMAT.md.

Do NOT hand-fix any misrecognized text here (e.g. a misread title) -- the
adapter only performs a mechanical format conversion so the evaluator can
score what the tool actually produced.
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import sys

_variant = sys.argv[1] if len(sys.argv) > 1 else "paddleocr-raw"
RAW_JSON = Path(f"experiments/{_variant}/page_1/Sample02_0_res.json")
SOURCE_PDF = "samples/Sample02.pdf"
OUT_NORMALIZED = Path(f"experiments/{_variant}/normalized.json")
OUT_SETTINGS = Path(f"experiments/{_variant}/settings.json")

# Kinds that are purely textual in PP-StructureV3's block_label taxonomy.
TEXT_LABELS = {
    "header", "text", "paragraph_title", "figure_title", "table_title",
    "doc_title", "footer", "reference", "abstract", "content",
}
# A full-page bitmap would be "raster_page"; PP-StructureV3 does not emit
# whole-page images for this sample, so every "image" block is a figure.
IMAGE_LABELS = {"image", "chart", "seal"}
TABLE_LABELS = {"table"}


class TableHTMLParser(HTMLParser):
    """Minimal HTML table parser: <table><tr><td>...</td></tr></table> -> rows."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell_chunks = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell_chunks = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._row.append("".join(self._cell_chunks).strip())
            self._in_cell = False
        elif tag == "tr":
            if self._row is not None:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell_chunks.append(data)


def html_table_to_cells(html: str):
    parser = TableHTMLParser()
    parser.feed(html)
    return parser.rows


def main():
    doc = json.loads(RAW_JSON.read_text(encoding="utf-8"))

    img_w, img_h = doc["width"], doc["height"]
    # Reference page dimensions (PDF points, from references/Sample02.json)
    pdf_w, pdf_h = 612.0, 792.0
    sx, sy = pdf_w / img_w, pdf_h / img_h

    def to_pdf_bbox(bbox):
        left, top, right, bottom = bbox
        return [round(left * sx, 2), round(top * sy, 2),
                round(right * sx, 2), round(bottom * sy, 2)]

    blocks = sorted(doc["parsing_res_list"], key=lambda b: b["block_id"])

    regions = []
    counters = {"text": 0, "table": 0, "figure": 0}

    for block in blocks:
        label = block["block_label"]
        bbox = to_pdf_bbox(block["block_bbox"])

        if label in TABLE_LABELS:
            counters["table"] += 1
            rid = f"p1_table{counters['table']}"
            cells = html_table_to_cells(block["block_content"])
            regions.append({"id": rid, "kind": "table", "bbox": bbox, "cells": cells})
        elif label in IMAGE_LABELS:
            counters["figure"] += 1
            rid = f"p1_figure{counters['figure']}"
            regions.append({"id": rid, "kind": "figure", "bbox": bbox})
        elif label in TEXT_LABELS:
            counters["text"] += 1
            rid = f"p1_text{counters['text']}"
            text = block["block_content"].strip()
            regions.append({"id": rid, "kind": "text", "bbox": bbox, "text": text})
        else:
            # Unknown/unsupported block label: record but flag for the
            # limitations writeup instead of silently dropping it.
            counters["text"] += 1
            rid = f"p1_text{counters['text']}"
            text = block["block_content"].strip()
            regions.append({
                "id": rid, "kind": "text", "bbox": bbox, "text": text,
                "metadata": {"unmapped_block_label": label},
            })

    normalized = {
        "coordinate_system": "displayed-page points; top-left origin; bbox=[left,top,right,bottom]",
        "pages": [
            {
                "number": 1,
                "width": pdf_w,
                "height": pdf_h,
                "rotation": 0,
                "regions": regions,
            }
        ],
    }

    OUT_NORMALIZED.parent.mkdir(parents=True, exist_ok=True)
    OUT_NORMALIZED.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    settings = {
        "ocr_engine": "PaddleOCR PP-StructureV3",
        "ocr_version": "3.7.0",
        "paddlepaddle_version": "3.3.1",
        "ocr_weights_sha256": "unavailable: PaddleX official model hub does not publish per-file checksums for the auto-downloaded PP-OCRv5_server_det/rec weights used here",
        "ocr_mode": "automatic (PP-OCRv5_server_det + PP-OCRv5_server_rec, default PPStructureV3 model set)",
        "languages": ["en"],
        "layout_model": "PP-DocLayout_plus-L",
        "table_model": "SLANet_plus / SLANeXt_wired (wired-table classification via PP-LCNet_x1_0_table_cls)",
        "preprocessing": (
            "PPStructureV3 defaults (doc orientation classification + UVDoc unwarping enabled)"
            if _variant == "paddleocr-raw" else
            "use_doc_unwarping=False, use_doc_orientation_classify=False (disabled after the default run showed bbox drift vs reference on this born-digital, non-scanned PDF)"
        ) + "; enable_mkldnn=False (required to work around a PaddlePaddle 3.3.1 CPU oneDNN/PIR NotImplementedError on this machine, see https://github.com/PaddlePaddle/Paddle/issues/77340)",
        "hardware": "CPU only (no GPU used)",
    }
    OUT_SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {OUT_NORMALIZED} with {len(regions)} regions "
          f"({counters['text']} text, {counters['table']} table, {counters['figure']} figure)")
    print(f"Wrote {OUT_SETTINGS}")


if __name__ == "__main__":
    main()
