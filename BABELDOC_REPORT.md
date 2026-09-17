# BabelDOC Extraction Evaluation Report

## 1. Purpose and scope

This report documents an extraction-only evaluation of **BabelDOC 0.6.4** for the Document Transformation capstone project.

The current project milestone is to compare whole-document extraction backends on **text, tables, and figures together** before choosing a baseline for later translation/redaction/reconstruction work.

BabelDOC was evaluated as an external tool and normalized into the project's shared intermediate representation (IR) through a custom adapter. Translation was intentionally disabled for this experiment.

Five PDFs were included:

- `Sample02.pdf`
- `Sample03.pdf`
- `Sample04.pdf`
- `YOLOv3.pdf`
- `TrafficSF.pdf`

The same adapter logic was used across the evaluated set.

---

## 2. BabelDOC environment and run mode

- **Tool:** BabelDOC
- **Version:** 0.6.4
- **Environment:** separate `.venv-babeldoc`
- **Translation:** skipped
- **Automatic glossary extraction:** disabled
- **Intermediate output inspected:** `paragraph_finder.json`
- **Normalized output:** shared project JSON IR
- **Evaluation environment:** project `.venv`
- **Source-page coordinate convention required by project:** displayed-page PDF points, top-left origin
- **BabelDOC geometry observed:** bottom-left-origin coordinates
- **Adapter action:** convert BabelDOC coordinates to the project top-left convention
- **Credentials:** no real credentials committed
- **Translation usage:** the Sample02 no-translation run reported zero translation tokens

Representative BabelDOC command:

```powershell
.\.venv-babeldoc\Scripts\babeldoc.exe `
  --files samples\Sample02.pdf `
  --skip-translation `
  --no-auto-extract-glossary `
  --openai `
  --openai-api-key dummy `
  --debug `
  --working-dir experiments\babeldoc-working-Sample02 `
  --output experiments\babeldoc-output-Sample02
```

The placeholder value above was used only because the CLI required a provider argument even with translation skipped. A real API key was not required for the extraction run and should never be committed.

---

## 3. Extraction pipeline used in this experiment

```text
PDF
  -> BabelDOC
  -> paragraph_finder.json
  -> babeldoc_adapter.py
  -> normalized.json
  -> main.py import-prediction
  -> experiments/babeldoc/<document>.json
  -> main.py evaluate
  -> evaluation-results/*.json + *.csv
```

The custom adapter performs the following:

1. Reads BabelDOC's `paragraph_finder.json`.
2. Converts geometry from BabelDOC's bottom-left coordinate convention to the project's top-left PDF-point convention.
3. Keeps body text outside table regions.
4. Maps detected image/layout regions to semantic figure predictions where appropriate.
5. Reconstructs rectangular table grids from positioned text found inside detected table boxes.
6. Leaves missing or unsupported content missing rather than repairing predictions from reference annotations.
7. Produces the normalized envelope required by the project importer.

---

## 4. What BabelDOC exposed successfully

### Text and layout

BabelDOC exposed positioned text, paragraph/layout information, page geometry, and reading-order-related intermediate information.

On simpler synthetic documents, this produced very accurate text extraction. On dense multi-column public documents, text accuracy degraded substantially.

### Table regions

BabelDOC consistently exposed table-region detections in the evaluated set. The pooled table-detection F1 was `1.0`.

### Image / figure regions

BabelDOC exposed image/layout boxes that could be mapped to semantic figure predictions on the simpler project samples. When a predicted figure matched a reference figure, its box geometry was extremely accurate.

### Intermediate representation

BabelDOC exposed rich intermediate JSON that was useful for investigation and adapter development rather than forcing the experiment to depend only on the final translated/rendered document.

---

## 5. Main BabelDOC limitation found: structured table cells

The most important limitation found in this investigation is that the inspected BabelDOC table layout object did **not** expose a ready-to-use rectangular table cell grid compatible with the project's shared IR.

For this reason, `babeldoc_adapter.py` reconstructs table cells geometrically from positioned text inside detected table regions.

