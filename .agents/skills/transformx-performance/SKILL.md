---
name: transformx-performance
description: "Diagnose and measure TransformX document-processing runtime using stage timings, transport evidence and controlled per-PDF benchmarks. Use for engine or worker performance rather than unrelated UI copy changes."
---

# TransformX performance

Read [timing sources and benchmark commands](references/runtime.md). Start with
saved-run evidence before proposing another model or GPU run.

## Attribute the time correctly

For each PDF, record total elapsed time, server processing, upload/download,
analysis/extraction, OCR, model requests, reconstruction and validation where
measured. Missing spans are unknown; do not attribute all non-model time to
BabelDOC startup. Note the actual machine running each stage: SSH full-pipeline
execution and a remote Ollama-only endpoint place work differently.

Compare identical source hashes, model/quantization, GPU, CPU/RAM allocation,
page count, OCR policy, validation and output settings. Record warm caches and
model residency explicitly. A first pass is not automatically a cold model run.
Do not compare interrupted transport totals with complete runs or aggregate
different PDFs into an unsupported speedup claim.

## Optimize the measured boundary

Trace repeated workers, layout sessions, source extraction/OCR caches, HTTP
keepalive and server pacing using the modules in the reference. Retain cache
validation and source/policy hashes. Avoid improving measured time by disabling
OCR, validation, PII verification or whole-page coverage without the user's
explicitly chosen tradeoff. Never add filename-specific cache shortcuts.

For the cloud route, examine request count, PDF buffering, hash work and database
queries separately from GPU inference. The current Worker still handles binary
uploads in memory and lists per-job artifacts; do not describe direct uploads or
batched history queries as implemented without checking code.

## Validation and reporting

Run focused local tests for the changed boundary before a fresh benchmark. A
benchmark request can authorize inference; a request for an offline audit does
not. Keep each new run in a separate output root and never clear previous caches
or unload models silently to manufacture a cold measurement. If a worker or
transport fails, inspect ownership and persisted results before any retry.

Report a PDF-by-PDF before/after table with timing scope, absolute delta and
percentage only when both measurements are valid. Pair it with quality/blocker
changes. Identify remaining unmeasured stages and provider costs without claiming
a GPU speedup from mocked tests or from changed hardware.
