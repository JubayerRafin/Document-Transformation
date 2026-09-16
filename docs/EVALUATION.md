# One evaluation process for the whole team

You run a baseline. Teammates run other tools on their own machines. Everyone scores their predictions against the same reviewed reference JSON files using this repository's evaluator and `config/evaluation.json`.

Tool adapters are separate from scoring. No Docling-specific metrics or automatic output repairs are applied by the evaluator.

## 1. Agree on the reference set together

Create an empty reference from a PDF:

```powershell
python main.py reference-init "samples/Sample02.pdf" --output references/Sample02.json
```

A draft for the supplied Sample02 is already in `references/Sample02.json`. It contains the correct PDF hash and page dimensions, but no answer annotations. Do not recreate it over your edits.

For each page, fill in:

- `text`: all legible visible text outside table cells, including headings, captions, headers/footers, and text inside figures, in the agreed reading order. Include repeated text at each occurrence. Do not include table cell text here.
- `tables`: each complete table's ID, box and rectangular `cells` grid. Cells are strings; preserve numeric values as strings. Use `null` only for structural placeholders, such as covered merged-cell positions, consistently across adapters.
- `figures`: each complete semantic figure's ID and box. A vector diagram or composite illustration can be one figure. Do not label the entire scanned page as a figure just because it is stored as a bitmap.
- `tasks`: the areas fully annotated on that page: `text`, `tables`, `figures`. Set `[]` to exclude a page without deleting it from the page inventory. An empty `figures` list with `figures` in tasks means the page has zero figures, not that annotation is unfinished.

Example table/figure annotation:

```json
{
  "tables": [
    {"id": "p1_table1", "bbox": [70, 245, 525, 345],
     "cells": [["Name", "Value"], ["Example", "10"]]}
  ],
  "figures": [
    {"id": "p1_figure1", "bbox": [70, 380, 220, 520]}
  ]
}
```

These numbers are illustrative; use actual page coordinates. All boxes are `[left, top, right, bottom]`, top-left origin, displayed-page PDF points. The existing viewer displays predicted boxes to help investigate, but those boxes must be checked against the source before becoming references.

When annotations for the selected tasks are complete, another teammate should check them. Set `reviewed` to `true` and fill `reviewed_by`. Drafts are rejected by the evaluator. Start with one page and one task if that makes the annotation process easier.

Share exactly the same reviewed files and protocol with all teammates. Any edit changes the benchmark fingerprint. Keep duplicate source formats together when separating development and held-out evaluation samples. Do not tune tools against the held-out answers.

## 2. Run your baseline once

Use a fresh output folder dedicated to one tool/version/settings combination:

```powershell
python main.py benchmark samples --backends docling --output experiments/docling-baseline
```

Do not reuse the folder for repeated trials: duplicate predictions for the same source PDF are rejected to prevent accidental selection of the best run. Choose a new experiment directory for a rerun. Store only the benchmark PDFs in `samples` and their references in `references`.

For a one-document start:

```powershell
python main.py extract "samples/Sample02.pdf" --backend docling --output experiments/docling-sample02
```

## 3. Teammates run other tools

Each teammate runs their chosen tool outside this application's extraction pipeline, preserves its raw output, and writes a small adapter to the page/region format in `docs/PREDICTION_FORMAT.md`.

After normalizing the pages:

```powershell
python main.py import-prediction teammate/normalized.json --source "samples/Sample02.pdf" --tool "their-tool" --tool-version "exact-version" --settings teammate/settings.json --output experiments/their-tool/Sample02.json
```

Repeat for each PDF. This command checks the coordinate convention and source dimensions, stamps the source hash, and retains the provided settings. It does not execute the tool, infer missing content, or convert an arbitrary vendor JSON automatically. If the tool already exports our complete IR, no import step is necessary.

Record OCR engine/version/weights, OCR mode, language, layout/table models, preprocessing and relevant tool options in `settings.json`. Missing content stays missing. Do not use references to repair predictions before reporting baseline scores.

## 4. Everyone uses the same evaluate command

```powershell
python main.py evaluate --references references --predictions experiments/docling-baseline --name docling-baseline --output evaluation-results/docling-baseline.json
```

Your teammate changes only the predictions, experiment name and output:

```powershell
python main.py evaluate --references references --predictions experiments/their-tool --name their-tool --output evaluation-results/their-tool.json
```

