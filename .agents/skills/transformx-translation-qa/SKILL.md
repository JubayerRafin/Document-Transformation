---
name: transformx-translation-qa
description: "Evaluate TransformX translations and protected-value regressions using saved runs, reviewed region alignments and optional COMET metrics. Use for translation quality rather than PII leakage or layout-only scoring."
---

# TransformX translation QA

Read [translation evidence and commands](references/translation.md) before
scoring a run. Keep execution status separate from semantic acceptance.

## Review workflow

1. Identify the config, source/output hashes, language, model, policy and run
   manifest. Keep failed and missing documents in the report denominator.
2. Check original page coverage before trusting extracted regions. Inspect
   paragraphs, titles, table headings/cells and text inside figures. Report
   missed source regions separately from recognition and translation errors.
3. Use existing ground-truth/review bundles. Validate region IDs, extracted text
   and output hashes before reusing annotations. Do not edit the translated
   artifact or use its wording as an independent reference.
4. Record accuracy, terminology, fluency, omissions, additions and untranslated
   content with the existing minor/major/critical taxonomy. Proper names and
   identifiers may legitimately remain unchanged; unchanged-text signals need
   source/context review rather than automatic translation.
5. Check protected literal counts, localized numbers, contact/value bindings,
   terminology consistency and rejected responses. Do not bypass protection
   checks or insert sample-specific corrections to make a run pass.
6. Run deterministic scoring first. Use COMETKiwi only for valid source/output
   pairs; reference-based COMET also needs independent reference translations.
   Report alignment exclusions and coverage. Neither model score is percentage
   accuracy or human approval.

## Validation and delivery

For code changes, run the relevant guard/alignment tests and compare a fresh run
against the preserved baseline. Compare each PDF under the same settings;
separate runtime changes from quality findings. Treat existing error categories
as MQM-style annotations, not a calibrated official MQM score.

Do not mark `source_reviewed`, `translation_reviewed`, `visual_reviewed` or
`human_accepted` on someone's behalf without their actual review evidence.
Report reviewed/unreviewed counts and major blockers alongside scores. Optional
metric downloads and GPU evaluation are separate from an offline report request.
