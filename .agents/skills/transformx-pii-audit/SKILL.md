---
name: transformx-pii-audit
description: "Audit TransformX PII detection, final-artifact redaction and evaluation coverage using exact-span annotations and independent output checks. Use for document privacy behavior, not general web application security review."
---

# TransformX PII audit

Read [the privacy implementation and evaluation map](references/privacy.md).
Audit detection and the delivered artifact as separate stages.

## Establish the evidence

- Freeze selected categories, source hash, source IR, workflow, model and policy.
  The default PII model is `qwen3.5:9b`; email/phone patterns and model-assisted
  semantic categories use different paths.
- Build or inspect human-reviewed exact source spans by region and category.
  Verify each `text[start:end] == value`. Count PII in missed image/text regions
  through original-page review; extracted-text detection metrics exclude it.
- Inspect `pii-findings.json` and compare exact and overlap matches separately,
  by category and PDF. Retain TP/FP/FN and denominators, including failures and
  empty-category cases. Do not rename F1 as overall document privacy accuracy.

## Check the artifact

Confirm the redaction implementation removes selected content rather than merely
painting a visible box. Inspect extractable/searchable text, rendered pages,
metadata, embedded files and uninspected content identified by existing checks.
Use fresh rendered-output OCR when requested and available; inspect uncertain
regions and OCR omissions rather than interpreting no detection as no leakage.
Repeated identifiers, split text runs and PII in tables/images need specific
coverage. Verify that combined mode redacts before translation and does not
preserve selected PII just because a value is translation-protected.

Report detection precision/recall/F1, known residual PII and observed
over-redactions separately. Over-redaction/leakage percentages require a defined,
reviewed denominator; record observations if the evaluator lacks one. No current
automatic check proves complete removal of unknown PII or all PDF hidden content.

## Validation and boundaries

Use synthetic PII fixtures for regression tests. Keep source PII, credentials,
findings and private logs out of public reports or external APIs unless that
transfer is authorized. Save new candidate artifacts without altering baseline
PDFs or reviewer provenance. Structural validation or a generated result is not
privacy approval; keep gaps and inaccessible content explicit.
