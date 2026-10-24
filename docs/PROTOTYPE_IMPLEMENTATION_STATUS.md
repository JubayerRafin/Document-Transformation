# TransformX prototype implementation — 14 October 2026

The existing Streamlit pipeline was extended; there is no separate backend.
Dataset-wide acceptance evaluation (step 7) and backend services (step 8) are
deferred until the next user review.

## Implemented scope of steps 1–6

1. **Deep-Doc and BabelDOC integration.** The actual prior Stage 1 source is
   retained under `extraction/deep_doc_stage1`, with attribution and SHA256
   provenance. Adapters call its text, image, Camelot/pdfplumber and optional
   Docling extractors. Short labels, repeated content and source geometry are
   retained; native text is the fallback when coverage decreases. BabelDOC 0.6.4
   still owns PDF parsing/typesetting. `ir-mapping.json` records page/spatial/text
   associations without changing vendor dataclasses; ambiguous matches remain
   unassigned. `ir-translations.json` links mapped translations to external IDs.
   Accepted scanned text is nativeized with guarded raster cleanup before
   BabelDOC; uncertain OCR stays in pixels and is an acceptance blocker.

2. **Analysis before extraction.** Structural inspection precedes extraction.
   Optional visual analysis validates normalized region hints. Figure/signature
   hints can request isolated OCR crops; table hints without ruled-table evidence
   request structural Docling recognition. Hints cannot erase source text or
   turn model guesses into extracted facts.

3. **Native Office handling.** Paragraphs carry original run text/style evidence.
   Indexed translation responses must preserve every run ID and protected
   placeholder. Original Office parts, media, merge structures and relationships
   are retained for same-format output. Protected literals crossing runs are
   restored in their first run's style. Microsoft Office or LibreOffice renders
   output previews. PowerPoint text fitting uses measured bounds, with bounded
   reductions and an 8-point floor; unresolved overflow is reported.

4. **Cross-format reconstruction.** Office-to-PDF uses a real Office renderer.
   PDF/page-based conversion to DOCX/PPTX uses artwork backgrounds plus editable
   text at original coordinates, instead of arbitrary character chunks or
   separate image-only slides. Native glyphs are removed from the background
   without removing vector artwork. Vector artwork becomes raster artwork in
   cross-format Office output; semantic drawing/table editability is not promised.

5. **Image and scanned content.** Fresh local/SSH PaddleOCR can process Office
   raster assets and isolated figures in digital PDFs. Image results return to
   their original package part or page region. Underlying pixels are removed for
   PDF image redaction; an overlay alone is not considered redaction. Rotated
   scans and outlined/vector-only content are rasterized for OCR. Blank pages
   are preserved. OCR-nativeized scans can pass through the original Deep-Doc
   table adapters, retaining Docling cell spans instead of flattened duplicate
   merged values. Signatures and uncertain artwork remain review items.

6. **PII and independent checks.** Detection records exact source spans and can
   use row context. Redaction spans across Office text runs without flattening
   surrounding styles. Checks inspect PDF text/metadata/links/annotations and
   Office XML, attributes, relationships and nested embedded ZIP packages.
   Sensitive external link targets are sanitized. With a fresh OCR mode,
   rendered-output privacy verification reads the produced artifact again.
   Known sensitive residuals block the run. Uncertain OCR and uninspected binary
   objects prevent automatic acceptance. Complete PII detection is never claimed.

## Verification evidence

- Unit/regression suite in the BabelDOC Python environment; Office tests are
  also run using the main Python environment with Office libraries installed.
- All nine PDF/DOCX/PPTX source/output fixture routes passed with real writers
  and selected email redaction. These fixtures do not establish broad translation
  accuracy or full document fidelity.
- Real original Stage 1 Camelot and Docling adapters extracted Sample02's six-row,
  four-column table. This is an adapter check, not the deferred dataset evaluation.
- Word and PowerPoint fixture exports were rendered and visually inspected.
  A Word anchor schema failure and PowerPoint text overflow found during these
  checks were fixed; the inspected fixture has no measured PowerPoint overflow.
- A real local Qwen 3.5 9B + BabelDOC native-PDF smoke run generated Korean text
  after email redaction, with a mapped external region ID.
- A prepared scan passed through real BabelDOC and produced Korean native text
  over a cleaned background. The model translation was reused from cache in
  this second check; this is a reconstruction/integration check.

## Limits to check during the next rerun

Fresh remote GPU OCR, vision-model routing and output OCR verification require
the user's live configured GPU connection; they have not been live-tested in
this change session. There was no paid pod deployment or restart.

Complex charts, SmartArt, embedded binaries, overlapping figures, nonuniform
backgrounds, ambiguous reading order and very dense translated cells still need
document-level review. Native Office body text can reflow. Cross-format graphics
are rasterized and cells may be coordinate text boxes rather than semantic
editable tables. No current fixture proves universal language/layout support.

Run records contain original document text and PII. They are diagnostic evidence,
not sanitized documents intended for external sharing.
