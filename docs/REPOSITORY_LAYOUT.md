# Repository layout

## Active source

```text
frontend/                    Customer Next.js/TypeScript application
cloud/
  src/                       Hosted Cloudflare API
  ui/                        Manual redaction editor
  test/                      Worker, API and asset tests
supabase/migrations/         Hosted PostgreSQL schema and access policies
backend/                     Separate local FastAPI service and engine adapter
backend/static/              Local FastAPI app's own pages (local Studio)
transformation/
  workflows.py               Translation / PII / combined entry points
  pipeline.py                Shared orchestration
  llm/                       Model transport (OpenAI-compatible: vLLM, Ollama, remote APIs), prompts,
                             literal and number guards, glossary
  ocr/                       Evidence handoff, caching and private OCR session client
  pdf/                       BabelDOC hooks, scan/figure workers, reconstruction, re-reading of
                             misread text, deskewing, annotations carry-over
  office/                    Prototype DOCX/PPTX routes
  validation/                PII detection, output checks and integrity
  analysis/                  Inspection and routing
  layout/                    Text fitting, line spacing, per-page scale and geometric guards
extraction/                  Shared IR and extraction adapters
evaluation/                  Evaluation contracts, earlier extraction metrics, PII test set (pii/)
                             and mass testing (robustness/: corpus generator, output checker, report)
tools/
  service/                   Hosted agent, web builders and packaging helpers
  ocr/                       OCR models, private serial service and diagnostics
  evaluation/                Saved-run evaluation and benchmark tools
  gpu/                       Server setup, start and restart scripts
  handoff/                   Earlier teammate packaging helpers (PDF reports need reportlab)
  legacy/                    Earlier standalone exports
  ui/                        UI-related helper tools
tests/                       Unit/regression tests and synthetic fixtures
ui/                          Streamlit interface and shared product copy rules
configs/                     Example configs (transformx.example.toml: the settings file);
                             *.local.json and transformx.toml stay private
config/                      Earlier extraction/evaluation defaults
docs/                        Setup, module maps, evaluation and design decisions
samples/, references/        Existing deliberate evaluation fixtures
.github/workflows/tests.yml  CI: engine tests, website type check and tests, site API tests
```

`transform_app.py` starts Streamlit. `app.py` and `main.py` serve the earlier
extraction lab. Windows launchers remain at the root so existing shortcuts work.
`backend_examples/` contains an earlier proposed adapter/contract; the active API
implementation is in `backend/` and `cloud/src/`.

## Local-only data

| Directory | Contents |
|---|---|
| `runs/` | Jobs, IR, extracted assets, manifests and logs |
| `output/`, `outputs/` | Reports, presentations, handoffs and investigation artifacts |
| `experiments/`, `evaluation-results/`, `review-output/` | Earlier extraction/evaluation evidence |
| `.venv*/`, model/cache folders | Machine-installed dependencies and models |
| `.local/` | Organization audit and other local working records |
| `.chart-data-*/`, `Microsoft/` | Local chart scratch data / copied runtime support files |
| `frontend/.next/`, `frontend/out/`, `cloud/public/`, `cloud/generated/` | Rebuilt web outputs |

These paths stay on disk and remain readable at their existing locations. The
organization branch removes formerly tracked generated evidence from the Git
index, not the filesystem. Prior commits retain that history. VS Code excludes
them from the default Explorer/search view; edit `.vscode/settings.json` to show
them. A fresh clone does not require those folders to contain historical runs.

No import roots, engine APIs, deployment routes, SQL schemas, runtime settings or
live GPU services are changed by the repository cleanup itself. Modules prepared
in previous work are included as the current source snapshot; Git presence is
not proof that each optional feature is enabled in production.