This distinction is essential:

```text
Table-region detection                    -> BabelDOC output
Rectangular rows/cells used by evaluator  -> adapter reconstruction
```

Therefore, table cell and table-dimension metrics in this report measure:

**BabelDOC + the documented geometric reconstruction adapter**

They must **not** be described as native BabelDOC structured-cell accuracy.

This reconstruction worked well on simpler tables but degraded sharply on merged, irregular, scientific, and government-document tables.

A concrete failure occurred in `TrafficSF`: one detected table region produced a `0x0` reconstructed grid because the positioned text could not be converted into a valid rectangular structure. The failure was preserved instead of manually repaired.

---

## 6. Per-document extraction diagnostics

These counts describe adapter output structure and are **not accuracy scores**.

| Document | Pages | Text regions | Tables | Figures | Notes |
|---|---:|---:|---:|---:|---|
| Sample02 | 1 | 13 | 1 | 3 | Simple onboarding document |
| Sample03 | 2 | 19 | 2 | 4 | Added performance-review table/signature |
| Sample04 | 2 | 15 | 1 | 6 | Images with baked-in text and images inside table cells |
| YOLOv3 | 6 | not recorded in command log | extracted/imported | extracted/imported | Dense scientific multi-column paper |
| TrafficSF | 5 | 111 | 6 | 0 | One reconstructed table became `0x0` |

Structural counts are diagnostic only. Region granularity can differ between tools and does not by itself indicate accuracy.

---

## 7. Individual evaluation results

### Sample02

- CER: `0.0`
- WER: `0.0`
- Character errors: `0`
- Table detection F1: `1.0`
- Table cell exact F1: `0.9583333333333334`
- Table dimensions recall: `1.0`
- Figure detection F1: `1.0`
- Figure matched mean IoU: `0.9999992900417822`

### Sample03

- CER: `0.007513661202185792`
- WER: `0.06060606060606061`
- Character errors: `11`
- Table detection F1: `1.0`
- Table cell exact F1: `0.44642857142857145`
- Table dimensions recall: `0.5`
- Figure detection F1: `1.0`
- Figure matched mean IoU: `0.9999994063790392`

### Sample04

- CER: `0.2348158775705404`
- WER: `0.24671052631578946`
- Character errors: `491`
- Table detection F1: `1.0`
- Table cell exact F1: `1.0`
- Table dimensions recall: `1.0`
- Figure detection F1: `1.0`
- Figure matched mean IoU: `1.0`

### YOLOv3

- CER: `0.595729080744203`
- WER: `0.6957323498419389`
- Character errors: `13000`
- Table detection F1: `1.0`
- Table cell exact F1: `0.0213903743315508`
- Table dimensions recall: `0.0`
- Figure detection F1: `0.0`
- Figure matched mean IoU: `null`

### TrafficSF

- CER: `0.5272362816336758`
- WER: `0.5915538362346873`
- Character errors: `10521`
- Table detection F1: `1.0`
- Table cell exact F1: `0.07194244604316546`
- Table dimensions recall: `0.16666666666666666`
- Figure detection F1: `0.0`
- Figure matched mean IoU: `null`

---

## 8. Five-document pooled evaluation

The evaluator pools underlying counts across documents rather than averaging the five percentages.

- Experiment: `babeldoc-five-documents`
- Documents: `5`
- Missing documents: `0`
- Missing pages: `0`
- CER: `0.5156035370879121`
- WER: `0.6025353228575201`
- Character errors: `24023`
- Table detection F1: `1.0`
- Table cell exact F1: `0.19248826291079812`
- Table dimensions recall: `0.3076923076923077`
- Figure detection F1: `0.7222222222222222`
- Figure matched mean IoU: `0.9999996535976633`
- Comparison key: `1861dad6130294cf047dcbe8385e97fc92cb5af749bb66dfe966ab70c2cf1293`

Summary table:

