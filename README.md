# TransformX

TransformX translates PDF, Word (.docx, .doc) and PowerPoint (.pptx, .ppt) documents and redacts personal
information (PII) while keeping the original layout. Users upload a document on the website; a
private GPU server processes it and returns it translated or redacted, in the format it was
uploaded in, for review.

Word and PowerPoint files are changed in place (`transformation/office/native.py`): every paragraph
keeps its runs, so styles, numbering, tables, pictures, notes and slide layouts stay as they were.
Bold, italic and link text keep their formatting through translation; Arabic targets turn
paragraphs and tables right to left; PowerPoint boxes the translation outgrows get a smaller font,
measured against the source deck. For PII, tracked changes are accepted and author fields cleared
before masking, and speaker notes, comments, headers and footers are checked too. The website shows
a LibreOffice PDF preview of the source and the result; manual box redaction is for PDF results.
Word and PowerPoint 97-2003 files (.doc, .ppt) are converted by LibreOffice and come back as .docx
and .pptx.

Three job types are supported:

| Job | What it does |
|---|---|
| **Translation** | Translates the document and rebuilds each page with the original layout, tables, colours and figures. |
| **PII redaction** | Finds names, emails, phone numbers, addresses, identifiers and bank details, masks them, and checks the result for anything still readable. |
| **Translation + PII** | Both in one job: PII is masked first, then the document is translated and checked again. |

There is also **manual redaction**: users draw boxes or pick values on the page, and the server
removes them. If a redaction result removed something that should stay, its page lists every removed
value (category, pages, how often); ticking **Keep visible** makes a new version from the original
with those values left visible, and the earlier result stays in the history.

### Languages

English, Korean, Japanese, Chinese (Simplified), Arabic, Hindi, Bengali, French, German, Spanish
and Portuguese. Arabic is written right to left with proper letter shaping; Hindi and Bengali use
shaped Indic text. Mass tests on generated documents (reports, invoices, forms, contracts, slides,
timetables, manuals, scans) cover English into each of the other ten, and Korean, Japanese,
Chinese, Arabic, Hindi and Bengali into English, plus a real 50-page printer manual and a 200-page
document. OCR of scanned pages reads all eleven languages (Bengali and Hindi lines are confirmed
by a second recognition model).

Highlights, notes, form fields and links in a source PDF are kept in its translation. If the GPU
server offers more than one translation model, users choose one per job on the upload form.

### Translation models

| Model | Use | Measured (1,997 held-out paragraphs, 20 language pairs, 24 Nov 2026) |
|---|---|---|
| TranslateGemma 12B | Default | MetricX-24 2.09, COMET-22 0.920 |
| TranslateGemma 27B | Optional: **"TranslateGemma 27B · higher quality, slower"** | MetricX-24 1.96, COMET-22 0.919; twice as slow |

27B is better mainly out of English (MetricX: Hindi 2.75 → 2.27, Spanish 2.00 → 1.57, French
2.26 → 1.85, Chinese 2.01 → 1.60); into English it is about the same. Lower MetricX is better
(0 = no errors), higher COMET is better. The comparison tool is `evaluation/suite/model_ab.py`.

27B does not fit on the GPU beside the PII model, so on a server that has it:

- it is loaded once at start and then sleeps (weights in host memory, a few seconds to wake);
- a job that chooses it gets the GPU alone: the PII model and 12B sleep while it works and wake
  again afterwards (`transformation/llm/gpu_share.py`, servers grouped in `TRANSFORMX_GPU_SWAP`);
- its jobs run one at a time, never beside another job, and never fall back to the PII model in the
  middle of translation;
- if it cannot start (too little free GPU memory), the default models wake and the site runs
  without the option.

Customers can set a **glossary** per language pair: required translations for their own terms,
applied to every new translation.

## How it works

```
Browser ──> Cloudflare Worker (cloud/) ──> Supabase (jobs, files, accounts)
                                                 ^
                                                 | claims jobs over HTTPS
                                       GPU server: tools/service/cloud_worker.py
                                         ├─ Deep Doc Extractor (Docling, pdfplumber, Camelot)
                                         ├─ PaddleOCR for scanned pages and figures
                                         ├─ vLLM: TranslateGemma 12B (translation)
                                         ├─ vLLM: Qwen 3.5 9B FP8 (PII detection)
                                         └─ BabelDOC (page reconstruction)
```

