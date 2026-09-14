# Adapter contract for other tools

Run any tool locally. Convert its output into the following normalized envelope. The importer does not know the vendor's native format; each teammate maps their tool's actual fields.

```json
{
  "coordinate_system": "displayed-page points; top-left origin; bbox=[left,top,right,bottom]",
  "pages": [
    {
      "number": 1,
      "width": 612,
      "height": 792,
      "rotation": 0,
      "regions": [
        {"id": "p1_text1", "kind": "text", "bbox": [50, 50, 400, 80], "text": "Example heading"},
        {"id": "p1_table1", "kind": "table", "bbox": [50, 120, 400, 220], "cells": [["Name", "Value"], ["Example", "10"]]},
        {"id": "p1_figure1", "kind": "figure", "bbox": [50, 300, 400, 500]}
      ]
    }
  ]
}
```

These are illustrative coordinates, not annotations for the supplied samples.

Rules:

- Page numbers are one-based. Page dimensions and rotation must match the displayed PDF page.
- Convert pixel coordinates into displayed PDF points using the actual rendered image width/height. If the tool uses bottom-left coordinates, convert the origin too. Do not merely change the coordinate label.
- IDs must be unique within a document.
- Keep text regions in the tool's predicted reading order. Include OCR text inside figures as text regions; do not include table cells again as body text.
- Tables use rectangular grids. Keep numbers as strings. Preserve line breaks in cell text; the scorer handles whitespace normalization. Keep merged-cell details in metadata for future metrics; the current scorer evaluates grid slots and dimensions only.
- A complete diagram/illustration is a `figure`. A full-page bitmap is `raster_page`, which is not a semantic-figure prediction.
- Keep missed pages or elements missing. Never copy reference annotations into predictions.
- Extra vendor information can remain in `metadata`. Retain the original output separately for auditing.
- No confidence thresholding, OCR repair or semantic cleanup happens in the importer. If you add it in your adapter, record it as part of the experiment settings.

Example settings file (fill in actual values, do not leave placeholders):

```json
{
  "ocr_engine": "actual engine",
  "ocr_version": "actual version",
  "ocr_weights_sha256": "actual checksum or an explicit unavailable explanation",
  "ocr_mode": "automatic or forced",
  "languages": ["actual language codes"],
  "layout_model": "actual model/version",
  "table_model": "actual model/version",
  "preprocessing": "describe changes, or none"
}
```

`import-prediction` adds `schema_version`, source PDF name/hash, tool/version and settings. The resulting file is evaluated exactly like a Docling or native `document.json`.

You do not need to install Docling to import or score another tool's predictions. The evaluation math uses the Python standard library; PDF identity/geometry checks in the importer use the base PyMuPDF dependency.