Single reference and prediction files are also supported. For an existing saved run, point `--predictions` at that run's `document.json`. A reference directory must contain only the intended benchmark references, including drafts that will correctly block evaluation until reviewed.

Every expected document is counted. If a tool failed and there is no prediction, the existing experiment folder can remain empty for that document: its text/regions are scored as missing. Missing pages are also scored, not skipped. Keep the tool's failure manifest or logs alongside the experiment for diagnosis. Predictions for unrelated source PDFs and mixed configurations are rejected.

## 5. Compare the reports

```powershell
python main.py compare evaluation-results/docling-baseline.json evaluation-results/their-tool.json --output evaluation-results/comparison.csv
```

Reports are comparable only if the reviewed references, protocol and evaluator implementation fingerprints match. Evaluator 1.0.1 normalizes Python source line endings before hashing, so equivalent Windows and Linux checkouts match. After upgrading from 1.0.0, regenerate reports from the saved predictions; do not rerun extraction or manually edit comparison keys. Share report JSONs; the comparison command produces a CSV without rerunning the tools. It does not rank by one invented overall score.

The app also has an Evaluation tab. Open a run, upload its reviewed reference JSON, inspect scores and reference-versus-prediction overlays, and download a single-document report. The CLI remains the canonical way to score the full shared dataset.

## What the metrics mean

| Metric | Better direction | Definition |
|---|---|---|
| CER | Lower | Total character edit distance divided by total reference characters across annotated pages |
| WER | Lower | Total whitespace-token edit distance divided by total reference tokens |
| Table detection F1 | Higher | One-to-one table box matching at configured IoU threshold |
| Table cell exact F1 | Higher | Exact normalized value at the same row/column slot within matched tables; missed/extra tables contribute missing/extra slots |
| Table dimensions recall | Higher | Matched tables with correct row and column counts divided by all reference tables |
| Figure detection F1 | Higher | One-to-one semantic figure box matching at configured IoU threshold |
| Figure matched mean IoU | Higher | Average crop overlap for matched figures; always read alongside detection recall |

Text normalization uses Unicode NFKC and collapses whitespace; case and punctuation remain significant. Concatenating text regions in predicted order removes line-versus-paragraph segmentation differences, but incorrect reading order and duplicate text still contribute errors. WER uses whitespace tokens and is not a linguistic word segmentation metric for languages without word spaces; prioritize CER for those documents.

IoU is intersection area divided by union area. Matching maximizes the number of valid one-to-one pairs. Higher IoU and stable IDs determine neighbor traversal, but total IoU is not globally optimized among tied maximum-cardinality assignments.

All aggregate metrics use pooled counts (micro aggregation), not an average of document percentages. CER/WER can exceed 1 due to insertions. If the reference denominator is zero but predictions contain text, the rate is `null` and the insertion/edit count remains visible. Empty detection sets produce `null` F1, not a fabricated perfect score. Unannotated tasks are not scored.

## Scope of version 1

This is a pilot evaluator, not a full document-understanding benchmark. It does not yet score merged-cell topology/TEDS, caption links, image pixel fidelity, exact fonts, or reading order as a separate metric. OCR/text recognition and reading sequence jointly affect page text error. Table cell scoring counts all slots, including empty cells, so examine the detailed failures rather than relying on one cell score.

Runtime from extraction is still a diagnostic; hardware and warm-up must be controlled for timing comparisons. Source hashes verify file identity, not annotation correctness. Review flags express your team's approval; they cannot verify human annotation quality automatically.

## Runnable synthetic walkthrough

The example files describe one invented page, not any supplied PDF or real model result:

```powershell
python main.py evaluate --references evaluation/examples/reference.json --predictions evaluation/examples/perfect.json --name demo-perfect --output evaluation-results/my-perfect.json
python main.py evaluate --references evaluation/examples/reference.json --predictions evaluation/examples/imperfect.json --name demo-imperfect --output evaluation-results/my-imperfect.json
python main.py compare evaluation-results/my-perfect.json evaluation-results/my-imperfect.json --output evaluation-results/my-comparison.csv
```

Expected: perfect CER=0 and detection/cell F1=1. Imperfect WER=0.5, cell F1=0.75 and figure F1=0. The wrong table cell reduces content quality even though the table boundary is correct.

Mathematical background: [JiWER edit-error metrics](https://github.com/jitsi/jiwer). This evaluator implements its own exact Levenshtein calculation and explicitly uses the zero-denominator policy above, which differs from JiWER's empty-reference rate convention.