1. **Extraction.** The Deep Doc Extractor reads text, tables, figures and reading order into a
   shared intermediate representation (IR). Scanned pages and text inside images go through
   PaddleOCR.
2. **Translation.** BabelDOC lays out each page; paragraphs are translated in parallel by
   TranslateGemma. Numbers, IDs, emails and protected values are checked after translation, and a
   paragraph that loses one is retried or kept in the source language and flagged.
3. **PII.** Rules and the Qwen model find candidates. The matching text is deleted from the page,
   not just covered with a box, and a final pass re-reads the output, including OCR of images,
   before the job is allowed to finish.
4. **Review.** Every result carries warnings and a review list. Generated output is a draft for a
   person to check, not an approved translation or a guarantee that all PII was found.

The website never runs the Python engine. All document processing happens on the GPU server the
user registers, and documents stay in that user's private storage.

The engine itself has no database, accounts or hosting dependency. For an internal deployment it
runs behind its own small HTTP API (`tools/service/engine_server.py`), with model servers that can be
local or remote (OpenAI-compatible, with API keys). One settings file,
[`configs/transformx.toml`](configs/transformx.example.toml), controls model servers, OCR, output,
the engine service and the worker; `python -m transformation.settings check` validates it. See
[engine, adapters and configuration](docs/ENGINE_SERVICE.md) and [security notes and
limitations](docs/SECURITY_AND_LIMITATIONS.md).

## Repository layout

| Path | Contents |
|---|---|
| `frontend/` | Customer web app (Next.js static export, React, TypeScript, Tailwind) |
| `cloud/` | Cloudflare Worker API and static site |
| `supabase/` | Database migrations, roles and storage rules |
| `tools/service/cloud_worker.py` | GPU server agent: claims jobs and runs the engine |
| `tools/service/engine_server.py` | Standalone engine HTTP API (no database or accounts) |
| `tools/gpu/` | Server setup and start scripts |
| `extraction/` | Deep Doc Extractor integration and the shared IR |
| `transformation/` | Translation, PII, OCR, reconstruction and validation |
| `backend/` | Job entry point used by the worker, plus a local FastAPI service |
| `evaluation/`, `tests/` | Evaluation tools and the unit test suite |
| `docs/` | Setup guides and design notes ([index](docs/README.md)) |

## Setting up a GPU server

Any Linux server with an NVIDIA GPU and outbound HTTPS works (RunPod, a university allocation or
your own machine). No inbound port is needed.

| GPU memory | How the two models run |
|---|---|
| 40 GB or more (L40S, A6000, A100) | Both models stay loaded together. |
| 24 GB (RTX 4090) | The models take turns on the GPU; jobs that use both pay a ~10 s switch. |

About 80 GB of disk is needed for the models and environments.

```bash
git clone https://github.com/JubayerRafin/Document-Transformation.git /workspace/transformx-app
cd /workspace/transformx-app
bash tools/gpu/setup_transformx.sh pii      # PaddleOCR (GPU) and Ollama
bash tools/gpu/setup_full_pipeline.sh       # Deep Doc Extractor, BabelDOC, LibreOffice
bash tools/gpu/setup_vllm.sh                # vLLM and both models (checksums verified)
```

For the optional higher-quality translation model (TranslateGemma 27B, about 55 GB more disk and a
GPU of 40 GB or more), add it to the vLLM setup; it is checked against Google's published checksums
and offered on the website after the next start:

```bash
TRANSFORMX_HIGH_QUALITY_SETUP=1 bash tools/gpu/setup_vllm.sh
```

`TRANSFORMX_HIGH_QUALITY=off` starts the server without it even when it is installed.

Then build the website once and start everything:

```bash
npm --prefix frontend ci && npm --prefix frontend run build     # Node 20 or newer
bash tools/gpu/start_site.sh
```

