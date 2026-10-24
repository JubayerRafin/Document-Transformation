> Archived extraction/Streamlit guide. For the current software, start at the repository README and docs/README.md.

# TransformX

For the current translation runtime investigation, measurements and benchmark
commands, see **docs/RUNTIME_OPTIMIZATION.txt**. Earlier experiment folders and
teammate ZIPs are frozen snapshots; new engine changes use new experiment roots.

For the current PII teammate handoff, start with **docs/PII_HANDOFF.txt** and
**docs/CODE_MAP.txt**. The clean ZIP includes a PDF handbook, six source PDFs,
completed translation evidence and a relocation helper. The extraction-only lab
guides under TEAM_START.md and ARCHITECTURE.md describe an earlier milestone.

Local Streamlit prototype for extraction, translation, optional PII redaction,
reconstruction and review. PDF, DOCX and PPTX are supported prototype routes.
See [implementation status](../../docs/PROTOTYPE_IMPLEMENTATION_STATUS.md) for limits.

Choose **Translation only**, **PII only**, or **Translation + PII** in the app.
Translation defaults to `translategemma:12b`; PII detection defaults to
`qwen3.5:9b`. Each has its own endpoint. Combined mode redacts selected PII
before translation. `transformation/workflows.py` exposes independent
`run_translation` and `run_pii` entry points over the shared coordinator.

Shared extraction uses the prior Deep Doc Stage 1 adapters, native geometry
fallbacks, PaddleOCR for scans, raster table-grid evidence, and Deep Doc
render-and-mask figure candidates with OCR masks and spatial grouping. Figure
assets and coordinates are saved with `source-ir.json`; their boundaries need
review. BabelDOC performs PDF typesetting and reconstruction. Model findings,
translation progress, and validation are stored separately for evaluation.

Install the selected models on the corresponding Ollama server before running:

```powershell
ollama pull translategemma:12b
ollama pull qwen3.5:9b
```

TranslateGemma uses a translation-only prompt, not Qwen's JSON extraction
prompt. Office mixed styles use contextual run-wise translation and require
review. Regions beyond the adapter's input bound are rejected rather than
silently truncated; full dataset and translation-quality acceptance are pending.

## Start here

For the six-PDF GPU evaluation, follow
[GPU dataset setup and evaluation](../../docs/GPU_DATASET_EVALUATION.md).
For separate OCR, translation, layout and PII scoring, use the
[detailed evaluation pipeline](../../docs/DETAILED_EVALUATION.md).
The offline configuration registers the inputs without inference. Run translation
first, review its results, then run the independent PII phase. New RunPod SSH
connection details must be filled in before starting either phase.

Double-click **Start TransformX.cmd**, or run from this project directory:

```powershell
.\.venv-paddleocr\Scripts\python.exe -m streamlit run transform_app.py --server.address 127.0.0.1 --server.port 8504
```

Open **http://127.0.0.1:8504/**. You do not need to run individual modules.
Stop the existing Streamlit process with Ctrl+C before restarting after this
folder reorganization.

Learn these three files first:

| File | Purpose |
|---|---|
| [transform_app.py](../../transform_app.py) | Starts the application |
| [ui/transformx.py](../../ui/transformx.py) | Upload, options, progress and review screen |
| [transformation/pipeline.py](../../transformation/pipeline.py) | Coordinates the complete workflow |

## Folder map

```text
transform_app.py              App entry point
Start TransformX.cmd         Windows launcher
ui/                          Streamlit interface
extraction/                  Shared models and Deep-Doc adapters
  backends/                  Native PDF and Docling extraction
  deep_doc_stage1/            Original prior Stage 1 source; keep unchanged
transformation/
  pipeline.py                Main workflow
  progress.py                Run progress reader
  analysis/                  Inspection and routing
  pdf/                       BabelDOC workers and scan reconstruction
  office/                    DOCX/PPTX packages, rendering and exports
  llm/                       Ollama, prompts and protected values
  ocr/                       Local/GPU PaddleOCR orchestration
  layout/                    Text fitting and layout guards
  validation/                PII detection and output checks
tools/
  ocr/                       Standalone OCR workers and diagnostics
  evaluation/                Translation/model evaluation commands
  legacy/                    Earlier exports, outside the active app
tests/                       Automated checks
docs/                        Reports, decisions and setup guides
config/                      Evaluation and extraction configuration
samples/                     Input documents
references/                  Reviewed evaluation references
runs/                        Outputs, IR, logs and diagnostics
experiments/                 Earlier extraction experiment evidence
evaluation-results/          Saved evaluation results
```

The extraction lab remains available through **Start Lab.cmd** / `app.py`.
`main.py` is its extraction/evaluation CLI; `babeldoc_adapter.py` adapts saved
BabelDOC extraction evidence. These are not additional TransformX applications.
The previous README is preserved in
[Extraction lab guide](../../docs/EXTRACTION_LAB_GUIDE.md).

## Environments

Install extraction dependencies in the same environment that starts Streamlit:

```powershell
.\.venv-paddleocr\Scripts\python.exe -m pip install -r requirements-deep-doc.txt
```

`auto` tries Docling first when native geometry or visual analysis indicates a
table, then Camelot/pdfplumber as needed. `docling` requests structural recognition
on all upright pages with native text and fails visibly if that engine cannot run.
Text and render-and-mask figures do not require Camelot to import. Each coordinated
run saves actual calls, accepted regions and fallback reasons in
`deep-doc/extraction-evidence.json` and `source-ir.json` settings. Scans still need
the separate PaddleOCR route; a Deep Doc extraction rerun does not rerun translation.

To verify the configured dataset without model inference or GPU charges:

```powershell
.\.venv-paddleocr\Scripts\python.exe -m tools.evaluation.extraction_probe --config configs/evaluation.local.json --strategy docling
```

This writes a new extraction report, source IR, previews and figure assets under
`runs/extraction-integration/`. It preserves the saved translation baseline.

- `requirements.txt`: Streamlit, PDF and Office libraries.
- `requirements-transformation.txt`: pinned BabelDOC environment.
- `requirements-deep-doc.txt`: Deep-Doc dependencies in the Streamlit environment.
- `requirements-docling.txt`: extraction-lab Docling setup.
- `.venv-paddleocr/`: isolated local OCR environment, when installed.

Moving code does not change models, SSH credentials, virtual environments or
saved runs. Historical manifests retain their original paths as run evidence.
VS Code hides Python caches and virtual environments to keep the Explorer tidy.

Standalone commands now use paths such as:

```powershell
python tools/ocr/run_paddleocr.py --help
python tools/ocr/adapt_paddleocr.py --help
python -m transformation.validation.integrity --help
python -m unittest discover -s tests -p "test_*.py"
```

Office rendering needs Microsoft Office on Windows or LibreOffice. GPU checks
need a configured server and SSH tunnel. Generated documents require review;
automated checks do not guarantee translation accuracy or complete PII detection.
Run folders contain original content and PII, so they are not sanitized outputs.


### Full server execution

Streamlit can now run the entire document pipeline on an existing SSH GPU server. Select **Run entire pipeline on GPU server**; the laptop handles uploads, progress and downloads. See [server setup and execution](../../docs/SERVER_EXECUTION.txt). The new six-document configuration is `configs/evaluation-server.local.json`; previous experiment outputs are preserved.
