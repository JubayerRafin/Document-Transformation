from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import time
import uuid
import zipfile

import pymupdf

from .reconcile import reconcile


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def validate_pdf(path):
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("Use a PDF smaller than 50 MB for this prototype.")
    with path.open("rb") as stream:
        if b"%PDF-" not in stream.read(1024):
            raise ValueError("The uploaded file is not a PDF.")
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("Password-protected PDFs are not supported. Upload an unlocked copy.")
        if not pdf.is_pdf or not 1 <= len(pdf) <= 100:
            raise ValueError("Use a PDF containing 1 to 100 pages.")
        return len(pdf)


def render_assets(path, document, output_dir, progress):
    assets = output_dir / "assets"
    assets.mkdir()
    with pymupdf.open(path) as pdf:
        if len(pdf) != len(document.pages):
            raise ValueError("Backend returned an incomplete page set.")
        for target in document.pages:
            page = pdf[target.number - 1]
            # The IR and preview both use displayed-page coordinates.
            if abs(target.width - page.rect.width) > 2 or abs(target.height - page.rect.height) > 2:
                raise ValueError(f"Backend geometry differs from displayed page {target.number}; refusing a misaligned export.")
            target.rotation = page.rotation
            progress(f"Rendering page {target.number} and its region crops")
            scale = min(1.5, 2400 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image_name = f"assets/page-{target.number}.png"
            pix.save(output_dir / image_name)
            target.preview = image_name
            # Crop the displayed preview to avoid PDF rotation/origin ambiguity.
            from PIL import Image
            with Image.open(output_dir / image_name) as preview:
                sx, sy = preview.width / target.width, preview.height / target.height
                for region in target.regions:
                    if region.kind == "text":
                        continue
                    x0, y0, x1, y1 = region.bbox
                    import math
                    crop_box = (max(0, math.floor(x0 * sx)), max(0, math.floor(y0 * sy)),
                                min(preview.width, math.ceil(x1 * sx)), min(preview.height, math.ceil(y1 * sy)))
                    region.asset = f"assets/{region.id}.png"
                    preview.crop(crop_box).save(output_dir / region.asset)


def run_pipeline(path, output_root, backend="native", options=None, progress=lambda _: None):
    path, output_root = Path(path), Path(output_root)
    options = dict(options or {})
    if backend not in {"native", "docling"}:
        raise ValueError(f"Unknown backend: {backend}")
    if backend == "native":
        options["ocr"] = False
    validate_pdf(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    run_dir = output_root / f"{backend}-{digest[:10]}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True)
    started = time.perf_counter()
    manifest = {"status": "running", "source_name": path.name, "source_sha256": digest,
                "backend": backend, "options": options, "created_at": datetime.now(timezone.utc).isoformat(),
                "python": platform.python_version(), "platform": platform.platform()}
    write_json(run_dir / "manifest.json", manifest)
    try:
        if backend == "native":
            from .backends.native import extract
        else:
            from .backends.docling_backend import extract
        document = extract(path, digest, run_dir, options, progress)
        progress("Checking shared geometry and region relationships")
        reconcile(document)
        render_assets(path, document, run_dir, progress)
        document.validate()
        data = document.to_dict()
        counts = Counter(r.kind for p in document.pages for r in p.regions)
        warnings = len(document.warnings) + sum(len(p.warnings) + sum(len(r.warnings) for r in p.regions) for p in document.pages)
        manifest.update(status="completed", backend_version=document.backend_version, seconds=round(time.perf_counter() - started, 3),
                        pages=len(document.pages), regions=dict(counts), warnings=warnings,
                        quality_score=None, quality_note="Counts and runtime are diagnostics, not accuracy scores. Ground-truth evaluation is pending.")
        write_json(run_dir / "document.json", data)
        write_json(run_dir / "manifest.json", manifest)
        return run_dir, data, manifest
    except Exception as exc:
        manifest.update(status="failed", seconds=round(time.perf_counter() - started, 3), error=f"{type(exc).__name__}: {exc}")
        write_json(run_dir / "manifest.json", manifest)
        raise


def export_zip(run_dir):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(run_dir).rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(run_dir).as_posix())
    return buffer.getvalue()
