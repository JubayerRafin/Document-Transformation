# Existing runtime instrumentation

| Evidence/boundary | Code |
|---|---|
| Engine wall-clock spans | `transformation/profiling.py`: `timed`, writes `runtime-timing.jsonl` |
| BabelDOC stage timing | `transformation/pdf/stage_timing.py` |
| Reusable layout process | `transformation/pdf/layout_service.py`, `layout_cpu.py` |
| Document/figure/I/O workers | `transformation/pdf/document_runner.py`, `figure_runner.py`, `io_worker.py` |
| Source/OCR caching | `extraction/source_cache.py`, `transformation/ocr/cache.py` |
| Model transport/session reuse | `transformation/llm/http_session.py`, `ollama_native.py` |
| SSH transport and artifact recovery | `transformation/remote_pipeline.py`, `remote_worker.py` |
| Hosted agent | `tools/service/cloud_worker.py`; API: `cloud/src/index.mjs` |
| Saved batch audit | `tools/evaluation/runtime_audit.py`, `transport_audit.py` |
| Paired first/repeat runs | `tools/evaluation/benchmark.py` |
| Existing guidance | `docs/RUNTIME_OPTIMIZATION.txt`, `docs/SERVER_EXECUTION.txt` |

Read the selected manifest, stage logs, `runtime-timing.jsonl`, translation
request timings and batch index. Some timings overlap or omit process startup;
do not sum all available counters as though they were exclusive intervals.

Offline audits write a new report destination and do not rerun model inference:

```powershell
python -m tools.evaluation.runtime_audit --batch <batch-root> --output <new-audit-root>
python -m tools.evaluation.transport_audit --batch <batch-root> --output <new-audit-root>
```

Explicit benchmark with two prepared, comparable configs:

```powershell
python -m tools.evaluation.benchmark --first <first.json> --repeat <repeat.json> --session <new-session.json>
```

The benchmark validates matching settings, separate empty output roots and a new
shared OCR cache. It never rents a pod. It starts inference, uses the existing
GPU verification and preserves session evidence. Copy configuration structure
from a real current config; do not copy old SSH hosts, keys or secret values into
a reusable skill. `--resume` is not a substitute for a new experiment after a
code/policy change.

Useful tests: `test_runtime_optimization`, `test_layout_service`,
`test_source_extraction_cache`, `test_http_session`, `test_server_pacing`,
`test_remote_pipeline`, `test_pdf_io_worker` and `test_translation_efficiency`.

The native PDF runner accepts `translation_workers` (1/2/4, default 1) and
`ollama_parallelism` (declared server capacity, default 1). The declaration does
not configure or verify Ollama. A parallel worker installs
`pdf/translation_concurrency.py`: per-paragraph/context copies, serialized IL
commits and per-engine/text singleflight over the existing vendor cache.
Scanned/figure slot translation remains sequential. Test with
`test_translation_concurrency` and the pinned-vendor
`test_babeldoc_parallel_integration` before enabling a parallel profile.

`validation/overlap_index.py` filters disjoint rectangle pairs and retains the
original exact overlap calculation and report order. Benchmark saved outputs
with `python -m tools.evaluation.benchmark_validation <pdfs> --output <new.json>`;
its timings measure validation only, never full processing. Include exact full
report parity, randomized geometry and PII artifacts (`test_overlap_index`).

Hosted profiles can opt into `engine_options.performance_probe: true` for
source/settings hashes, initial model residency and sampled job-tree RSS / total
visible-GPU memory. These are sampled peaks; GPU usage can include other jobs.
`python -m tools.evaluation.job_performance <saved-job> --output <new.json>`
summarizes all nested transport/stage traces without source text. Concurrent
request-duration sums are not wall time; use the recorded maximum in flight.

Keep an instrumentation-only control package separate from optimized code for
an initial same-hardware baseline. Label model residency, translation cache and
OCR cache independently. Production deployment and Ollama changes remain
separate authorized operations; neither a config declaration nor unit tests
establishes a measured parallel speedup.

Opt-in hosted `engine_profile.persistent_ocr: true` reuses serial Paddle
predictors through `transformation/ocr/service.py` and
`tools/ocr/persistent_worker.py`. The cloud worker owns the private Unix-socket
child; it exits after 300 idle seconds or parent loss. No tensor batching or
concurrent Paddle inference is enabled. `tools/ocr/model_pool.py` keys predictors
by complete constructor settings. Recognition thresholds, raster policy and
verified handoff remain unchanged. Existing source/policy-hashed OCR cache also
retains verified empty results; no detected boxes never proves absence of text.

Per-crop `ocr-timing.json` separates detection/recognition and model-load/reuse.
Persistent OCR RSS is measured separately: it is a sibling of each engine job.
Test `test_persistent_ocr` and `test_ocr_service_linux` plus OCR/scan/PII checks.
A fresh-results test uses trusted engine options `ignore_cache: true`,
`ocr_cache_mode: "fresh"`, `ocr_cache_root: null`,
`source_extraction_cache: null`. Model residency is separate from result caching.
Do not delete saved evidence or unload Ollama. Candidate details and deployment
status: `docs/OCR_SESSION_EXPERIMENT.txt`, `output/ocr-session-20261024/`.
