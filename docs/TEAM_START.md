# Extraction comparison: team quick start

Current milestone: compare whole-document extraction on the same PDFs and reviewed references. Translation, redaction and reconstruction are later stages.

| Owner | Tool | Responsibility |
|---|---|---|
| Jubayer | Docling | Baseline, shared evaluation and integration |
| Nahid | BabelDOC | Investigate parsing/intermediate output before translation |
| Mashrur | Marker | Local whole-document extraction |
| Hyun | PaddleOCR PP-StructureV3 | Whole-document extraction |

Each tool extracts text, tables and figures together. Only Docling and native PyMuPDF are built-in backends. Teammates run the other tools separately and write adapters; the importer does not understand vendor JSON automatically.

## 1. Install and reproduce the saved baseline

Create your own Git branch and Python environment. From the repository folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py evaluate --references references/Sample02.json --predictions experiments/docling-fixed --name docling-baseline --output evaluation-results/local-docling-check.json
```

No Docling or OCR model installation is needed to score saved predictions. Expected CER: 0.006349206349206349; WER: 0.07514450867052024; table detection F1: 1; cell F1: 0.9166666666666666; dimensions recall: 1; figure F1: 1; mean figure IoU: 0.8453170903172769.

The saved complete report is evaluation-results/docling-fixed-text-tables.json; it includes figures too. Older reports are historical text-only runs and must be re-evaluated against the current reference before comparison. Evaluator 1.0.1 fixes cross-platform fingerprints: regenerate reports with the updated evaluator; extraction does not need to run again.

To inspect the baseline visually:

```powershell
.venv\Scripts\python -m streamlit run app.py
```

Select the run under experiments/docling-fixed in Previous runs, then Open saved run. Streamlit discovers completed runs under both runs/ and experiments/. An imported prediction without a manifest and preview assets is evaluated through the CLI; it is not a complete viewer run.

## 2. Run your assigned tool

Use a separate environment for the tool if its dependencies conflict. Process samples/Sample02.pdf unchanged. Keep the original output. Read docs/PREDICTION_FORMAT.md and map output to the shared pages/regions schema, including PDF-point coordinates and reading order. Store actual tool, model, OCR and preprocessing settings in a JSON file.

BabelDOC: establish whether parsing exposes text, structured table cells and figure boxes. Record unsupported outputs honestly. Do not translate the input for this extraction experiment.

Marker: begin with local processing without optional cloud LLM enhancement. For every tool, record the actual OCR engine/mode rather than assuming it matches Docling.

## 3. Import and evaluate

Replace example names, paths and ACTUAL_VERSION with your actual values:

```powershell
python main.py import-prediction my-tool-pages.json --source samples/Sample02.pdf --tool my-tool --tool-version ACTUAL_VERSION --settings my-tool-settings.json --output experiments/my-tool/document.json
python main.py evaluate --references references/Sample02.json --predictions experiments/my-tool --name my-tool --output evaluation-results/my-tool.json
python main.py compare evaluation-results/local-docling-check.json evaluation-results/my-tool.json --output evaluation-results/comparison.csv
```

Run these commands in the environment containing the lab dependencies. Reports require matching reference, protocol and evaluator fingerprints. Existing JSON and companion CSV reports are protected from accidental overwrite.

## 4. Submit a reproducible experiment

Use a pull request containing your adapter, dependency versions, setup instructions, raw output, normalized prediction, settings, reports and failure examples. Record hardware and timing conditions. New experiment/report folders are ignored by default; explicitly stage only the intended outputs:

```powershell
git add -f experiments/my-tool evaluation-results/my-tool.json evaluation-results/my-tool.csv
```

Keep model weights, credentials and virtual environments out of Git. Do not change shared references to improve a tool's score. Reference corrections require team review and re-evaluation of every tool.

## Evaluation agreement

- Sample02 has one 6-by-4 table and three figures: blue icon, warning symbol, ID photo. Table crops and page previews are not figures.
- Text reference excludes table-cell text. Preserve headers, footers and predicted reading order.
- Docling baseline OCR is automatic, not forced on every page. Different OCR engines are complete-system comparisons, not controlled OCR comparisons.
- Figure metrics measure detection/boxes, not image fidelity. Table metrics do not evaluate merged-cell topology.
- Expand to reviewed scans, multi-column pages and complex tables before selecting a winner. One PDF is a first check, not sufficient evidence of general performance.
