"""Run PaddleOCR PP-StructureV3 on samples/Sample02.pdf and save raw output.

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
from pathlib import Path

from paddleocr import PPStructureV3

SAMPLE_PDF = Path("samples/Sample02.pdf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["nowarp", "default"], default="nowarp")
    args = parser.parse_args()
    out_dir = Path("experiments/paddleocr-raw" + ("-nowarp" if args.variant == "nowarp" else ""))
    if out_dir.exists():
        raise SystemExit(f"Output already exists: {out_dir}. Preserve or move the previous run first.")
    out_dir.mkdir(parents=True)
    print("Loading PP-StructureV3 pipeline...", flush=True)
    t0 = time.time()
    pipeline = PPStructureV3(
        enable_mkldnn=False,
        use_doc_unwarping=args.variant == "default",
        use_doc_orientation_classify=args.variant == "default",
    )
    print(f"Pipeline loaded in {time.time() - t0:.1f}s", flush=True)

    print(f"Running on {SAMPLE_PDF} ...", flush=True)
    t0 = time.time()
    output = pipeline.predict(input=str(SAMPLE_PDF))

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
        "source_pdf": str(SAMPLE_PDF),
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
