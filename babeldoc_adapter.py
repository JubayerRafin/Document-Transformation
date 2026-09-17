#!/usr/bin/env python
"""
BabelDOC -> TransformX normalized prediction adapter.

Input:
    BabelDOC debug/intermediate JSON, e.g.
    experiments/babeldoc-sahil-working/Sample02/paragraph_finder.json

Output:
    docs/PREDICTION_FORMAT.md-compatible normalized JSON.

Important:
- BabelDOC coordinates in this intermediate JSON use bottom-left origin.
- TransformX requires displayed-page PDF points with top-left origin.
- BabelDOC exposes a table region but not an explicit row/column/cell grid here.
  For Sample02, this adapter reconstructs a rectangular grid from BabelDOC's own
  positioned fallback-line text only. It does NOT use the reference JSON.
- Image-form boxes are mapped to figure predictions. Full-page image forms are
  excluded from semantic figures.
"""

import argparse
import json
from pathlib import Path


def bbox_to_top_left(box, page_height):
    return [
        float(box["x"]),
        float(page_height - box["y2"]),
        float(box["x2"]),
        float(page_height - box["y"]),
    ]


def center(box):
    return ((box["x"] + box["x2"]) / 2.0, (box["y"] + box["y2"]) / 2.0)


def point_inside(box, outer, pad=2.0):
    cx, cy = center(box)
    return (
        outer["x"] - pad <= cx <= outer["x2"] + pad
        and outer["y"] - pad <= cy <= outer["y2"] + pad
    )


def cluster_numeric(values, threshold):
    values = sorted(values)
    clusters = []
    for value in values:
        if not clusters or value - clusters[-1][-1] > threshold:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(c) / len(c) for c in clusters]


def reconstruct_table_grid(page, table_box):
    """
    Reconstruct table slots only from BabelDOC-positioned paragraph text.

    This is adapter-side geometry post-processing, not native structured-cell
    output from BabelDOC. The behavior must be reported in experiment settings.
    """
    lines = []
    for p in page.get("pdf_paragraph", []):
        if p.get("layout_label") is None:
            continue
        text = str(p.get("unicode", "")).strip()
        box = p.get("box")
        if not text or not box or not point_inside(box, table_box):
            continue

        cx, cy = center(box)
        lines.append(
            {
                "text": text,
                "box": box,
                "x": float(box["x"]),
                "cx": cx,
                "cy": cy,
            }
        )

    if not lines:
        return []

    # Detect columns from strong gaps between left-edge x positions.
    table_width = max(1.0, float(table_box["x2"] - table_box["x"]))
    x_threshold = table_width / 8.0
    x_centers = cluster_numeric(
        sorted(set(round(item["x"], 3) for item in lines)),
        x_threshold,
    )
    if len(x_centers) < 2:
        return []

    for item in lines:
        item["col"] = min(
            range(len(x_centers)),
            key=lambda i: abs(item["x"] - x_centers[i]),
        )

    # First group text by near-equal baseline/vertical center.
    y_groups = []
    for item in sorted(lines, key=lambda x: -x["cy"]):
        match = None
        for group in y_groups:
            mean_y = sum(x["cy"] for x in group) / len(group)
            if abs(item["cy"] - mean_y) <= 4.0:
                match = group
                break
        if match is None:
            y_groups.append([item])
        else:
            match.append(item)

    # A main table row normally has content in 2+ columns.
    # Single-column groups are treated as wrapped continuation lines and
    # attached to the nearest main row.
    main_groups = [
        group for group in y_groups
        if len({item["col"] for item in group}) >= 2
    ]
    if not main_groups:
        return []

    row_anchors = [
        sum(item["cy"] for item in group) / len(group)
        for group in main_groups
    ]

    cell_items = {}
    for item in lines:
        row = min(
            range(len(row_anchors)),
            key=lambda i: abs(item["cy"] - row_anchors[i]),
        )
        cell_items.setdefault((row, item["col"]), []).append(item)

    grid = [["" for _ in x_centers] for _ in row_anchors]
    for (row, col), items in cell_items.items():
        # In bottom-left coordinates, the visually upper line has larger y.
        items = sorted(items, key=lambda x: -x["cy"])
        grid[row][col] = "\n".join(item["text"] for item in items)

    return grid


