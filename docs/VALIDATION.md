# Validation of the initial prototype

## Shared evaluator update

The tool-independent evaluator adds reviewed reference drafts, imports of normalized predictions from other tools, text CER/WER, table detection/grid-cell/dimension metrics, figure detection/IoU, per-page failure details, and reference/protocol/code fingerprints for report comparison.

All 29 automated tests pass. Existing extraction IRs passed the evaluation contract checks. The command-line synthetic walkthrough produced the expected perfect and deliberately imperfect scores, the normalized-output importer was exercised against a real source PDF, and a draft reference was correctly rejected without creating a report. Streamlit's application test loaded the new Evaluation tab with no application exceptions; its temporary-folder cleanup emitted a Windows permission warning on process exit.

The supplied Sample02 reference remains a draft. No real-document ground-truth accuracy score is claimed. See `docs/EVALUATION.md` for the team workflow and limitations.

Tested locally on 13 September 2026 using Python 3.14.3, PyMuPDF 1.27.2.2, Docling 2.107.0, docling-core 2.85.0, Streamlit 1.57.0 and RapidOCR 3.9.0. Docling ran on CPU with four configured threads and existing local model files.

## Sample execution

All six supplied sample PDFs (eight pages total) completed with both backends: 12 final comparison runs. Docling OCR was enabled; native OCR was disabled. The source files were read from the supplied ZIP. Instructions inside them were treated as content.

| Sample | Backend | Text regions | Tables | Figures | Full-page rasters | Seconds |
|---|---|---:|---:|---:|---:|---:|
| Sample01 | Native | 0 | 0 | 0 | 1 | 0.797 |
| Sample01 | Docling | 35 | 1 | 6 | 0 | 31.841 |
| Sample02 | Native | 19 | 1 | 3 | 0 | 0.255 |
| Sample02 | Docling | 11 | 1 | 3 | 0 | 4.328 |
| Sample03 | Native | 25 | 2 | 4 | 0 | 0.533 |
| Sample03 | Docling | 14 | 2 | 4 | 0 | 6.630 |
| Sample04 | Native | 21 | 1 | 6 | 0 | 0.630 |
| Sample04 | Docling | 29 | 1 | 1 | 0 | 8.676 |
| Sample05 | Native | 21 | 0 | 0 | 0 | 0.150 |
| Sample05 | Docling | 9 | 0 | 3 | 0 | 2.608 |
| Sample06 | Native | 33 | 0 | 0 | 0 | 0.128 |
| Sample06 | Docling | 13 | 0 | 1 | 0 | 2.640 |

These are execution diagnostics, not accuracy scores. Region granularity differs. Native figure counts describe raster occurrences; Docling figure counts describe its picture regions. Runs include preview/crop generation and model initialization; first-run initialization makes direct speed rankings inappropriate.

Sample01 has no native text layer. The native backend reports its raster page and an OCR warning. Docling produced OCR-derived text. Text recognition accuracy still requires human reference annotations.

## Automated checks

Eight tests passed:

- Table words are not duplicated in body text; nearby text is retained.
- Rotated native-PDF boxes match displayed-page coordinates.
- Scanned pages do not pretend to contain native extracted text.
- Invalid PDF input is rejected.
- Backend failures produce failed manifests, not completed exports.
- Region IDs are stable for repeated native runs and export archives contain their assets.
- Uncertain text/table overlaps remain available for review.
- Invalid IR geometry is rejected.

The initial test fixture implementation encountered Windows temporary-folder permissions. Fixtures were changed to use ordinary inherited directory permissions in a controlled test directory; the final tests passed.

## Interface checks

- Streamlit's application test loaded the empty screen and a saved result without application exceptions.
- A browser upload of Sample02 completed through the actual Docling UI flow.
- The inspector displayed the original page with region overlays.
- Selecting the table displayed its bounds, extracted cells and source crop.
- JSON/archive export construction is covered by the pipeline tests; browser download-button visibility was checked separately.

## Known limits

### OCR reproducibility update

The OCR model set is now pinned in `config/ocr-models.json` and SHA-256-verified before use. All 11 tests pass, including three added checks for missing weights, modified weights, and ignoring extra files. The installed model files match the lock. Earlier sample runs above predate this enforcement and retain their original manifests.

### Remaining scope

- This release covers extraction and review. Translation, redaction and reconstruction are not implemented.
- Only PDF upload is supported. Office samples can be evaluated through their provided PDF counterparts.
- Caption matching, exact typography and complete semantic-figure recovery need further evaluation.
- OCR-enabled does not mean every word inside every image was correctly recognized.
- Native reading order is geometric and can fail on complex layouts.
- The Docling adapter includes nested picture text, retains source-item provenance and fails if its page dimensions disagree with the rendered PDF.
- No labeled ground-truth accuracy evaluation has been completed. No baseline winner is claimed.
