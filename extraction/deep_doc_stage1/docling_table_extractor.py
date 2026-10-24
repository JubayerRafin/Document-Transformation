"""
docling_table_extractor.py
--------------------------
Table structure extraction using Docling (IBM Research, TableFormer/TATR
lineage). Produces the same JSON shape and ExtractedElement format as the
Camelot/pdfplumber path so it is a drop-in primary extractor.

Docling handles borderless and irregular tables that geometry-based methods
(Camelot lattice/stream, pdfplumber) cannot — it detects table structure with
a layout model and matches cells back to the PDF text tokens.

Models are downloaded once (HuggingFace cache) and then run fully offline on
CPU, the same pattern as the sentence-transformers embedding model already
used elsewhere in the pipeline.

Team DocuForge | Woosong University Capstone 2026
"""
import os
import json
import tempfile

from .extracted_element import ExtractedElement


class DoclingTableExtractor:
    """Primary table extractor backed by Docling. Falls back gracefully:
    if Docling is unavailable or errors, the caller can use the legacy
    Camelot/pdfplumber path."""

    def __init__(self, config: dict, output_dir: str):
        tables_cfg      = config.get("tables", {})
        subdir          = tables_cfg.get("output_subdir", "tables")
        self.tables_dir = os.path.join(output_dir, subdir)
        os.makedirs(self.tables_dir, exist_ok=True)

        self.available = False
        self._converter = None
        self._init_docling()

    def _init_docling(self):
        """Build a CPU, no-OCR Docling converter. Sets self.available."""
        try:
            from docling.document_converter import (
                DocumentConverter, PdfFormatOption)
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions

            opts = PdfPipelineOptions()
            opts.do_table_structure = True
            opts.do_ocr = False                 # born-digital manuals
            opts.generate_page_images = False   # save memory
            try:
                from docling.datamodel.accelerator_options import (
                    AcceleratorOptions, AcceleratorDevice)
                opts.accelerator_options = AcceleratorOptions(
                    device=AcceleratorDevice.CPU)
            except Exception:
                pass

            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
                }
            )
            self.available = True
        except Exception as e:
            print(f"  [DoclingTableExtractor] Docling unavailable: {e}")
            self.available = False

    # ── Public API ──────────────────────────────────────────────────

    def extract_page(self, pdf_path: str, page_num: int,
                     start_index: int = 0) -> list:
        """Extract tables from a single page. Processes one page at a time
        (via a temp single-page PDF) to keep memory bounded on large manuals.
        Returns a list of ExtractedElement, or [] if none / on error."""
        if not self.available:
            return []
        one_pdf = None
        try:
            one_pdf = self._single_page_pdf(pdf_path, page_num)
            result = self._converter.convert(one_pdf)
            doc = result.document
        except Exception as e:
            print(f"  [DoclingTableExtractor] page {page_num} failed: {e}")
            if one_pdf and os.path.exists(one_pdf):
                os.remove(one_pdf)
            return []

        elements = []
        idx = start_index
        for tbl in doc.tables:
            try:
                table_json = self._table_to_json(tbl, page_num, idx, doc)
                if table_json is None:
                    continue
                fname = f"table_p{page_num}_{idx}.json"
                fpath = os.path.join(self.tables_dir, fname)
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(table_json, f, ensure_ascii=False, indent=2)
                elements.append(ExtractedElement(
                    page_num=page_num,
                    y_coordinate=self._table_y(tbl),
                    element_type="table",
                    content=json.dumps(table_json, ensure_ascii=False),
                ))
                idx += 1
            except Exception as e:
                print(f"  [DoclingTableExtractor] table export failed "
                      f"(p{page_num}): {e}")
        if one_pdf and os.path.exists(one_pdf):
            os.remove(one_pdf)
        return elements

    # ── Helpers ─────────────────────────────────────────────────────

    def _single_page_pdf(self, pdf_path: str, page_num: int) -> str:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        writer.add_page(reader.pages[page_num - 1])
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(tmp, "wb") as f:
            writer.write(f)
        return tmp

    def _table_to_json(self, tbl, page_num, table_index, doc) -> dict:
        """Convert a Docling table to the pipeline's JSON shape:
        {page, table_index, headers, rows}."""
        try:
            df = tbl.export_to_dataframe(doc)
        except TypeError:
            df = tbl.export_to_dataframe()  # older docling signature
        if df is None or df.shape[0] == 0:
            return None

        headers = [str(c).strip() for c in df.columns]
        # de-duplicate / fill blank header names so JSON keys stay unique
        seen, uniq = {}, []
        for i, h in enumerate(headers):
            if not h:
                h = f"Column_{i}"
            if h in seen:
                seen[h] += 1
                h = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
            uniq.append(h)
        headers = uniq

        rows = []
        for _, row in df.iterrows():
            rows.append({h: ("" if v is None else str(v).strip())
                         for h, v in zip(headers, row)})

        return {
            "page":        page_num,
            "table_index": table_index,
            "headers":     headers,
            "rows":        rows,
            "source":      "docling",
        }

    def _table_y(self, tbl) -> float:
        """Best-effort top y-coordinate for reading-order sorting."""
        try:
            prov = tbl.prov[0]
            # docling bbox is top-left origin in most versions
            return float(prov.bbox.t)
        except Exception:
            return 0.0
