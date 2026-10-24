"""Run PaddleOCR PP-StructureV3 on a supplied PDF and save raw output.

Peter's task: extract text + tables + figures with PP-StructureV3 and keep
the original tool output for later conversion into the shared IR format
described in docs/PREDICTION_FORMAT.md.
"""
import os

# Work around a PaddlePaddle 3.x CPU oneDNN/PIR executor bug that crashes on
# some layout-detection ops (NotImplementedError: ConvertPirAttribute2RuntimeAttribute).
os.environ.setdefault("FLAGS_use_mkldnn", "false")
os.environ.setdefault("FLAGS_enable_pir_api", "0")

import json
import argparse
import time
import hashlib
from pathlib import Path

SAMPLE_PDF = Path("samples/Sample02.pdf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["nowarp", "default"], default="nowarp")
    parser.add_argument("--source", type=Path, default=SAMPLE_PDF)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"Source PDF not found: {source}")
    out_dir = args.output or Path("experiments/paddleocr-raw" + ("-nowarp" if args.variant == "nowarp" else ""))
    if out_dir.exists():
        raise SystemExit(f"Output already exists: {out_dir}. Preserve or move the previous run first.")
    from paddleocr import PPStructureV3
    out_dir.mkdir(parents=True)
    print("Loading PP-StructureV3 pipeline...", flush=True)
    t0 = time.time()
    pipeline = PPStructureV3(
        device="cpu",
        enable_mkldnn=False,
        use_doc_unwarping=args.variant == "default",
        use_doc_orientation_classify=args.variant == "default",
    )
    print(f"Pipeline loaded in {time.time() - t0:.1f}s", flush=True)

    print(f"Running on {source} ...", flush=True)
    t0 = time.time()
    output = pipeline.predict(input=str(source))

    results = []
    for i, res in enumerate(output):
        page_out_dir = out_dir / f"page_{i+1}"
        page_out_dir.mkdir(parents=True, exist_ok=True)
        # Save PaddleOCR's native JSON + visualization for inspection
        res.save_to_json(save_path=str(page_out_dir))
        res.save_to_img(save_path=str(page_out_dir))
        results.append(res.json if hasattr(res, "json") else str(res))

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s. Pages processed: {len(results)}", flush=True)

    manifest = {
        "tool": "PaddleOCR PP-StructureV3",
        "paddleocr_version": __import__("paddleocr").__version__,
        "source_pdf": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "device": "cpu",
        "variant": args.variant,
        "paddlepaddle_version": __import__("paddle").__version__,
        "pages_processed": len(results),
        "elapsed_seconds": elapsed,
    }
    with open(out_dir / "run_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