| Document | CER ↓ | WER ↓ | Table detection F1 ↑ | Cell exact F1 ↑ | Dimensions recall ↑ | Figure F1 ↑ | Figure IoU ↑ |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sample02 | 0.0000 | 0.0000 | 1.0000 | 0.9583 | 1.0000 | 1.0000 | ~1.0000 |
| Sample03 | 0.0075 | 0.0606 | 1.0000 | 0.4464 | 0.5000 | 1.0000 | ~1.0000 |
| Sample04 | 0.2348 | 0.2467 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| YOLOv3 | 0.5957 | 0.6957 | 1.0000 | 0.0214 | 0.0000 | 0.0000 | null |
| TrafficSF | 0.5272 | 0.5916 | 1.0000 | 0.0719 | 0.1667 | 0.0000 | null |
| **All five pooled** | **0.5156** | **0.6025** | **1.0000** | **0.1925** | **0.3077** | **0.7222** | **~1.0000*** |

`*` Figure mean IoU is calculated only over matched figures. It must always be interpreted together with figure detection F1. A near-perfect matched IoU does not mean missed figures were recovered.

---

## 9. Per-document observations

### Sample02

Sample02 was the easiest document for BabelDOC + adapter.

Strengths:

- exact normalized text
- correct table-region detection
- almost-complete reconstructed cell grid
- correct table dimensions
- all three figures detected
- nearly exact figure geometry

This sample demonstrates that the approach can work very well on a clean, simple, mostly single-column synthetic document.

### Sample03

Sample03 remained strong for text and figure extraction, but the additional performance-review table exposed the limitations of geometric table reconstruction.

Strengths:

- very low CER/WER
- both table regions detected
- all figures detected

Weaknesses:

- cell exact F1 dropped to about `0.446`
- table dimensions recall dropped to `0.5`

This shows that table-region detection and table-structure recovery are separate problems.

### Sample04

Sample04 intentionally contains text baked into images and images embedded inside table cells.

Strengths:

- table region detected correctly
- reconstructed table matched the current reference
- all six semantic image regions detected
- figure geometry matched accurately

Weakness:

- text CER/WER were noticeably worse than on Sample02/Sample03

The document is useful because it demonstrates the difference between selectable PDF text and text that exists only inside pixels.

### YOLOv3

The YOLOv3 paper was much more difficult.

Characteristics:

- six pages
- dense two-column academic layout
- equations
- charts
- complex tables
- captions
- reading-order complexity

Observed failures:

- high CER (`~0.596`)
- high WER (`~0.696`)
- table regions detected but reconstructed cell structure failed badly
- table dimensions recall `0`
- figure detection F1 `0`

This is the clearest evidence that the current BabelDOC adapter does not generalize well to dense scientific papers.

### TrafficSF

TrafficSF is a five-page NHTSA research note with multi-column text, multiple complex tables, and a chart.

Observed failures:

- high CER (`~0.527`)
- high WER (`~0.592`)
- table regions detected, but cell reconstruction was weak
- dimensions recall only `~0.167`
- one table reconstruction resulted in `0x0`
- figure detection F1 `0`

Again, region detection was much stronger than semantic/structured recovery.

---

## 10. What BabelDOC did well

Across this experiment, BabelDOC was useful for:

- exposing document text and paragraph/layout information
- exposing page geometry
- exposing table-region candidates
- exposing image/layout regions
- providing rich intermediate JSON suitable for adapter development
- preserving enough geometry to convert into the project's coordinate system
- performing very well on simpler synthetic PDFs
- producing very accurate figure boxes when a semantic figure was successfully matched

---

## 11. What BabelDOC did not provide directly in this experiment

The inspected BabelDOC intermediates did not provide:

- a complete project-ready rectangular table cell grid
- reliable complex-table topology
- guaranteed semantic figure recovery on dense public documents
- project-native JSON output
- the exact project's top-left coordinate convention without conversion
- a project-ready evaluation report without a custom adapter/import step

Also, translation/reconstruction capabilities were deliberately outside the scope of this extraction benchmark and were not evaluated.

---

## 12. Adapter-added behavior

The following behavior came from `babeldoc_adapter.py`, not directly from native BabelDOC output:

- bottom-left -> top-left coordinate conversion
- exclusion of table-cell text from body-text output
- geometric table-grid reconstruction
- semantic mapping of image/layout forms to project figure regions
- generation of project region IDs
- normalization into the shared IR

These adapter decisions are part of the experiment configuration and must be reported because they affect measured accuracy.

---

## 13. Comparison with Docling

A direct numeric comparison is valid only where both tools were evaluated against the same reviewed reference/protocol.

For `Sample02`, the reproduced Docling baseline was:

| Metric | BabelDOC + adapter | Docling baseline |
|---|---:|---:|
| CER ↓ | 0.0000 | 0.006349 |
| WER ↓ | 0.0000 | 0.075145 |
| Table detection F1 ↑ | 1.0000 | 1.0000 |
| Table cell exact F1 ↑ | 0.9583 | 0.9167 |
| Table dimensions recall ↑ | 1.0000 | 1.0000 |
| Figure detection F1 ↑ | 1.0000 | 1.0000 |
| Figure matched mean IoU ↑ | ~1.0000 | 0.8453 |

This Sample02 comparison should **not** be interpreted as proof that BabelDOC is generally better than Docling.

The expanded BabelDOC results show major degradation on complex scientific/government layouts, especially for table structure and figures.

According to the project architecture, Docling is a built-in joint extraction backend that returns text/OCR, table structure, and picture regions through the project's native extraction path. BabelDOC, by contrast, required an external run plus a custom normalization adapter for this experiment.

A fair full-dataset comparison would require Docling predictions to be evaluated against the same final five reviewed references.

---

## 14. Comparison boundary for Marker and PaddleOCR PP-StructureV3

Marker and PaddleOCR PP-StructureV3 are other team extraction backends, but this report does **not** contain their same-reference five-document evaluation results.

Therefore this report does not claim that BabelDOC is more or less accurate than Marker or PaddleOCR.

The correct comparison procedure is:

```text
same PDFs
+ same reviewed references
+ same evaluator/protocol
= comparable reports
```

Until those reports exist, only implementation/capability differences should be discussed, not accuracy rankings.

---

## 15. Capability summary

| Capability | BabelDOC in this experiment |
|---|---|
| Native/selectable text extraction | Yes |
| Positioned text geometry | Yes |
| Paragraph/layout information | Yes |
| Table-region detection | Yes |
| Native project-ready rectangular table grid | No, not exposed in inspected intermediate |
| Adapter-based table reconstruction | Yes, but limited |
| Figure/image region information | Yes |
| Reliable semantic figure recall on complex public documents | No |
| Strong simple-document performance | Yes |
| Strong complex scientific table recovery | No |
| Strong complex government table recovery | No |
| Project top-left coordinates directly | No; adapter conversion required |
| Project IR directly | No; adapter/import required |
| Translation available in BabelDOC generally | Yes, but not evaluated here |
| Translation used in this experiment | No |
| Redaction/reconstruction evaluated | No |
| Intermediate JSON suitable for investigation | Yes |

---

## 16. Reference creation and review limitation

`Sample02.json` was the existing reviewed project reference.

Additional references were created for:

- `Sample03`
- `Sample04`
- `YOLOv3`
- `TrafficSF`

For this individual BabelDOC investigation, those four references were annotated and self-reviewed because an independent second-team-member review was not available before submission.

This differs from the project's preferred reference protocol.

Therefore, the four new references and their resulting scores should be treated as **provisional** until independently reviewed by the team.

If any reference is corrected later, every tool must be re-evaluated against the corrected shared reference set.

---

## 17. Dataset provenance

### Project samples

- `Sample02.pdf`
- `Sample03.pdf`
- `Sample04.pdf`

### Public documents added for broader testing

**YOLOv3**

- Title: *YOLOv3: An Incremental Improvement*
- Authors: Joseph Redmon and Ali Farhadi
- arXiv identifier: `1804.02767`

**TrafficSF**

