# Six-PDF translation and PII evaluation

Run from the repository directory. The production runner, OCR cache, and model
adapters are the existing TransformX code. References stay outside inference.
Translation uses TranslateGemma 12B; the later independent PII phase starts from
original PDFs and uses Qwen 3.5 9B. Visual-model analysis is off for this baseline.

## 1. Run translation

Keep the SSH tunnel open. Use the fresh evaluation configuration, which preserves
the older GPU baseline folders:

```powershell
python -m tools.evaluation.dataset preflight --config configs/evaluation.local.json --phase translation
python -m tools.evaluation.dataset run --config configs/evaluation.local.json --phase translation
```

The runner attempts all six PDFs, records failed jobs, checks source hashes and
GPU placement, and creates basic and detailed reports plus review bundles.
If GPU verification fails, fix the connection and use the same command with
`--resume`. A code/policy change needs a new output_root, not an overwritten run.
OCR inference evidence is shared through the validated OCR cache; extraction and
IR assembly still execute in each branch. Do not claim full extraction is cached.

## 2. Review source and translation

Under `runs/dataset-evaluation/detailed-baseline-v1`:

- `ground-truth/SampleXX.ko.json`: per-region annotation templates.
- `review-bundles/SampleXX/ko/`: source/output PNGs and aligned region CSV.
- `translation/detailed/report.html`: readable complete document inventory.
- `translation/detailed/report.json`: metrics, blockers, and review details.

For each region, verify `source_reference` against the original page and set
`source_reviewed=true`. Never accept the OCR text as ground truth without checking.
Fill `reference_translation` with a bilingual-reviewed reference when available.
Review the saved `translation`; do not edit it to improve the measured output.
Mark `translation_reviewed=true` and annotate errors, for example:

```json
{"category":"terminology","severity":"major","note":"Employee job title translated as document title."}
```

Categories: accuracy, terminology, fluency, omission, addition, untranslated.
Severities: minor, major, critical. These are MQM-style error annotations, not an
official calibrated MQM score. Unreviewed empty error lists do not imply success.
Check auto-suggested protected literals against the verified source. Review page
coverage and add omitted text/entities to `missing_source_regions` (include page,
bbox, text, category where relevant). A region-level CER can be excellent while
an entire missed region is absent; coverage is therefore reported separately.
Set `source_coverage_reviewed` and `visual_reviewed` only after inspecting every
page. Add clipping, overlaps, missing figures, style losses and table defects to
`layout_issues`. Only explicitly set `human_accepted` when review is complete.
Major/critical errors, missing source regions, preservation failures, OCR blockers
or geometry blockers prevent acceptance.

```powershell
python -m tools.evaluation.detailed evaluate --config configs/evaluation.local.json --phase translation
```

CER/WER use deterministic Levenshtein alignment with whitespace normalization
only. Case, punctuation and identifiers remain significant. Counts and rates are
reported per region and aggregated by reference characters/words. Empty reviewed
source with extra output has insertion counts and an undefined rate, not infinity
or a fabricated perfect score. No JiWER installation is required for these counts.

## 3. Optional neural translation scoring

Use a separate Python 3.11 evaluation environment with project requirements and
`requirements-evaluation.txt`; do not install COMET into BabelDOC's environment.
COMET is lazy-loaded only when explicitly requested. Its dependencies and weights
are not installed by preparing or running the production batch.

```powershell
python -m tools.evaluation.detailed evaluate --config configs/evaluation.local.json --phase translation --comet-model Unbabel/wmt22-cometkiwi-da
```

This uses verified source text plus uniquely mapped translations. For reviewed
reference translations use `--comet-model Unbabel/wmt22-comet-da` instead.
CPU is the default; `--gpus 1` refers to the machine executing the evaluator, not
the remote Ollama tunnel. Run the evaluator on a GPU host separately if needed.
Model downloads may require provider terms/authentication. Scores are aligned
per region and are not percentage accuracy or automatic human approval. Regions
without verified source, unique mapping or required references remain unscored.
Check model language coverage before expanding beyond English/Korean.

## 4. Run and evaluate PII afterward

On the pod, install the PII model using the prepared script's `pii` phase. Then:

```powershell
python -m tools.evaluation.dataset preflight --config configs/evaluation.local.json --phase pii
python -m tools.evaluation.dataset run --config configs/evaluation.local.json --phase pii
```

Fill `ground-truth/SampleXX.pii.json`. Each region's `pii` list contains exact
zero-based Python string offsets into `extracted_text`:

```json
{"start":8,"end":12,"value":"Maya","category":"Names"}
```

Choose only configured categories; unselected categories are outside this test's
scope. Mark `pii_ground_truth_reviewed=true` only after every source region is
reviewed, including documents with no selected PII. The evaluator reports exact
and one-to-one overlap span precision/recall/F1, plus exact per-category scores.
These measure detection on extracted text. OCR-missed entities are recorded in
`missing_source_regions` and block end-to-end acceptance; span F1 does not measure
their recall. Compare original pages with redacted pages, inspect searchable text,
images and hidden content, and record `privacy_residuals` and `over_redactions`.
Set `privacy_reviewed=true` only after that original-document privacy inspection.
No automated text-only check proves sensitive pixels were removed.

```powershell
python -m tools.evaluation.detailed evaluate --config configs/evaluation.local.json --phase pii
```

Preserve all failed runs and notes. Present per-document and per-category results,
not just an average. Human ground truth and actual GPU dataset results are still
required; preparing this evaluator does not establish product acceptance.
