"""A single Docling conversion supplies all content types."""

import importlib.metadata
from importlib.util import find_spec
import json
import os
from pathlib import Path

from ..models import Document, Page, Region, clipped_box
from ..ocr_config import resolve_ocr_models


def extract(path, source_hash, output_dir, options, progress):
    # Use existing local models during document processing.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        RapidOcrOptions,
    )
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.document_converter import (
        DocumentConverter,
        PdfFormatOption,
    )
    from docling_core.types.doc import ContentLayer

    path = Path(path)
    output_dir = Path(output_dir)

    # Record the adapter configuration for reproducible comparisons.
    options["adapter_version"] = "1.1.0"
    options["included_content_layers"] = ["body", "furniture"]

    settings = PdfPipelineOptions()
    settings.accelerator_options = AcceleratorOptions(
        device="cpu",
        num_threads=4,
    )
    settings.enable_remote_services = False
    settings.allow_external_plugins = False
    settings.do_ocr = options.get("ocr", True)
    settings.do_table_structure = True
    settings.generate_page_images = True
    settings.generate_picture_images = True
    settings.images_scale = 1.5

    if settings.do_ocr:
        spec = find_spec("rapidocr")

        if spec is None:
            raise RuntimeError(
                "Install rapidocr and pre-download its OCR models, "
                "or turn OCR off for a native-text-only run."
            )

        model_dir = Path(
            options.get("ocr_model_dir")
            or Path(next(iter(spec.submodule_search_locations))) / "models"
        )

        progress("Verifying pinned OCR model checksums")
        model_paths, record = resolve_ocr_models(model_dir)

        settings.ocr_options = RapidOcrOptions(
            backend="onnxruntime",
            **model_paths,
        )

        options["ocr_models"] = {
            key: Path(value).name
            for key, value in model_paths.items()
        }

        record["engine_version"] = importlib.metadata.version("rapidocr")
        record["runtime_version"] = importlib.metadata.version("onnxruntime")
        options["ocr_configuration"] = record

    if options.get("artifacts_path"):
        settings.artifacts_path = options["artifacts_path"]

    progress("Running Docling layout, text, tables and pictures together")

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=settings,
            )
        }
    )

    converted = converter.convert(path, raises_on_error=True)
    status = str(
        getattr(converted.status, "value", converted.status)
    ).lower()

    if status != "success":
        raise RuntimeError(
            f"Docling did not fully complete (status: {status}). "
            "No successful result was exported."
        )

    native = converted.document

    # Preserve the original Docling output for inspection.
    (output_dir / "native-docling.json").write_text(
        json.dumps(
            native.export_to_dict(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = Document(
        path.name,
        source_hash,
        "docling",
        importlib.metadata.version("docling"),
        settings=options,
    )

    pages = {}

    for number, source in sorted(native.pages.items()):
        page = Page(
            int(number),
            float(source.size.width),
            float(source.size.height),
            0,
        )
        pages[int(number)] = page
        result.pages.append(page)

    # Include body content AND furniture such as headers and footers.
    # Traverse pictures to retain text nested inside figures.
    # Preserve Docling's item sequence.
    for item, level in native.iterate_items(
        traverse_pictures=True,
        included_content_layers={
            ContentLayer.BODY,
            ContentLayer.FURNITURE,
        },
    ):
        raw_label = getattr(item, "label", "")
        label = str(getattr(raw_label, "value", raw_label))

        if label == "table":
            kind = "table"
        elif label == "picture":
            kind = "figure"
        else:
            kind = "text"

        for occurrence, prov in enumerate(getattr(item, "prov", [])):
            page = pages[int(prov.page_no)]

            bb = prov.bbox.to_top_left_origin(page.height)
            box = clipped_box(
                [bb.l, bb.t, bb.r, bb.b],
                page.width,
                page.height,
            )

            if not box:
                page.warnings.append(
                    f"Skipped invalid geometry for {item.self_ref}."
                )
                continue

            cells = []

            if kind == "table":
                data = item.data
                cells = [
                    [None for _ in range(data.num_cols)]
                    for _ in range(data.num_rows)
                ]

                for cell in data.table_cells:
                    cells[
                        cell.start_row_offset_idx
                    ][
                        cell.start_col_offset_idx
                    ] = cell.text

            raw_layer = getattr(item, "content_layer", "")

            metadata = {
                "source_ref": item.self_ref,
                "source_label": label,
                "content_layer": str(
                    getattr(raw_layer, "value", raw_layer)
                ),
                "hierarchy_level": level,
                "occurrence": occurrence,
                "source_item": item.model_dump(mode="json"),
            }

            region = Region(
                id=f"p{page.number}_r{len(page.regions) + 1}",
                kind=kind,
                bbox=box,
                text=getattr(item, "text", ""),
                cells=cells,
                metadata=metadata,
            )

            page.regions.append(region)

    if not settings.do_ocr:
        result.warnings.append(
            "Docling OCR was disabled for this run; "
            "image-only text may be missing."
        )
    else:
        result.warnings.append(
            "OCR was enabled, but figure-contained text may still "
            "be omitted or misrecognized. Inspect image regions "
            "before using this output for redaction."
        )

    result.warnings.append(
        "Preserved source items in metadata; "
        "IR 0.1 does not guarantee exact font reconstruction."
    )

    return result