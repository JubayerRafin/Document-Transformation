# Engine, adapters and configuration

TransformX is split into an **engine** that transforms documents and **adapters** that feed it
jobs. The engine has no database, no user accounts and no hosting dependency, so a deployment can
replace everything around it (sign-in, storage, web front end) without changing it.

```
                    ┌─────────────────────────── engine (one job = one process) ───────────────────────────┐
 adapter ──job──>   │ backend/engine_job.py → transformation/pipeline.py                                    │
  · site worker     │   extraction (Deep Doc Extractor) · OCR (PaddleOCR) · PII (Qwen) · translation         │
  · engine service  │   (TranslateGemma) · reconstruction (BabelDOC) · checks                               │
  · test harness    └──────────────┬──────────────────────────────────────────────────┬─────────────────────┘
                                   │ HTTP (OpenAI-compatible)                          │ files
                          model servers (vLLM, local or remote)               job folder (input, output, logs)
```

## Adapters

| Adapter | File | Use |
|---|---|---|
| Site worker | `tools/service/cloud_worker.py` | Claims jobs from the hosted website (Cloudflare + Supabase), uploads results back. |
| Engine service | `tools/service/engine_server.py` | Standalone HTTP API with no database or accounts, for internal deployments (for example Docker). |
| Test harness | `evaluation/pii/pod_samples.py` | Runs a folder of PDFs with the same settings, for regression and speed tests. |

All three start a job the same way: a `request.json` (`source`, `filename`, `options`, `profile`),
then `python -m backend.engine_job request.json events.jsonl terminal.json`. Progress is appended
to `events.jsonl`; the result (status, output file, warnings, failure code) is written to
`terminal.json`.

## Engine service API

```bash
python tools/service/engine_server.py --config configs/engine.json --root /data/transformx --port 8080
```

| Method and path | Purpose |
|---|---|
| `GET /health` | `{"status": "ok", "queued": n, "running": job id or null}`; no sign-in needed |
| `POST /v1/jobs` | Body: the PDF (`Content-Type: application/pdf`, at most 50 MB). Query: `operation` (`translation`, `pii`, `combined`), `source_language`, `target_language`, `pii_categories` (comma-separated), `pii_style` (`box` or `label`). Optional header `X-TransformX-Options`: JSON with `terminology` (`{"term": "required translation"}`), `protected_values` and `document_context`. Returns `202 {"job_id": ...}`. |
| `GET /v1/jobs` | All jobs with status and creation time |
| `GET /v1/jobs/{id}` | `status` (`queued`, `running`, `generated`, `failed`), current `stage`, `failure_code`, `warnings`, `seconds` |
| `GET /v1/jobs/{id}/result` | The output PDF once `generated` |
| `DELETE /v1/jobs/{id}` | Cancels a queued or running job and deletes its files |

Example:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/pdf" --data-binary @report.pdf \
  "http://127.0.0.1:8080/v1/jobs?operation=translation&source_language=en&target_language=ko"
```

Jobs run one at a time on the GPU; others wait in a queue. Errors are returned as
`{"error": "CODE"}` with an HTTP status; document contents never appear in error messages.

## Configuration reference

The engine profile (`engine_profile` in the JSON config) says where the Python environments are and
which engine options to use. Everything below is read from the environment, so keys and addresses
never need to be written into job files.

### Model servers

| Setting | Meaning |
|---|---|
| `TRANSFORMX_TRANSLATION_ENDPOINT`, `TRANSFORMX_PII_ENDPOINT` | Base URL of each model server, for example `http://127.0.0.1:8010/v1` or `https://llm.internal.example/team/v1` |
| `TRANSFORMX_TRANSLATION_MODEL`, `TRANSFORMX_PII_MODEL` | Model names as the server knows them. A model whose name contains `translategemma` gets TranslateGemma's prompt format. |
| `TRANSFORMX_OPENAI_COMPATIBLE` | Comma-separated OpenAI-compatible servers. **A server on another machine is used only if it is listed here.** |
| `TRANSFORMX_ALLOW_HTTP_REMOTE` | `1` allows plain `http://` to listed remote servers (trusted internal network only); otherwise remote servers must use `https://`. |
| `TRANSFORMX_TRANSLATION_API_KEY`, `TRANSFORMX_PII_API_KEY` (or `..._FILE`) | Bearer key sent to the matching endpoint |
| `TRANSFORMX_MODEL_API_KEY` (or `..._FILE`) | Bearer key for any other listed remote server; never sent to local servers |
| `TRANSFORMX_MODEL_TIMEOUT` | Seconds to wait for one model answer (default 600) |
| `TRANSFORMX_CHAT_TEMPLATE_KWARGS` | `1`/`0`: send vLLM's "no thinking" switch (default: local servers only) |
| `TRANSFORMX_GPU_SWAP` | Model servers that take turns on one GPU (set by the start script on cards under 40 GB) |

Remote servers get up to two retries on 429, 502, 503 and 504 and on dropped connections; requests
to local servers are never repeated.

### Engine service and worker

| Setting | Meaning |
|---|---|
| `TRANSFORMX_SERVICE_API_KEY` (or `..._FILE`) | Engine service sign-in. Without it the service has none and must sit behind a proxy that provides it. |
| `TRANSFORMX_SERVICE_WORKERS` | Jobs processed at the same time (default 1) |
| `TRANSFORMX_SERVICE_RETENTION_HOURS` | Engine service: delete finished jobs after this many hours (default 24) |
| `TRANSFORMX_RUN_RETENTION_HOURS` | Site worker: delete local job folders after this many hours (default 72, `0` keeps them) |
| `TRANSFORMX_FIGURE_WORKERS` | Pictures translated at the same time (default 3; 1 when models share the GPU) |
| `TRANSFORMX_SHARED_LAYOUT` | `0` turns off the shared page-layout model (each run then loads its own) |
| `TRANSFORMX_PRELOAD` | `1` starts job processes from pre-loaded parents (saves import time) |

### Starting model servers without the site worker

```bash
bash tools/gpu/start_worker.sh --servers-only
```

starts the vLLM servers, sizes them for the GPU, and writes their settings (endpoints and model
names, never keys) to `/workspace/servers.env` for the engine service or another adapter.

## Languages and OCR

Translation works between English and Korean, Japanese, Chinese (Simplified), Arabic, Hindi,
Bengali, French, German, Spanish and Portuguese. Scanned pages and text in images are read with
PaddleOCR: English with `en_PP-OCRv5_mobile_rec` checked by `PP-OCRv5_server_rec`, Korean with
`korean_PP-OCRv5_mobile_rec`, other languages with `PP-OCRv5_server_rec`. Korean has no second
reader: a line is accepted when several differently prepared views agree, and a line with numbers
only when a second model reads the same digits on the line or on each word piece; otherwise the
line keeps its original pixels and is flagged for review.

## Speed (L40S, 19 November 2026)

Six sample documents (8 pages) into Korean, first-time translations (no cache), warm server:
about 100 s in total, 12.6 s per page (15.3 s per page before the 19 November changes). Repeated
documents reuse cached translations: about 10 s per page. A one-page digital document takes about
5–7 s. The target of 3–5 s per page is not reached yet; the largest remaining costs are Docling
extraction on the CPU (its pinned PyTorch build has no CUDA variant), the longest paragraph's
translation, and BabelDOC's per-run start-up.
