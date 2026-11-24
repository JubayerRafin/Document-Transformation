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
| `POST /v1/jobs` | Body: the PDF (`Content-Type: application/pdf`, at most 50 MB). Query: `operation` (`translation`, `pii`, `combined`), `source_language`, `target_language`, `pii_categories` (comma-separated), `pii_style` (`box` or `label`), `translation_model` (a name from `/v1/translation-models`). Optional header `X-TransformX-Options`: JSON with `terminology` (`{"term": "required translation"}`), `protected_values` and `document_context`. Returns `202 {"job_id": ...}`. |
| `GET /v1/jobs` | All jobs with status and creation time |
| `GET /v1/translation-models` | Translation models a job may choose (`id`, `label`); see the settings file |
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

## Settings file

One file controls the whole system: `configs/transformx.toml` (or the file named by
`TRANSFORMX_SETTINGS`). Copy [`configs/transformx.example.toml`](../configs/transformx.example.toml),
which explains every setting, and change what you need. The site worker, the engine service, the
start script and the test harness all read it. Without a file everything works from environment
variables as before.

```bash
cp configs/transformx.example.toml configs/transformx.toml
python -m transformation.settings check   # validate the file, key files and model servers
python -m transformation.settings show    # settings in effect and where each came from
```

What wins, lowest first: built-in defaults < the site's worker connection file < `transformx.toml`
< environment variables, so a container can override one value without editing the file. Keys never
go in the file: each `key_file` names a file holding the key. `check` names every mistake at once
(unknown setting, wrong type, missing key file, a server that does not answer, a model it does not
serve) and `show` never prints keys.

Using an OpenAI-compatible server on another machine for translation:

```toml
[translation]
endpoint = "https://api.openai.com/v1"
model = "gpt-5-mini"
key_file = "/run/secrets/openai_key"
requests_per_second = 5

[models]
remote_servers = ["https://api.openai.com/v1"]
```

### Letting users choose the translation model

Each `[translation_models.<name>]` section adds a model users can pick for a job; `[translation]`
stays the default. On the website the upload form then shows a **Translation model** list for that
GPU server (the Workers panel lists them too); the engine service takes `?translation_model=<name>`
and lists the choices at `GET /v1/translation-models`.

```toml
[translation]
label = "Standard (this server)"

[translation_models.openai]
label = "OpenAI GPT-5 mini"
endpoint = "https://api.openai.com/v1"
model = "gpt-5-mini"
key_file = "/run/secrets/openai_key"
requests_per_second = 5
```

The worker offers a model only while its server answers and serves it (checked every 45 s). Only
the name and whether text leaves the machine reach the website; addresses and keys stay on the
server. A job's chosen server and key file are given to that job's process only, and the default
model's key is never sent to another server. Users see a notice before choosing a model on another
machine.

### The higher-quality model on the same GPU (TranslateGemma 27B)

On a self-hosted server, `tools/gpu/start_worker.sh` adds one more choice without a settings entry
when `/workspace/models/translategemma-27b` holds verified weights
(`TRANSFORMX_HIGH_QUALITY_SETUP=1 bash tools/gpu/setup_vllm.sh`): **TranslateGemma 27B · higher
quality, slower** (id `high_quality`). It writes three settings to `servers.env`:

| Setting | Value on an L40S | Meaning |
|---|---|---|
| `TRANSFORMX_HIGH_QUALITY_TRANSLATION` | `http://127.0.0.1:8011/v1,translategemma:27b` | The choice's server and model (`transformation/settings.py` adds it, marked exclusive) |
| `TRANSFORMX_GPU_SWAP` | `http://127.0.0.1:8000+http://127.0.0.1:8010,http://127.0.0.1:8011` | Servers joined by `+` stay awake together; 27B is a group of its own |
| `TRANSFORMX_OPENAI_COMPATIBLE` | `…,http://127.0.0.1:8011` | 27B is called like the other vLLM servers |

At start the script unloads any model Ollama holds, puts the PII model and 12B to sleep, starts 27B
with 78% of the GPU (`TRANSFORMX_HIGH_QUALITY_GPU_SHARE`), puts it to sleep and wakes the other two.
If 27B does not start, the other two wake and the server runs without the choice
(`TRANSFORMX_HIGH_QUALITY=off` skips it). A job that chooses it starts only when no other job runs,
and no job starts beside it (`Store.claim_next` in `tools/service/local_server.py`); before each
model call `gpu_share.ensure` gives the GPU to the right group. Such a job never calls the PII model
as a second translator mid-translation (`TRANSFORMX_SECOND_TRANSLATION_MODEL=off`), since waking it
would put 27B to sleep under the other paragraphs.

Measured on 1,997 held-out paragraphs (24 November 2026, `evaluation/suite/model_ab.py`): MetricX-24
2.09 → 1.96 and with references 1.83 → 1.67, COMET-22 0.920 → 0.919, twice as long per paragraph.
In a live test a translation job took about 18 s per page on 27B, and translate-and-redact 15.8 s
against 10.6 s on the default model.

## Configuration reference

The sections below list the environment variables behind each setting. The engine profile
(`engine_profile` in a JSON config) says where the Python environments are and which engine options
to use; keys and addresses never need to be written into job files.

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

Requests to remote servers use OpenAI's current fields (`max_completion_tokens`; `reasoning_effort`
when `TRANSFORMX_REASONING_EFFORT` / `models.reasoning_effort` is set). When a model refuses a field
(for example a fixed temperature, which newer OpenAI models do not accept), the request is sent again
without it once and that model is not sent it again. Local servers get exactly the request they
always had.