- Title: *Results of the 2013-2014 National Roadside Survey of Alcohol and Drug Use by Drivers*
- Publisher: National Highway Traffic Safety Administration
- Report: `DOT HS 812 118`
- Date: February 2015

These additional documents broaden the experiment beyond one synthetic PDF, but five documents are still a pilot set rather than a comprehensive benchmark.

---

## 18. Reproducibility artifacts

Recommended submission artifacts:

```text
Document-Transformation/
├── babeldoc_adapter.py
├── BABELDOC_REPORT.md
├── references/
│   ├── Sample02.json
│   ├── Sample03.json
│   ├── Sample04.json
│   ├── YOLOv3.json
│   └── TrafficSF.json
├── experiments/
│   ├── babeldoc/
│   │   ├── Sample02.json
│   │   ├── Sample03.json
│   │   ├── Sample04.json
│   │   ├── YOLOv3.json
│   │   └── TrafficSF.json
│   └── babeldoc-raw/
│       ├── settings.json
│       ├── Sample02/
│       │   ├── paragraph_finder.json
│       │   └── normalized.json
│       ├── Sample03/
│       │   ├── paragraph_finder.json
│       │   └── normalized.json
│       ├── Sample04/
│       │   ├── paragraph_finder.json
│       │   └── normalized.json
│       ├── YOLOv3/
│       │   ├── paragraph_finder.json
│       │   └── normalized.json
│       └── TrafficSF/
│           ├── paragraph_finder.json
│           └── normalized.json
└── evaluation-results/
    ├── babeldoc.json
    ├── babeldoc.csv
    ├── babeldoc-sample03.json
    ├── babeldoc-sample03.csv
    ├── babeldoc-sample04.json
    ├── babeldoc-sample04.csv
    ├── babeldoc-yolov3.json
    ├── babeldoc-yolov3.csv
    ├── babeldoc-trafficsf.json
    ├── babeldoc-trafficsf.csv
    ├── babeldoc-five-documents.json
    └── babeldoc-five-documents.csv
```

Do **not** commit:

- `.venv/`
- `.venv-babeldoc/`
- real API keys
- model caches/weights
- temporary debug PDFs
- bulky `babeldoc-working-*` directories
- temporary `babeldoc-output-*` directories unless specifically requested by the team

Raw evidence needed for reproducibility should be preserved through the curated `experiments/babeldoc-raw/` structure.

---

## 19. Main limitations

1. BabelDOC did not expose a project-ready rectangular table-cell grid in the inspected intermediate representation.
2. The custom geometric reconstruction works on simple tables but degrades sharply on complex/merged tables.
3. Dense multi-column reading order caused high text errors on YOLOv3 and TrafficSF.
4. Semantic figure recall was poor on the two complex public documents.
5. Near-perfect matched figure IoU is not equivalent to high figure recall.
6. Table detection F1 of `1.0` does not imply correct table structure.
7. One TrafficSF detected table could not be reconstructed and became `0x0`.
8. Translation was disabled, so this report does not evaluate BabelDOC translation quality.
9. Redaction and document reconstruction are outside this milestone.
10. The four added references are self-reviewed/provisional until independent team review.
11. Five documents are insufficient to claim general superiority over another extraction backend.
12. Full cross-tool comparison requires all tools to be evaluated on the exact same reviewed references and evaluator fingerprint.

---

## 20. Conclusion

BabelDOC was straightforward to inspect through its intermediate JSON and performed strongly on simpler synthetic documents.

Its strongest result in this experiment was **region-level detection**, especially table-region detection and highly accurate geometry for figures that were successfully matched.

The main weakness was **structured recovery**. BabelDOC did not directly expose the rectangular table-cell representation required by the shared project IR, so the adapter had to infer rows/cells geometrically. That approach did not generalize well to the complex YOLOv3 and TrafficSF tables.

The experiment therefore shows that a backend can achieve strong region detection while still providing weak downstream structured extraction.

BabelDOC remains useful as an extraction candidate, but before adopting it as a general baseline the team should independently review the expanded references and run the same five-document evaluation for Docling, Marker, and PaddleOCR PP-StructureV3.
