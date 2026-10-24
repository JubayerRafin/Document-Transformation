# TransformX

Document translation, PII redaction and PDF reconstruction, with a customer web app and an internal Studio.

## Start here

- **Understand the code:** [repository layout](docs/REPOSITORY_LAYOUT.md).
- **Choose a setup or evaluation guide:** [documentation index](docs/README.md).
- **Run the customer app:** [frontend setup](docs/CUSTOMER_FRONTEND.md).
- **Connect a processing server:** [private GPU workers](docs/PRIVATE_GPU_WORKERS.md).
- **Contribute changes:** [contribution guide](CONTRIBUTING.md).

## What runs where

| Component | Location | Responsibility |
|---|---|---|
| Customer interface | `frontend/` | Next.js static export, React, TypeScript, Tailwind, Radix/shadcn components, Motion and GSAP |
| Hosted API and Studio | `cloud/` | Cloudflare Worker API, static assets and Studio UI overrides |
| Authentication and persistence | `supabase/` | PostgreSQL migrations, Auth roles and private Storage permissions |
| Processing agent | `tools/service/cloud_worker.py` | Claims jobs and runs the engine on the processing server |
| Document engine | `transformation/`, `extraction/` | Shared IR, translation, PII checks, OCR and reconstruction |
| Local API and prototype | `backend/`, `ui/` | Separate FastAPI service and Streamlit workflow |
| Evaluation | `evaluation/`, `tools/evaluation/`, `tests/` | References, metrics and regression checks |

The hosted site sends jobs to a connected processing worker. Ollama and PaddleOCR inference run on that worker's GPU; parsing, layout and file operations use its CPU. Cloudflare does not execute the Python PDF engine. Local FastAPI defaults to SQLite; the hosted application uses Supabase PostgreSQL. These are separate deployment routes.

The defaults are TranslateGemma 12B for translation and Qwen 3.5 9B for PII assistance. BabelDOC reconstructs PDFs. Models, credentials, private documents and results are not distributed with the source code. A generated output requires review; it is not automatic translation or privacy approval.

## Web development

Use the Node version supported by the pinned Next.js dependencies, and Python 3.11 for the existing engine environments.

```sh
npm --prefix frontend ci
npm --prefix cloud ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix cloud test
```

Cloud tests build the customer export and Studio before testing the Worker. They do not submit GPU jobs. For the integrated local web preview:

```sh
npm --prefix cloud run dev
```

`npm --prefix frontend run dev` starts the separate Next development server; authenticated processing still needs a configured API. `npm --prefix cloud run deploy` publishes to Cloudflare, so use it only for an intentional deployment.

## Python environments

Keep the existing dependency boundaries separate:

| Requirements | Environment |
|---|---|
| `requirements.txt` | Extraction lab / Streamlit prototype |
| `requirements-backend.txt` | Local FastAPI service |
| `requirements-transformation.txt` | Pinned BabelDOC worker |
| `requirements-deep-doc.txt` | Shared extraction integration |
| `requirements-docling.txt` | Earlier Docling lab |
| `requirements-evaluation.txt` | Translation and layout metrics |

GPU Paddle packages must match the server and driver. Follow the [server guide](docs/SERVER_EXECUTION.txt) rather than combining every requirements file into one environment. No GPU rental or model inference is required to inspect the code or run mocked unit tests.

For the local API after installing backend requirements:

```sh
python -m uvicorn backend.api:create_app --factory --host 127.0.0.1 --port 8000
```

On an already configured Windows checkout, `Start TransformX Web.cmd` starts the local web API, `Start TransformX.cmd` starts Streamlit, and `Start Lab.cmd` starts the earlier extraction lab. The [archived Streamlit guide](docs/archive/STREAMLIT_GUIDE.md) retains the earlier walkthrough.

## Source, configuration and local evidence

Runtime modules keep their current paths so imports, launchers, builds and saved manifests stay compatible. The editor groups the project by hiding generated directories; disable individual `files.exclude` entries in `.vscode/settings.json` to browse local evidence.

- Commit source, tests, migrations, lockfiles, documentation and example configuration.
- Keep credentials and machine paths in ignored `*.local.json` / `.env` files. See [configuration](configs/README.md).
- Keep `runs/`, `output/`, `outputs/`, `experiments/`, `evaluation-results/` and `review-output/` locally. No existing results are deleted by the Git cleanup.
- Earlier experiment files remain recoverable from prior Git commits. New private run evidence should not be added to source commits.
- Keep only deliberately reviewed fixtures under `samples/` and `references/`; do not place arbitrary uploads there.

For persistent GPU OCR, fresh-result benchmark settings and their limits, see [OCR session experiments](docs/OCR_SESSION_EXPERIMENT.txt). Local evidence identifies what was actually deployed; a source checkout alone does not prove live service configuration.