Every translation model goes through the same checked path: long paragraphs are split, each answer
has a length budget, protected values must come back exactly, answers in a third script are
rejected, and a paragraph that still fails after a retry is kept in its source language and flagged
for review instead of failing the document. TranslateGemma gets its documented prompt format; any
other chat model (OpenAI, Qwen, ...) gets the same instructions as a system message, with the
document text alone as the user message.

### Engine service and worker

| Setting | Meaning |
|---|---|
| `TRANSFORMX_SERVICE_API_KEY` (or `..._FILE`) | Engine service sign-in. Without it the service has none and must sit behind a proxy that provides it. |
| `TRANSFORMX_SERVICE_WORKERS` | Jobs processed at the same time (default 1) |
| `TRANSFORMX_SERVICE_RETENTION_HOURS` | Engine service: delete finished jobs after this many hours (default 24) |
| `TRANSFORMX_RUN_RETENTION_HOURS` | Site worker: delete local job folders after this many hours (default 72, `0` keeps them) |
| `TRANSFORMX_MIN_FREE_GB` | Site worker: report not ready and take no jobs while the job disk has less free space (default 5) |
| `timeout_seconds` (engine profile; `[output] job_timeout_seconds` in the settings file) | Seconds a job may run (default 3600). The site worker adds 30 s for every page beyond 100, up to 24 hours. |
| `TRANSFORMX_FIGURE_WORKERS` | Pictures translated at the same time (default 3; 1 when models share the GPU) |
| `TRANSFORMX_SHARED_LAYOUT` | `0` turns off the shared page-layout model (each run then loads its own) |
| `TRANSFORMX_PRELOAD` | `1` starts job processes from pre-loaded parents (saves import time); restart them after a code update (`tools/gpu/restart_worker.sh`) |
| `TRANSFORMX_TRACE_PARAGRAPHS` | `1` writes `paragraph-trace.jsonl`: every paragraph BabelDOC prepared for translation (finding text left untranslated without an error) |
| `TRANSFORMX_BABELDOC_DEBUG` | `1` keeps BabelDOC's intermediate files. A failure's traceback is always written to the job's `process.log` |

### Testing many documents

`evaluation/robustness/make_corpus.py` generates varied test PDFs (reports, invoices, forms,
contracts, slides, newsletters, manuals, papers, timetables, letters; scans, rotated pages, text in
pictures, edge cases, long documents; English, Korean, Japanese, Chinese, Arabic, Hindi and Bengali
sources). `evaluation/pii/pod_samples.py` runs a folder of them, `evaluation/robustness/check_outputs.py`
checks every output and `evaluation/robustness/report.py` collects a before/after summary with
sample source/output pairs and side-by-side images. Use `--set heldout` for documents not used
while fixing, and measure those only.

The checker reports, per document: whether it was generated (else the failure code), page count,
protected values missing from the output, letters of a third script, untranslated source lines,
letters still in the source's own script, unread glyphs (`(cid:N)`), answers about the translation
instead of one ("Here is the translation", "please provide the text"), missing glyphs and text
outside the page. For generated documents translated into English it also checks meaning: each
known generator sentence must come out with its key English words. Without that check, invented
text passed as a translation (Arabic sources, 19 November 2026). "Generated" is not "good": read
some outputs by eye as well.

```bash
python -m evaluation.robustness.check_outputs RESULTS SOURCES --target ko [--source en] [--review DIR]
python -m evaluation.robustness.report OUT --run LABEL=RESULTS:SOURCES:TARGET:SOURCE \
  [--before LABEL=EARLIER_RESULTS] [--samples 2]
```

A mass run writes about 50 MB of intermediate files per document; delete the BabelDOC `work`
folders of finished documents (results, summaries and logs stay) and keep 20 GB or more free.

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
`korean_PP-OCRv5_mobile_rec`, other languages with `PP-OCRv5_server_rec`. Korean, Chinese and
Japanese have no second reader for their letters: a line is accepted when several differently
prepared views agree, and a line with numbers only when a second installed model reads the same
digits on the line or on each word piece (the general model for Korean, the English model for
Chinese and Japanese); otherwise the line keeps its original pixels and is flagged for review.

Before OCR, scanned pages (no text layer, one picture over the page) that are tilted between 0.25°
and 5° are turned level, so the source is erased and the translation placed in level boxes; pages
with a text layer are never changed. Chinese and Japanese lines that do not end a sentence are
joined with the next line and translated as one paragraph. Arabic, Hindi and Bengali scans are not
read.

Text that BabelDOC's PDF reader gets wrong is read again with MuPDF before translation: letters
written only as ActualText (Word and LibreOffice do this for Bengali and Hindi clusters), which
arrive as `(cid:N)`, and right-to-left text stored in drawing order. The result is used only when
it agrees with what BabelDOC did read; each decision is recorded in the run's `unmapped-text.json`.

## Speed (L40S, 19 November 2026)

Six sample documents (8 pages) into Korean, first-time translations (no cache), warm server:
about 100 s in total, 12.6 s per page (15.3 s per page before the 19 November changes). Repeated
documents reuse cached translations: about 10 s per page. A one-page digital document takes about
5–7 s. The target of 3–5 s per page is not reached yet; the largest remaining costs are Docling
extraction on the CPU (its pinned PyTorch build has no CUDA variant), the longest paragraph's
translation, and BabelDOC's per-run start-up.

Longer documents into Korean on the same server: a 50-page printer manual in 313 s (6.3 s per
page); a 200-page document with 25 scanned pages in 52 minutes, while a second job shared the GPU.

Translated text is shrunk only as much as its own page needs: BabelDOC caps every paragraph at the
most common scale, and that cap is now taken per page instead of per document (the 50-page manual
kept full size on 41 pages, against 12 with one cap for the whole document).
