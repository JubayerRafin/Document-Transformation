# Docling batch results — 2026-09-27

Completed all six provided PDFs (8 pages) and the public YOLOv3 paper (6 pages): 7 documents, 14 pages.

## Configuration

Docling 2.107.0, adapter 1.1.0, RapidOCR 3.9.0 with pinned model hashes; CPU, 4 threads. Automatic OCR enabled, not forced full-page OCR. Environment and source hashes are saved beside the runs and under config/.

## Extraction diagnostics

Counts below are predictions, not accuracy scores. Time includes startup and preview generation, so it is not a controlled speed comparison.

| File | Pages | Text regions | Tables | Figures | Seconds |
|---|---:|---:|---:|---:|---:|
| Sample01.pdf | 1 | 35 | 1 | 6 | 226.72 |
| Sample02.pdf | 1 | 13 | 1 | 3 | 11.61 |
| Sample03.pdf | 2 | 18 | 2 | 4 | 23.06 |
| Sample04.pdf | 2 | 29 | 1 | 1 | 24.13 |
| Sample05.pdf | 1 | 9 | 0 | 3 | 6.90 |
| Sample06.pdf | 1 | 13 | 0 | 1 | 8.24 |
| YOLOv3.pdf | 6 | 315 | 3 | 5 | 145.19 |

## Accuracy evaluation

Only Sample02 has an approved reference in the current lab. Its rerun reproduced CER 0.635%, WER 7.514%, table detection F1 1.0, table cell exact F1 0.9167, table dimensions recall 1.0, figure detection F1 1.0, and matched figure mean IoU 0.8453. Shared report: benchmark-records/docling-20260927/Sample02-evaluation.json.

Samples01 and03–06 and YOLOv3 require independently reviewed references before scoring. Sample01 has six text/table overlap warnings requiring inspection. The provisional YOLOv3 reference was not used for scoring.

## Output locations

- Provided PDFs: experiments/docling-provided-20260927-retry/
- Extra PDF: experiments/docling-extra-20260927/
- Each completed run contains document.json, native-docling.json, manifest.json, page previews and region crops.
- experiments/docling-provided-20260927/ is an interrupted initial startup attempt, not part of the completed batch.

The official TrafficSF download returned HTTP 403, so that file was not included. Obtain the exact source file from the teammate before adding it.

## Next step

Review extracted regions in Streamlit, annotate the remaining references from the source PDFs, then evaluate each tool against the same frozen references. Compact records are shared under benchmark-records/docling-20260927; full run folders remain local.
