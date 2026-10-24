# Milestone 1 implementation and first-run findings

## Implemented

- Separate Streamlit transformation workspace: `transform_app.py`.
- BabelDOC 0.6.4 subprocess runner with a local model endpoint, isolated run directories, source hash, elapsed time and failure reporting.
- Eleven experimental language choices; translation and unavailable PII-redaction controls are independent.
- Source/output page previews, PDF download and saved-run inspection.
- Existing extraction application remains unchanged.

## Initial integration experiment

Source: Sample02.pdf, SHA-256 `684082b44c8c554ac095b31ab6c70d326a4f73fc02bb62fd1adbe68c92dcb772`.

Model: existing local Ollama `qwen2.5:3b`, running on CPU. Direction: English to Korean. BabelDOC: 0.6.4. Elapsed time: 442 seconds. Output: one-page translated PDF that opens and retains the original page count. No redaction applied. Optional specialized table-text processing was not enabled; some table text was nevertheless translated by the ordinary pipeline.

This is a pipeline integration result, not a passing translation-quality result. Visual/text inspection found:

1. Title, several headings and much of the employee-summary paragraph remain English.
2. Mixed-language and inaccurate wording in the translated notes/caption.
3. Photo caption overlaps the bottom of the image.
4. BabelDOC logs include unchanged-translation and response-length mismatch fallbacks.

The table and image remain visible, but that alone does not establish layout fidelity.

## Verification

45 unit tests pass with the repository's inherited-permission temporary-directory helper in this Windows environment. Streamlit AppTest loaded without application exceptions; 11 language options, disabled redaction and disabled processing with no operation selected were checked. AppTest cleanup emitted environment-specific temporary-directory permission warnings. The local web server passed its health check. Browser automation could not attach to its tab, so visual UI review remains pending.

## Remaining Milestone 1 acceptance work

- Improve local-model prompt/response compatibility and evaluate model/context settings.
- Resolve incomplete translation and caption overlap on Sample02.
- Visually review the running interface and output alongside the source.
- Do not mark Milestone 1 quality acceptance complete until those issues are resolved.

DOCX, PPTX and redaction implementation belong to later milestones. Language availability is not evidence of validated translation quality.

## Smaller-model follow-up — 2026-10-02

At the user's request, continued with existing local Ollama `qwen2.5:3b`. No new model download. The 9B attempt ended with a Windows manifest replacement permission error and has been marked interrupted; it is not a completed comparison.

Run: `runs/transformation/20261002-161716-96f3d541`. Same Sample02 source and English-to-Korean direction. BabelDOC 0.6.4; native local Ollama transport; temperature 0, context 8192, output limit 4096. Generated in 125.2 seconds on this machine. This single run is not a controlled speed benchmark.

Changes:
- Single-paragraph translation avoids the JSON batch response format. Mixed rich-text styles within paragraphs are disabled by BabelDOC on this path; the UI exposes this limitation.
- Capability probes no longer send empty requests to the model.
- Caption guard uses source PDF image coordinates where BabelDOC does not expose figures. It shifts the caption below the photo without editing vendor code.
- Cache key distinguishes the new translation mode; manifest records the actual prompt and settings.

Poppler rendering and text inspection confirmed:
- One-page PDF opens, the table and photo remain visible, and the caption no longer overlaps the photo.
- No literal HTML tags or text spans outside the page were detected.
- A heading wraps behind the photo: the trailing `ID` is in extracted PDF text but hidden in the rendered image. One image/text overlap is flagged.
- Translation remains unacceptable: mixed-language words, inaccurate Korean, and the table email is changed from `minjun.kim@...` to `민준.김@...`. The original email elsewhere in the paragraph is retained. Absence of unchanged-prose flags does not establish correctness.

All 51 unit tests pass, including plain-path dispatch, capability probing, native request options, and caption geometry checks. PDF output and its review signals are retained locally. Nothing pushed to GitHub.

Milestone 1 remains **in progress**. Next acceptance work: protect exact identifiers per occurrence before translation, constrain headings above figures as well as captions below them, and improve/review Korean meaning using the smaller-model constraint. Do not use this output as an approved translation or redacted document.

## Technical restart — 2026-10-10

Latest retained run: `20261008-162031-deb39a23`, Sample02, English to Korean, Qwen3.5:9b. A read-only final-PDF integrity check found matching occurrence counts for all seven explicitly protected values and both email occurrences. This is not translation or visual acceptance. Earlier findings above describe historical runs.

Added final-output integrity diagnostics to future quality reports and a standalone command:

```powershell
python -m transformation.validation.integrity runs/transformation/<run-id>
```

The checker detects missing/extra occurrences, absent source values and email count changes. PDF text extraction can introduce ambiguity; findings require review. It does not rerun the model or modify existing run evidence unless a new output report path is explicitly supplied. Nine new integrity tests and 19 existing transformation/protection tests passed.

Next user-run test: Sample03, English to Korean if its source is English, same model/settings. Clear Sample02-specific context and protected names before supplying values relevant to the new document. Inspect table content, figures, completeness and page layout; repeat with Sample04 after resolving failures. Redaction and Office support remain later milestones.
