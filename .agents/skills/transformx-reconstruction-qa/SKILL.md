---
name: transformx-reconstruction-qa
description: "Review TransformX reconstructed PDFs for layout, table, figure and text-fitting regressions using saved artifacts, geometric matching and rendered comparisons. Use for reconstruction fidelity, not standalone translation scoring."
---

# TransformX reconstruction QA

Read [reconstruction evidence and evaluation commands](references/layout.md).
Use the original PDF, reconstructed PDF and their recorded source/output hashes.

## Inspect the failure boundary

- Establish whether output came through BabelDOC, scanned-page reconstruction,
  figure OCR slots or an Office export. Follow the selected renderer rather than
  treating every fitting issue as a BabelDOC defect.
- Inspect fitting reports and the source/transformed IR. Separate an extraction
  omission, wrong translation, wrong region association and rendering failure.
- Render corresponding source/output pages at identical DPI. Check table titles,
  rows/columns, clipping, overlaps, font visibility, reading order and page count.
  Examine photographs/figures directly: intact image geometry can still hide an
  image underneath a reconstructed text region.
- Check native PDF text and the visible rendering together. Source-language text
  embedded in figures is not the same as an untranslated native paragraph.

## Measure with the existing methods

Retain the earlier preservation assessment and add standard metrics separately.
Use the current evaluator's one-to-one geometry matching and IoU thresholds.
Report class/count/coverage and unmatched regions, not just the mean overlap of
matched boxes. Full-page SSIM measures appearance and naturally penalizes intended
translation changes. It cannot certify translation quality or privacy.

## Validate changes

Choose fitting/geometry/renderer tests from the reference, then compare a fresh
candidate with the frozen baseline PDF by PDF. Include digital, scanned and mixed
content cases implicated by the change. Reject clipping/overlap fixes that erase
figures, silently omit text or hide failure by shrinking beyond configured limits.
Change generalized fitting/routing behavior, not a particular sample's text or
coordinates. Preserve unapproved output status until the actual visual findings
and acceptance checks support a different result.