def adapt_page(page):
    media = page["mediabox"]["box"]
    width = float(media["x2"] - media["x"])
    height = float(media["y2"] - media["y"])

    layouts = page.get("page_layout", [])
    table_layouts = [
        obj for obj in layouts if obj.get("class_name") == "table"
    ]
    table_boxes = [obj["box"] for obj in table_layouts]

    regions = []

    # Text: keep actual BabelDOC paragraph objects, excluding table-cell text.
    text_items = []
    for p in page.get("pdf_paragraph", []):
        # Entries with layout_label=None are BabelDOC debug labels, not source text.
        if p.get("layout_label") is None:
            continue
        text = str(p.get("unicode", "")).strip()
        box = p.get("box")
        if not text or not box:
            continue
        if any(point_inside(box, tbox) for tbox in table_boxes):
            continue

        bbox = bbox_to_top_left(box, height)
        text_items.append((bbox, text, p))

    # Use geometric reading order (top -> bottom, then left -> right).
    # This is documented adapter-side ordering because BabelDOC's render_order
    # follows PDF rendering order rather than reliable human reading order here.
    text_items.sort(key=lambda item: (round(item[0][1], 3), round(item[0][0], 3)))

    for idx, (bbox, text, p) in enumerate(text_items, start=1):
        regions.append(
            {
                "id": f"p{int(page.get('page_number', 0)) + 1}_text{idx}",
                "kind": "text",
                "bbox": [round(x, 6) for x in bbox],
                "text": text,
                "metadata": {
                    "babeldoc_layout_label": p.get("layout_label"),
                    "babeldoc_layout_id": p.get("layout_id"),
                    "babeldoc_render_order": p.get("render_order"),
                },
            }
        )

    # Tables: BabelDOC layout exposes table boxes. Structured cells are not
    # exposed directly in the inspected object, so reconstruct from positioned
    # text in the same table region.
    for idx, layout in enumerate(table_layouts, start=1):
        box = layout["box"]
        regions.append(
            {
                "id": f"p{int(page.get('page_number', 0)) + 1}_table{idx}",
                "kind": "table",
                "bbox": [round(x, 6) for x in bbox_to_top_left(box, height)],
                "cells": reconstruct_table_grid(page, box),
                "metadata": {
                    "babeldoc_layout_id": layout.get("id"),
                    "babeldoc_class_name": layout.get("class_name"),
                    "babeldoc_confidence": layout.get("conf"),
                    "cell_grid_source": (
                        "adapter geometry reconstruction from BabelDOC "
                        "positioned paragraph text; BabelDOC table layout "
                        "object did not expose rows/columns/cells directly"
                    ),
                },
            }
        )

    # Figures: map BabelDOC image forms to semantic figure predictions.
    # Exclude near-full-page images because the TransformX contract treats
    # those as raster pages rather than semantic figures.
    page_area = max(1.0, width * height)
    seen = set()
    figure_idx = 0
    for form in page.get("pdf_form", []):
        if form.get("form_type") != "image":
            continue
        box = form.get("box")
        if not box:
            continue
        area = max(0.0, (box["x2"] - box["x"]) * (box["y2"] - box["y"]))
        if area / page_area >= 0.80:
            continue

        bbox = tuple(round(x, 6) for x in bbox_to_top_left(box, height))
        if bbox in seen:
            continue
        seen.add(bbox)
        figure_idx += 1
        regions.append(
            {
                "id": f"p{int(page.get('page_number', 0)) + 1}_figure{figure_idx}",
                "kind": "figure",
                "bbox": list(bbox),
                "metadata": {
                    "babeldoc_form_type": form.get("form_type"),
                    "babeldoc_render_order": form.get("render_order"),
                    "figure_mapping": "non-full-page BabelDOC image form",
                },
            }
        )

    return {
        "number": int(page.get("page_number", 0)) + 1,
        "width": width,
        "height": height,
        "rotation": 0,
        "regions": regions,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", help="BabelDOC intermediate JSON")
    parser.add_argument("output_json", help="TransformX normalized prediction JSON")
    args = parser.parse_args()

    source = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    pages = [adapt_page(page) for page in source.get("page", [])]

    out = {
        "coordinate_system": (
            "displayed-page points; top-left origin; "
            "bbox=[left,top,right,bottom]"
        ),
        "pages": pages,
    }

    Path(args.output_json).write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    text_count = sum(
        r["kind"] == "text" for p in pages for r in p["regions"]
    )
    table_count = sum(
        r["kind"] == "table" for p in pages for r in p["regions"]
    )
    figure_count = sum(
        r["kind"] == "figure" for p in pages for r in p["regions"]
    )
    print(f"Saved: {args.output_json}")
    print(
        f"Pages={len(pages)} Text={text_count} "
        f"Tables={table_count} Figures={figure_count}"
    )
    for page in pages:
        for region in page["regions"]:
            if region["kind"] == "table":
                cells = region.get("cells", [])
                rows = len(cells)
                cols = len(cells[0]) if cells else 0
                print(
                    f"{region['id']}: {rows}x{cols}, "
                    f"bbox={region['bbox']}"
                )


if __name__ == "__main__":
    main()
