"""Transparent native-PDF reference; no OCR or semantic layout model."""
from collections import defaultdict
import importlib.metadata
import pymupdf

from ..models import Document, Page, Region, clipped_box, intersection_fraction


def extract(path, source_hash, output_dir, options, progress):
    result = Document(path.name, source_hash, "native", importlib.metadata.version("pymupdf"), settings=options)
    result.warnings.append("Native reference only: geometric reading order, no OCR, no semantic figure detection.")
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf):
            progress(f"Extracting page {index + 1} of {len(pdf)}")
            target = Page(index + 1, page.rect.width, page.rect.height, page.rotation)
            result.pages.append(target)

            def display_box(bbox):
                return clipped_box(list(pymupdf.Rect(bbox) * page.rotation_matrix), target.width, target.height)

            tables = []
            try:
                for table in page.find_tables().tables:
                    box = display_box(table.bbox)
                    if box:
                        tables.append((table.bbox, Region("", "table", box, cells=table.extract(), metadata={
                            "rows": table.row_count, "columns": table.col_count,
                            "cell_bboxes": [display_box(c) if c else None for c in table.cells],
                            "method": "native ruled-table detection"})))
            except Exception as exc:
                target.warnings.append(f"Table detection failed: {type(exc).__name__}. Text remains available.")
            target.regions.extend(region for _, region in tables)

            # Assign words to tables before forming body text. This avoids silently
            # discarding a whole paragraph when only part of it overlaps a table.
            groups = defaultdict(list)
            table_word_counts = [0] * len(tables)
            for word in page.get_text("words", sort=True):
                assigned = False
                for t, (bbox, region) in enumerate(tables):
                    if intersection_fraction(word[:4], bbox) >= .65:
                        table_word_counts[t] += 1
                        assigned = True
                        break
                if not assigned:
                    groups[(word[5], word[6])].append(word)
            for words in groups.values():
                bbox = [min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words)]
                box = display_box(bbox)
                if box:
                    target.regions.append(Region("", "text", box, " ".join(w[4] for w in words), metadata={
                        "method": "native text layer", "words": [{"text": w[4], "bbox": display_box(w[:4])} for w in words]}))
            for t, (_, region) in enumerate(tables):
                region.metadata["assigned_native_words"] = table_word_counts[t]
                if table_word_counts[t] and not any(v for row in region.cells for v in row):
                    region.warnings.append("Table has native words but empty cells; inspect the source crop.")

            native_text = page.get_text().strip()
            image_boxes = []
            for info in page.get_image_info():
                box = display_box(info["bbox"])
                if not box or any(intersection_fraction(box, prior) > .98 and intersection_fraction(prior, box) > .98 for prior in image_boxes):
                    continue
                image_boxes.append(box)
                coverage = (box[2] - box[0]) * (box[3] - box[1]) / (target.width * target.height)
                kind = "raster_page" if coverage > .85 else "figure"
                region = Region("", kind, box, metadata={"method": "embedded raster occurrence", "pixel_width": info["width"], "pixel_height": info["height"]})
                if kind == "figure":
                    region.warnings.append("Raster occurrence, not a confirmed complete semantic figure.")
                target.regions.append(region)
            if not native_text:
                target.warnings.append("No usable native text found. OCR is required if this page contains text; this backend does not run OCR.")
            elif image_boxes:
                target.warnings.append("Text inside raster images has not been OCR-checked.")
            if page.get_drawings():
                target.warnings.append("Vector graphics are preserved in the preview but are not identified as semantic figures by this backend.")
            target.regions.sort(key=lambda r: (round(r.bbox[1] / 3), r.bbox[0], r.kind))
            for number, region in enumerate(target.regions, 1):
                region.id = f"p{index + 1}_r{number}"
    return result
