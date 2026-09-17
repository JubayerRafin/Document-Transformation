"""Convert PaddleOCR PP-StructureV3's raw output into the shared prediction
envelope described in docs/PREDICTION_FORMAT.md.

Do NOT hand-fix any misrecognized text here (e.g. a misread title) -- the
adapter only performs a mechanical format conversion so the evaluator can
score what the tool actually produced.
"""
import json
from html.parser import HTMLParser
from pathlib import Path

import argparse
import pymupdf

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
            if any(k in {"rowspan", "colspan"} and v != "1" for k, v in attrs):
                raise ValueError("Merged cells are not supported by this Sample02 adapter.")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", nargs="?", choices=["paddleocr-raw-nowarp", "paddleocr-raw"], default="paddleocr-raw-nowarp")
    parser.add_argument("--allow-unmapped-geometry", action="store_true",
                        help="Reproduce historical dewarped scores; boxes are NOT valid original-page geometry")
    args = parser.parse_args()
    _variant = args.variant
    root = Path("experiments") / _variant
    OUT_NORMALIZED = root / "normalized.json"
    OUT_SETTINGS = root / "settings.json"
    doc = json.loads((root / "page_1/Sample02_0_res.json").read_text(encoding="utf-8"))
    preprocessing = doc.get("doc_preprocessor_res", {})
    transformed = preprocessing.get("model_settings", {}).get("use_doc_unwarping", False) or preprocessing.get("angle", -1) not in (-1, 0)
    if transformed and not args.allow_unmapped_geometry:
        raise ValueError("Dewarped/rotated boxes require inverse mapping. Use the nowarp run for valid geometry evaluation.")
    with pymupdf.open("samples/Sample02.pdf") as pdf:
        if len(pdf) != 1 or doc.get("page_count", 1) != 1:
            raise ValueError("This Sample02 adapter supports one-page PDFs only.")
        pdf_w, pdf_h = pdf[0].rect.width, pdf[0].rect.height
        rotation = pdf[0].rotation

    img_w, img_h = doc["width"], doc["height"]
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
                "rotation": rotation,
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
    # Keep the recorded environment for historical raw output. Never label
    # a new machine's run with the original author's package versions.
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    settings["ocr_version"] = manifest["paddleocr_version"]
    settings["paddlepaddle_version"] = manifest.get("paddlepaddle_version", "unrecorded; historical submission reported 3.3.1")
    settings["geometry_status"] = "unmapped transformed coordinates; geometry scores invalid" if transformed else "untransformed image scaled to PDF points"
    OUT_SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {OUT_NORMALIZED} with {len(regions)} regions "
          f"({counters['text']} text, {counters['table']} table, {counters['figure']} figure)")
    print(f"Wrote {OUT_SETTINGS}")


if __name__ == "__main__":
    main()
