# Value-only PDF redaction

The local candidate removes sensitive values while preserving field labels,
titles, neighboring text and table rules. It does not establish detector recall
or whole-document privacy approval. Candidate checks are recorded under
`output/value-redaction-20261105/`; deployment is a separate step.

## Native PDFs

`transformation/validation/value_redaction.py` matches selected source literals
across style runs and layout whitespace, retaining exact source character
offsets. Punctuation and letter case are not rewritten. Token boundaries prevent
propagating a short value into a longer identifier or word. Each matched line
receives boxes derived from actual PDF glyph geometry, not a whole paragraph
rectangle. Native PDF blocks supplement deterministic email/phone detection in
`pipeline.py`; these are stored as `pii_source_regions` and do not become duplicate
translation input. Supplemental source text is revalidated against the PDF bytes.

The detector retains wrapped email/phone substrings and exact spans. Context and
Office-run masking share the same source-whitespace matcher, so a wrapped value
is not accidentally reintroduced through unmasked context. Table/vector rules
are preserved. Selected image pixels are removed where a redaction intersects
an image. Export uses a new garbage-collected PDF and removes metadata and
attachments. Reopened text is checked for the selected values; surrounding
printable native glyphs must survive, allowing only 0.05-point serialization
rounding in unchanged origins.

Semantic proposals are instructed to return values rather than field labels.
Obvious whole-field proposals receive the existing bounded validation retry
rather than being silently trimmed. Colon-bearing identifiers remain valid
values; this guard is not a complete document-label classifier.

## Scanned fields

An OCR region containing only the selected value can use its existing value
box. Partial-line values require verified source-word boxes. The bounded English
OCR route can recognize short field-like crops with two distinct resident
recognizers, using detected whitespace boundaries rather than estimated
character widths. Both word transcripts must agree exactly with the original
line, each word score must be at least 0.90, and the source quadrilateral must be
axis-aligned. Word localization adds at most 16 predictor calls per eligible
region; it does not change the transcription or recognition decision. Ordinary
prose is not word-localized. Its runtime overhead requires GPU measurement.

`paddle_handoff.py` verifies those candidates and maps their measured source
crop boundaries to page coordinates. Unsupported languages, long/skewed crops,
disagreement and partial-word values do not receive fabricated word boxes.
Missing geometry raises `RedactionLocalizationError`; the pipeline writes
`redaction-review-required.json` and does not release an incompletely redacted
artifact. Old saved OCR without word evidence may therefore need a fresh OCR
pass. The OCR cache fingerprint includes the changed recognizer source.

This deliberately replaces the previous whole-OCR-line fallback. It is a
fail-closed behavior change; it can require review on documents that previously
completed by erasing a complete field. It is not a new manual-review job state
or a promise that every scan can be localized automatically.

## Verification

- `tests/test_value_redaction.py`: wrapped and styled values, labels, table rules,
  rotations/crops, exact source evidence, word agreement and review-only cases.
- `tests/test_pii_credentials.py` and `test_pii_occurrences.py`: actual raster
  removal, label-pixel preservation and repeated values.
- Existing PII/proposal, workflow, manual-redaction and worker tests cover shared
  contracts. Translation-only output is not modified by PDF redaction code.
- The saved six-document check reuses historical detections; it does not measure
  fresh detector precision/recall, perform translation, or confer human approval.

Keep all historical artifacts and annotations. Compare a fresh candidate run
separately for each PDF and mode before releasing the candidate to users.
