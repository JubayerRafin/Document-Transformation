# Repository layout

## Active source

```text
frontend/                    Customer Next.js/TypeScript application
cloud/
  src/                       Hosted Cloudflare API
  ui/                        Studio source overrides and worker UI
  test/                      Worker, asset and Studio tests
supabase/migrations/         Hosted PostgreSQL schema and access policies
backend/                     Separate local FastAPI service and engine adapter
backend/static/              Source assets also used to build Studio
transformation/
  workflows.py               Translation / PII / combined entry points
  pipeline.py                Shared orchestration
  llm/                       Ollama transport, prompts and literal guards
  ocr/                       Evidence handoff, caching and private OCR session client
  pdf/                       BabelDOC, scan/figure workers and reconstruction
  office/                    Prototype DOCX/PPTX routes
  validation/                PII detection, output checks and integrity
  analysis/                  Inspection and routing
  layout/                    Text fitting and geometric guards
extraction/                  Shared IR and extraction adapters
evaluation/                  Evaluation contracts and earlier extraction metrics
tools/
  service/                   Hosted agent, web builders and packaging helpers
  ocr/                       OCR models, private serial service and diagnostics
  evaluation/                Saved-run evaluation and benchmark tools
  gpu/                       Server dependency setup
  handoff/                   Earlier teammate packaging helpers
  legacy/                    Earlier standalone exports
  ui/                        UI-related helper tools
tests/                       Unit/regression tests and synthetic fixtures
ui/                          Streamlit interface and shared product copy rules
configs/                     Example configs; *.local.json remains private
config/                      Earlier extraction/evaluation defaults
docs/                        Setup, module maps, evaluation and design decisions
.agents/skills/              Repository-specific task instructions
samples/, references/        Existing deliberate evaluation fixtures
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
