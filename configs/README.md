# Configuration

`config/` holds earlier extraction-lab defaults. `configs/` holds evaluation and
processing-worker configuration. Both paths are retained because existing tools
use them.

- `gpu-evaluation.example.json`: portable dataset/evaluation template. Copy to a
  new `*.local.json`, then fill in your actual document and interpreter paths.
- `hosted-engine.example.json`: example **engine_profile** fields for an outbound
  hosted worker. It is not a complete registered worker credential file.
- `*.local.json`: private machine-specific configs; intentionally ignored.

Register a hosted worker using the existing app/agent procedure in
[PRIVATE_GPU_WORKERS.md](../docs/PRIVATE_GPU_WORKERS.md). Registration writes its
private identity/token configuration. Never replace it with an example profile
or add it to Git. Keep Supabase server secrets in the platform secret store or
ignored `cloud/.dev.vars`; a browser publishable key is a different credential.

Fresh-result benchmarks use `ignore_cache: true`, `ocr_cache_mode: "fresh"`, and
disabled source-extraction result caching in the trusted engine options. Model
residency is independent. The example keeps normal cache reuse and persistent
OCR off until the server environment has been checked. Do not copy past RunPod
hosts or private key paths into shared templates.
