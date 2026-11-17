# TransformX

TransformX translates PDF documents and redacts personal information (PII) while keeping the
original page layout. Users upload a PDF on the website; a private GPU server processes it and
returns a translated or redacted PDF for review.

Three job types are supported:

| Job | What it does |
|---|---|
| **Translation** | Translates the document and rebuilds each page with the original layout, tables, colours and figures. |
| **PII redaction** | Finds names, emails, phone numbers, addresses, identifiers and bank details, masks them, and checks the result for anything still readable. |
| **Translation + PII** | Both in one job: PII is masked first, then the document is translated and checked again. |

There is also **manual redaction**: users draw boxes or pick values on the page, and the server
removes them.

### Languages

English, Korean, Japanese, Chinese (Simplified), Arabic, Hindi, Bengali, French, German, Spanish
and Portuguese. Arabic is written right to left with proper letter shaping; Hindi and Bengali use
shaped Indic text. Translation from English into each of the other ten has been tested on sample documents. OCR of scanned
pages currently reads English, Chinese and Japanese only.

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

## Repository layout

| Path | Contents |
|---|---|
| `frontend/` | Customer web app (Next.js static export, React, TypeScript, Tailwind) |
| `cloud/` | Cloudflare Worker API and static site |
| `supabase/` | Database migrations, roles and storage rules |
| `tools/service/cloud_worker.py` | GPU server agent: claims jobs and runs the engine |
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

Then register the server on the website (**GPU servers → Register**), copy the downloaded
`gpu.worker.local.json` to `configs/` on the server, and start it:

```bash
bash tools/gpu/start_worker.sh
```

The script starts both model servers, sizes their GPU memory for the card, and starts the worker.
It is safe to run again; anything already running is left alone. The worker log is
`/workspace/cloud-worker.log`. More detail: [private GPU workers](docs/PRIVATE_GPU_WORKERS.md).

### Measured speed

Six sample documents (8 pages: native, scanned, tables and figures), warm server, November 2026.
Times include extraction, OCR, translation, reconstruction and checks.

| Job | RTX 4090 (24 GB) | L40S (48 GB) |
|---|---|---|
| Translation, per document | 7–28 s | 7–26 s |
| Translation, average per page | — | 10–14 s depending on language |
| Translation + PII, all six documents | 309 s | 158 s |
| PII only, 52-document test set | 2.6 s/page | — |

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

The tests use synthetic documents and fake model servers, so they run without a GPU. A few need
the BabelDOC or PaddleOCR environment and are skipped or fail elsewhere.

## Privacy

- Worker credentials, `*.local.json` and `.env` files are ignored by Git; never commit them.
- Job outputs (`runs/`, `output/`, `outputs/`) stay on the machine that produced them.
- PII masking removes the underlying text and re-checks the output, but detection can miss
  values. Treat redacted output as needing human review before it is shared.

## License

MIT. See [LICENSE](LICENSE).
