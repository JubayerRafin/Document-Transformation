# Pipeline modules and evidence

Paths below are relative to the TransformX repository root. Recheck them if the
repository is reorganized; code takes precedence over historical handoff notes.

| Boundary | Existing implementation |
|---|---|
| Mode selection and model defaults | `transformation/workflows.py`: `run_workflow`, `run_translation`, `run_pii` |
| Main execution and artifacts | `transformation/pipeline.py`: `run_pipeline`, `redact_pdf` |
| Analysis before extraction | `transformation/analysis/document_analysis.py`, `document_profile.py` |
| PDF extraction and normalized content IR | `extraction/coordinator.py`: `extract_pdf`, `content_ir`, `text_regions` |
| Prior Deep Doc adapter | `extraction/deep_doc_adapter.py`, `extraction/deep_doc_stage1/provenance.json` |
| Text, tables and render/mask figures | `extraction/deep_doc_stage1/text_extractor.py`, `table_extractor.py`, `docling_table_extractor.py`, `image_extractor.py`; `extraction/figures.py` |
| Native and raster table evidence | `extraction/backends/native.py`, `raster_tables.py` |
| PaddleOCR and caching | `transformation/ocr/paddle_pipeline.py`, `paddle_handoff.py`, `remote_ocr.py`, `cache.py` |
| Context and BabelDOC association | `extraction/context_bridge.py`, `transformation/pdf/ir_bridge.py` |
| PDF execution | `transformation/pdf/runner.py`, `document_runner.py`, `babeldoc_worker.py`, `scan_reconstruction.py` |
| Full server execution via SSH | `transformation/remote_pipeline.py`, `remote_worker.py` |
| Outbound cloud worker | `tools/service/cloud_worker.py` calls `backend/engine_job.py` |

The cloud profile can select `engine_python` for the main process, `python` for
BabelDOC and `ocr_python` for PaddleOCR. `ocr_device: "gpu:0"` enables server GPU
OCR routing before importing the pipeline. Readiness probes the selected OCR
environment; it is still prerequisite evidence, not per-document GPU proof.

Optional hosted `engine_profile.persistent_ocr` uses a private serial Unix socket
and an OCR child owned by the cloud worker. See `transformation/ocr/service.py`,
`tools/ocr/persistent_worker.py` and `tools/ocr/model_pool.py`. Source/image hashes,
archive verification, coordinates, confidence and literal checks stay mandatory.
Models persist across crops/jobs until 300 idle seconds; result caching is a
separate policy. Check `output/ocr-session-20261024/` for deployment status;
presence of local code does not mean it is deployed.

Look for `document-analysis.json`, `source-ir.json`, `transformed-ir.json`,
`ir-translations.json`, `manifest.json`, `validation.json`,
`independent-validation.json`, OCR evidence and stage-specific logs under the
selected run. BabelDOC's native/transformed IR lives in its worker directories;
verify the actual artifact paths instead of assuming a single global IR file.

Focused tests, run with an environment satisfying their imports:

```powershell
python -m unittest tests.test_separate_workflows tests.test_deep_doc_integration tests.test_deep_doc_figures
python -m unittest tests.test_document_profile tests.test_nativeized_ir tests.test_remote_pipeline
```

OCR-specific boundaries additionally have `tests/test_paddle_pipeline.py`,
`test_empty_ocr_detection.py`, `test_ocr_raster_policy.py` and
`test_remote_ocr_transport.py`. Choose the affected cases; do not run paid model
inference merely to discover an entry point.

Dependencies are intentionally split across `requirements.txt`,
`requirements-deep-doc.txt`, `requirements-transformation.txt` and
`requirements-evaluation.txt`. BabelDOC is pinned to 0.6.4 with bitstring 4.4.0.
Do not upgrade that environment to resolve an unrelated UI or COMET dependency.
