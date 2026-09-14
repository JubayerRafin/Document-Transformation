# Document Extraction Lab

A working extraction-first capstone foundation: run a whole-document backend, inspect text/tables/figures together, and export one intermediate representation (IR).

**Shared evaluation is now available.** Follow `docs/EVALUATION.md`: create human-reviewed references, run one baseline, let teammates import other tools' normalized output, and score every experiment with the same command. `docs/PREDICTION_FORMAT.md` defines the adapter contract. No new model installation is needed to evaluate existing predictions.

This is a standalone companion prototype for Deep Doc Extractor. It does not modify the upstream repository or implement translation, redaction, or document reconstruction yet. Those features follow after the extraction baseline is selected.

The working folder contains actual sample runs. The downloadable source ZIP excludes uploads and extracted document content. See `docs/VALIDATION.md` for measured execution results, `docs/ARCHITECTURE.md` for the Python flow, and `docs/TEAM_START.md` for the first team sprint.

## Start the application

On the current machine, Python and the application dependencies are already installed:

```powershell
python -m streamlit run app.py
```

Run this from this project folder. Open http://127.0.0.1:8501 in your browser. The app binds only to localhost. Choose a saved run or upload a PDF. Native extraction needs no model downloads.

On another machine, create an isolated environment first (Python 3.12 is a conservative starting point; this project was exercised on Python 3.14.3):

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

## What runs together

```text
PDF validation
  -> selected whole-document backend (PyMuPDF OR Docling)
  -> shared IR
  -> reconcile overlaps and validate geometry
  -> page previews + figure/table crops
  -> document.json + manifest.json
  -> inspect, save team findings, compare runs, download
```

`extraction/pipeline.py` owns this sequence. Each backend receives the same PDF and returns a `Document`. You do not independently run text, table, and image extraction as unrelated applications.

## Backends

| Backend | Included functionality | Important limits |
|---|---|---|
| `native` / PyMuPDF | Native text with word boxes, ruled tables, raster occurrences, source-page previews | No OCR; no semantic figure detector; vector graphics appear in previews but are not separately extracted; reading order is geometric |
| `docling` | Joint layout, text/OCR, table structure and picture extraction; native output retained | Requires local model weights; exact typography is not preserved; geometry mismatches fail visibly |

Native results identify full-page raster occurrences separately from figures. A scanned page producing zero text is flagged as needing OCR, not counted as successful text recognition. Embedded raster occurrences are not equivalent to complete diagrams or figures.

### Docling local setup

```powershell
python -m pip install -r requirements-docling.txt
docling-tools models download
```

Model setup requires internet and disk space; document processing does not enable remote model services. The app sets Hugging Face offline mode and uses existing caches or a supplied artifacts folder. RapidOCR requires the exact detection, recognition and classification ONNX model files pinned in `config/ocr-models.json`. Supply their folder in the sidebar or `--ocr-model-dir`. If omitted, the application checks the installed RapidOCR `models` folder. Every OCR-enabled run verifies SHA-256 checksums before initializing OCR; missing or different weights stop extraction. The manifest records the profile, model filenames and verified hashes, configuration hash, and OCR engine/runtime versions. Extra model files in the folder are ignored. Do not assume disabling Docling OCR removes the need for layout/table models.

For another OCR experiment, deliberately change and version `config/ocr-models.json` with the selected filenames, verified checksums and a distinct profile name. Teammates comparing the same configuration should use the same config file and model weights. These hashes identify the currently installed baseline files; they are not independent publisher authenticity verification. Historical runs retain their original metadata and are not retroactively checksum-verified. This change pins OCR weights only; layout/table weights and the full environment are not yet locked.

Supported OCR language coverage depends on the supplied recognition model. Evaluate your agreed language pair before claiming multilingual support.

The tested package versions are pinned for the main extraction tools. Code and model licenses differ across tools; review their actual terms before redistributing a deployment. PyMuPDF has AGPL/commercial licensing; this project does not relicense its dependencies.

## Command-line extraction and comparison

```powershell
python main.py extract samples\example.pdf --backend native
python main.py extract samples\example.pdf --backend docling
python main.py extract samples\example.pdf --backend docling --no-ocr
python main.py benchmark samples --backends native docling --output runs
python -m unittest discover -s tests -v
```

The benchmark writes `benchmark.csv` plus independent run folders. A failed backend does not stop other comparisons; the command exits nonzero if any run failed. Each row describes a separate run, with runtime including conversion, IR work, and preview/crop creation. Model initialization is included; this is not a controlled performance benchmark.

**Extraction counts are diagnostics, not accuracy scores.** Different tools use different region granularity. The separate `evaluate` command now computes text, table and figure metrics against reviewed references. Real sample annotation remains required; no scores against unreviewed sample answers are claimed.

## Outputs and privacy

```text
runs/<backend>-<source-hash>-<unique-run>/
  document.json          # Unified IR
  manifest.json          # Tool version, settings, time, counts, status
  native-docling.json    # Original Docling output when that backend is used
  reviews.json           # Team observations, when saved
  assets/
    page-1.png           # Original page preview
    p1_r3.png            # Table/figure/raster region crop
```

Uploads are stored locally under content hashes in `uploads/`. Extraction archives contain original document content; they are not redacted. Runs, uploads, and models are ignored by Git. Remove local runs/uploads when no longer needed. This local research app has no authentication or multi-user scheduling and should not be exposed as a public service.

Documents are untrusted input data. No document text is executed or interpreted as instructions, prompts, paths, or configuration.

## IR contract

- One-based page numbers; stable region IDs within a fixed backend/version/configuration.
- All region boxes use displayed-page coordinates in PDF points, with a top-left origin.
- `bbox = [left, top, right, bottom]`; page dimensions match the rendered preview.
- Kinds: `text`, `table`, `figure`, `raster_page`.
- Table cells are preserved separately from body text. Native table assignment happens at word level.
- Native text metadata keeps word geometry; Docling metadata keeps the original item, including cell spans and provenance.
- Text/figure overlap is retained as a relationship. Uncertain text/table overlap is flagged rather than deleted.
- The adapter is intentionally minimal. Detailed typography, character-level redaction geometry, caption inference and reconstruction need further engineering.

## Team ownership

| Owner | Starting files | First useful task |
|---|---|---|
| Text member | `backends/native.py`, `backends/docling_backend.py` | Compare text/OCR and reading order on the same saved runs |
| Table member | Same backend adapters; `models.py` | Label table cells/spans and check duplicates or missing content |
| You: figures + lead | Backend adapters, `reconcile.py`, `pipeline.py` | Compare complete figures, caption relationships, raster fragments and vector-only graphics |
| Fourth member | `app.py`, `main.py`, `tests/` | Maintain benchmark reproducibility, viewer and evaluation references |

Do not create three independent pipelines. Once a specific failure is measured, extract the relevant improvement into a focused module with shared IR inputs and outputs.

## Next engineering milestones

1. Annotate a shared evaluation set and choose the baseline using evidence.
2. Add targeted extraction improvements and test them against the unchanged baseline.
3. Add reviewed translation/redaction transformations over the IR.
4. Reconstruct PDFs and evaluate layout, translation quality and actual redaction removal.

Official references: [Docling](https://docling-project.github.io/docling/), [Docling offline options](https://docling-project.github.io/docling/usage/advanced_options/), [PyMuPDF coordinates and page operations](https://pymupdf.readthedocs.io/en/latest/page.html).
