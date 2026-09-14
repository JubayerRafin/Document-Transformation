import hashlib
import json
import math
from pathlib import Path

COORDINATES = "displayed-page points; top-left origin; bbox=[left,top,right,bottom]"
TASKS = {"text", "tables", "figures"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_box(region, page):
    box = region.get("bbox", [])
    require(isinstance(region.get("id"), str) and bool(region["id"]), "Every region needs a non-empty string ID.")
    require(isinstance(box, list) and len(box) == 4 and all(type(n) in (int, float) and math.isfinite(n) for n in box), "Bounding boxes must contain four finite numbers.")
    x0, y0, x1, y1 = box
    require(0 <= x0 < x1 <= page["width"] + .01 and 0 <= y0 < y1 <= page["height"] + .01, f"Invalid/out-of-page box: {region['id']}")


def validate_cells(cells):
    require(isinstance(cells, list), "Table cells must be a rectangular list of rows.")
    require(all(isinstance(row, list) for row in cells), "Every table row must be a list.")
    require(not cells or all(len(row) == len(cells[0]) and len(row) > 0 for row in cells), "Table rows must be non-empty and have equal lengths.")
    require(all(value is None or isinstance(value, str) for row in cells for value in row), "Cell values must be strings or null; preserve numbers as strings.")


def validate_document(data, reference=False):
    require(isinstance(data, dict), "Document must be a JSON object.")
    digest = data.get("source_sha256", "")
    require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "A lowercase source PDF SHA-256 is required.")
    require(data.get("coordinate_system") == COORDINATES, "Use the shared displayed-page coordinate convention exactly.")
    if reference:
        require(data.get("reference_version") == "1.0", "Unsupported reference format.")
        require(data.get("reviewed") is True and bool(str(data.get("reviewed_by", "")).strip()), "Reference is a draft. Complete the annotations and set reviewed=true with reviewed_by before scoring.")
    else:
        require(data.get("schema_version") == "0.1.0", "Prediction must use IR schema_version 0.1.0.")
        require(all(isinstance(data.get(k), str) and data[k].strip() for k in ("backend", "backend_version")), "Predictions must identify the tool and version.")
        require(isinstance(data.get("settings"), dict), "Prediction settings must record the experiment configuration.")
    require(isinstance(data.get("pages"), list), "pages must be a list.")
    require(not reference or data["pages"], "Reference must contain at least one page.")
    page_ids, region_ids = set(), set()
    task_count = 0
    for page in data["pages"]:
        n = page.get("number")
        require(type(n) is int and n > 0 and n not in page_ids, "Page numbers must be unique positive integers.")
        page_ids.add(n)
        require(all(type(page.get(k)) in (int, float) and math.isfinite(page[k]) and page[k] > 0 for k in ("width", "height")), "Page dimensions must be finite and positive.")
        require(page.get("rotation", 0) in (0, 90, 180, 270), "Invalid page rotation.")
        if reference:
            tasks = page.get("tasks")
            require(isinstance(tasks, list) and len(tasks) == len(set(tasks)) and set(tasks) <= TASKS, "Page tasks must be a unique subset of text, tables, figures.")
            task_count += len(tasks)
            require(isinstance(page.get("text"), str), "Reference text must be a string, including when empty.")
            require(isinstance(page.get("tables"), list) and isinstance(page.get("figures"), list), "Reference tables and figures must be lists.")
            regions = [(r, "table") for r in page["tables"]] + [(r, "figure") for r in page["figures"]]
        else:
            require(isinstance(page.get("regions"), list), "Prediction regions must be a list.")
            regions = [(r, r.get("kind")) for r in page["regions"]]
        for region, kind in regions:
            require(kind in {"text", "table", "figure", "raster_page"}, "Unknown prediction region kind.")
            validate_box(region, page)
            require(region["id"] not in region_ids, "Region IDs must be unique in a document.")
            region_ids.add(region["id"])
            if kind == "text":
                require(isinstance(region.get("text"), str), "Text regions require a text string.")
            if kind == "table":
                validate_cells(region.get("cells"))
    require(not reference or task_count > 0, "Reference has no evaluated tasks.")


def load_protocol(path):
    protocol = read_json(path)
    require(protocol.get("protocol") == "extraction-eval-v1", "Unknown scoring protocol.")
    require(type(protocol.get("iou_threshold")) in (int, float) and 0 < protocol["iou_threshold"] <= 1, "IoU threshold must be greater than zero and at most one.")
    expected = {"text_normalization": "NFKC-collapse-whitespace-case-sensitive", "matching": "maximum-cardinality-iou-priority",
                "text_scope": "all-text-regions-in-predicted-order-excluding-table-cells", "table_scoring": "exact-normalized-grid-slots-and-dimensions"}
    for key, value in expected.items():
        require(protocol.get(key) == value, f"Unsupported scoring rule: {key}")
    return protocol
