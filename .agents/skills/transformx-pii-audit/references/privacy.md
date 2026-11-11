# Privacy modules and scoring limits

| Task | Module |
|---|---|
| Categories, exact-span model proposals and patterns | `transformation/validation/privacy.py`: `detect`, `mask`, `mask_runs` |
| Native/scanned PDF redaction | `transformation/pipeline.py`: `redact_pdf` |
| Additional redaction helpers | `transformation/validation/redaction.py` |
| User-selected page areas | `transformation/validation/manual_redaction.py`; shared editor `cloud/ui/redaction-review.*`; API `cloud/src/manual-redaction.mjs` |
| Independent text/package checks | `transformation/validation/output_validation.py`: `inspect_output` |
| Fresh rendered-output OCR verification | `transformation/validation/privacy_verification.py`: `verify` |
| Mode selection, redaction-before-translation | `transformation/workflows.py`, `pipeline.py` |
| Exact/overlap/per-category detection metrics | `tools/evaluation/detailed.py`: `score_review`, `overlapping_scores`; `dataset.py`: `pii_scores` |

`privacy.py` requires model-proposed values to be exact contiguous source
substrings. Email and phone detection uses patterns. Under selected `Identifiers`,
explicit credential fields (`API Key:`, `Access Token:`, authentication/refresh
tokens and client secrets) also use exact-source label/value patterns; the model
prompt defines credentials as identifiers. Other semantic detection still needs
assistance. Uncertain OCR must not exempt a labelled credential from detection:
the value-only candidate requires value/word geometry and blocks an unresolved
selected span rather than erasing the complete field.
This is not a complete secret scanner or proof of identifier recall. See
`tests/test_pii_credentials.py` for selected-category, exact-span, model-omission,
scanned-pixel and native-text preservation tests.
Neighboring table context is background only: a proposal
must occur in the current region's `text`, not merely its row/header context.
Invalid JSON, categories or unsupported values receive one validation retry with
context withheld. A second invalid response fails closed; no invalid proposal is
accepted and no incomplete semantic result silently falls back to patterns.
`tests/test_privacy_proposals.py` covers context leakage, exact offsets, repeated
values, invalid mixed lists, and legitimate positive proposals. This bounded
retry does not prove detection recall or authorize empty human ground truth.
The local value-only candidate delegates `redact_pdf` to
`validation/value_redaction.py`: actual native glyph boxes with layout-whitespace
matching, or verified OCR word/value boxes. It removes metadata/embedded files,
reopens the PDF and checks both residual values and surrounding native glyphs.
It replaces the previous whole-OCR-line fallback with explicit localization
review when a label cannot be separated reliably. See
`docs/VALUE_ONLY_REDACTION.md` and `output/value-redaction-20261105/`; local code
does not imply deployment or fresh six-document/model approval.
A selected value is propagated to every matching source
region/page, even when the model proposed only one repeated occurrence. Native
glyph matching covers PDF pages; scanned occurrences need verified value/word
geometry. This is redaction coverage propagation, not additional model detection
TPs. Supplemental native pattern findings are recorded separately in
`pii_source_regions`; include their source evidence when scoring detection.
Do not infer a new recall score from saved-detection redaction replay.
`tests/test_pii_occurrences.py`
covers repeated raster/native values and pixel removal. Phone patterns exclude
validated ISO date/time spans, including a lost OCR space between date and time;
they must still detect actual phones beside timestamps and must not blanket-
exempt invalid dates or arbitrary digit strings.

Inspect `source-ir.json`, `pii-findings.json`, `redaction-audit.json`,
`independent-validation.json`, `privacy-verification.json` when produced, and
`manifest.json`. The fresh OCR verifier itself states that OCR can still miss
content. `docs/PII_HANDOFF.txt` and `docs/DETAILED_EVALUATION.md` give existing
annotation and runner conventions; verify them against current code.

```powershell
python -m tools.evaluation.detailed reviews --config <config.json> --phase pii
python -m tools.evaluation.detailed evaluate --config <config.json> --phase pii
python -m tools.evaluation.dataset report --config <config.json> --phase pii
```

Those commands write reports/review bundles. Review creation does not perform a
human annotation. `dataset run --phase pii` actually processes documents and can
consume GPU credits; it is not an offline audit command.

Useful tests: `tests/test_transformation.py`, `test_document_quality_extensions.py`,
`test_product_extensions.py`, `test_detailed_evaluation.py`, `test_dataset_evaluation.py`
and `test_separate_workflows.py`. Inspect the specific test cases before choosing
them, because some Office routes need native rendering dependencies.

Manual redaction is a post-processing child version, separate from automatic
PII detection and combined-mode ordering. Its normalized areas refer to the
displayed rotated crop box of a SHA-256-bound finished PDF. Check actual selected
text/pixel removal, cropped rotations and form appearances with
`tests/test_manual_redaction.py`. Comments, links, attachments and metadata are
removed; forms are flattened. Intersecting vectors can change outside the marked
area. Never transfer automatic detection scores or a parent's review approval to
this new artifact. See `docs/MANUAL_REDACTION.md` for contracts and limitations.