The script starts the model servers (sized for the card), the website with its engine on port 8080,
and a watchdog that starts again whatever stops. It is safe to run again; anything already running
is left alone. Open the website through an SSH tunnel (`ssh -L 8080:localhost:8080 user@<server>`,
then http://localhost:8080), or set `TRANSFORMX_PASSWORD` and `TRANSFORMX_HOST` to open it on the
company network. `bash tools/gpu/stop_site.sh` stops it for maintenance; after an update,
`bash tools/gpu/start_site.sh --restart` loads the new code. Starting at boot, updating, logs,
backups and troubleshooting: **[operations guide](docs/OPERATIONS.md)**.

The older hosted mode (a website elsewhere and a GPU worker that registers with it,
`tools/gpu/start_worker.sh`) still works; see [private GPU workers](docs/PRIVATE_GPU_WORKERS.md).
`bash tools/gpu/start_worker.sh --servers-only` starts only the model servers, for the
[engine service](docs/ENGINE_SERVICE.md). All of these read `configs/transformx.toml` when it exists;
a model server set there to another machine is used instead of starting the local one.

### Measured speed

Six sample documents (8 pages: native, scanned, tables and figures), warm server, November 2026.
Times include extraction, OCR, translation, reconstruction and checks. On an L40S on 19 November,
first-time translations into Korean took 12.6 s per page (10 s for repeated documents, which reuse
cached translations); details in [engine, adapters and configuration](docs/ENGINE_SERVICE.md#speed-l40s-19-november-2026).

| Job | RTX 4090 (24 GB) | L40S (48 GB) |
|---|---|---|
| Translation, per document | 7–28 s | 7–26 s |
| Translation, average per page | — | 10–14 s depending on language |
| Translation + PII, all six documents | 309 s | 158 s |
| PII only, 52-document test set | 2.6 s/page | — |
| 50-page printer manual into Korean | — | 313 s (6.3 s/page) |
| 200-page document (25 scanned pages) into Korean, beside another job | — | 52 min |

The website accepts up to 200 pages and 50 MB. A job may run 60 minutes, plus 30 s for every page
beyond 100 (configurable).

## Website development

Node 20 or newer.

```sh
npm --prefix frontend ci
npm --prefix cloud ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix cloud test          # builds the site, then tests the Worker
npm --prefix cloud run dev       # local preview
```

`npm --prefix cloud run deploy` runs all checks and publishes the site to Cloudflare. First-time
Cloudflare and Supabase setup is in [cloud setup](docs/CLOUD_SETUP.md); apply the migrations in
`supabase/migrations/` in date order.

## Python environments

The engine uses separate environments because BabelDOC, PaddleOCR and Docling pin conflicting
packages. The setup scripts create them; to build them by hand:

| Requirements | Environment |
|---|---|
| `requirements-deep-doc.txt` | Engine and Deep Doc Extractor (Python 3.11) |
| `requirements-transformation.txt` | BabelDOC worker (Python 3.11) |
| `requirements-backend.txt` | Local FastAPI service |
| `requirements-evaluation.txt` | Translation and layout metrics |
| `requirements.txt`, `requirements-docling.txt` | Earlier extraction lab |

## Tests

```sh
python -m unittest discover -s tests -t .
```

The tests use synthetic documents and fake model servers, so they run without a GPU. Tests that
need the BabelDOC or PaddleOCR environment, a GPU, Microsoft Office or LibreOffice, or saved
experiment results (`experiments/`, not in Git) are skipped where those are missing.

GitHub Actions (`.github/workflows/tests.yml`) runs the engine tests, the website's type check and
tests, and the site API tests on every pull request and on `main`.

To test many documents on a GPU server (generated corpus, output checker, summary report), see
[testing many documents](docs/ENGINE_SERVICE.md#testing-many-documents).

## Privacy

- Worker credentials, `*.local.json` and `.env` files are ignored by Git; never commit them.
- Job outputs (`runs/`, `output/`, `outputs/`) stay on the machine that produced them.
- PII masking deletes the matched text and re-checks the output, but detection can miss values and
  unrecoverability has not been independently verified: it is reviewed masking, not certified
  secure redaction. Treat masked output as needing human review before it is shared.
- Model servers on other machines are used only when listed explicitly; keys come from the
  environment and are never written to job files. See [security notes](docs/SECURITY_AND_LIMITATIONS.md).

## License

MIT. See [LICENSE](LICENSE).
