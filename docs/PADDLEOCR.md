# PaddleOCR Sample02 experiment

The original baseline is a one-page Sample02 experiment. The runner now accepts `--source` and `--output`; the adapter accepts `--source`, `--raw-dir` and `--output-dir` for another one-page PDF such as Sample01. The adapter still rejects multi-page inputs, rotated source pages and merged cells. This is not yet a Streamlit translation backend. Model inference has not been independently rerun during merge review; raw-output conversion and evaluation were reproduced.

## Sample01 next test

Run with the Python environment containing PaddleOCR, not the BabelDOC environment. The runner explicitly uses CPU; the Ollama GPU server is independent. Keep orientation correction and unwarping disabled initially so coordinates remain directly mappable. Inspect raw text and boxes before translation; a successful extraction is not an accuracy approval.

```powershell
python tools/ocr/run_paddleocr.py --source "C:\Users\Jubayer\Desktop\TransformX\document-extraction-lab-source\PDF_version\Sample01.pdf" --variant nowarp --output runs/paddleocr-sample01/raw
python tools/ocr/adapt_paddleocr.py --source "C:\Users\Jubayer\Desktop\TransformX\document-extraction-lab-source\PDF_version\Sample01.pdf" --raw-dir runs/paddleocr-sample01/raw --output-dir runs/paddleocr-sample01/normalized
```

Both new output directories must not already exist. Raw extraction records the source hash and package versions; the adapter verifies that hash when present. `normalized.json` retains the existing shared prediction format with text, figures, tables and displayed-page coordinates. Connecting these predictions to BabelDOC reconstruction remains a separate integration step. English language metadata describes this initial test scope; support for other scan languages is not validated here.

## Setup and reproduction

Use a separate Python environment compatible with PaddlePaddle. The contributor reported PaddleOCR 3.7.0 and PaddlePaddle 3.3.1; PaddleX OCR dependencies are also required:

```powershell
python -m pip install -r requirements.txt
python -m pip install paddleocr==3.7.0 paddlepaddle==3.3.1 "paddlex[ocr]"
```

PaddleX and downloaded model artifacts are not pinned, so this is not yet a fully locked environment. Models may download on the first extraction. Keep their versions and local weight hashes for future runs.

The committed raw output can be converted and evaluated without installing PaddleOCR. From the repository root:

```powershell
python tools/ocr/adapt_paddleocr.py
python main.py import-prediction experiments/paddleocr-raw-nowarp/normalized.json --source samples/Sample02.pdf --tool paddleocr-pp-structurev3-nowarp --tool-version 3.7.0 --settings experiments/paddleocr-raw-nowarp/settings.json --output runs/paddleocr-review/Sample02.json
python main.py evaluate --references references/Sample02.json --predictions runs/paddleocr-review --name paddleocr-review --output runs/paddleocr-review-report.json
```

For a new model run, preserve or move the existing raw-output folder first; the runner refuses to overwrite it:

```powershell
python tools/ocr/run_paddleocr.py --variant nowarp
```

`--variant default` reproduces the preprocessing-enabled configuration. The adapter defaults to the matching nowarp folder. To audit the historical default conversion, use `python tools/ocr/adapt_paddleocr.py paddleocr-raw --allow-unmapped-geometry`.

## Interpretation

The nowarp run's Sample02 scores are CER 0.32%, WER 4.62%, table detection F1 1.0, table cell F1 23/24, figure detection F1 1.0, and figure mean IoU 0.8721. Detection and table dimensions tie Docling; text errors, exact cells and figure overlap improve on this sample. One development document cannot establish a general winner. The preprocessing setting was chosen after inspecting this sample; test it on unseen documents.

The default run contains dewarped coordinates. Simple scaling cannot invert that transformation. Its saved box-based scores, including figure F1 0, are historical diagnostics, not a valid original-page detection comparison. A future adapter must inverse-map transformed boxes before scoring them. Both reports carry an explicit review limitation.

The HTML parser covers the simple unmerged table only. OCR engines differ between Docling and PaddleOCR, so this is a whole-pipeline comparison, not a controlled same-OCR comparison. Imported predictions do not supply the viewer manifests/assets needed for Streamlit's saved-run selector.

Reports were regenerated with evaluator 1.0.1 during integration. Raw tool output, OCR text and reference annotations were preserved. Selected experiment/report files remain intentionally force-added; large new runs remain ignored by default.
