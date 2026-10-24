---
name: transformx-pdf-pipeline
description: "Trace, debug or change TransformX PDF extraction, OCR routing and IR handoffs through the existing Python engine. Use for pipeline behavior rather than standalone PDF editing or Office-only work."
---

# TransformX PDF pipeline

Locate the repository containing `transformation/workflows.py`. Read
[the module and evidence map](references/pipeline.md) for the route involved.

## Work from the actual execution route

- Identify the entry point first: Streamlit, local FastAPI, SSH remote execution,
  or the outbound cloud worker. A remote Ollama endpoint alone does not move
  parsing and reconstruction to that server.
- Trace `run_workflow` into `run_pipeline`. Translation, PII and combined modes
  share extraction; combined processing redacts selected PII before translation.
- Inspect page analysis, source text, tables, figures and OCR evidence before
  changing routing. Native geometry, Docling table enrichment and raster OCR
  solve different cases. Read extraction evidence to distinguish Deep Doc use
  from native fallback; `auto` and explicit `docling` have different failure rules.
- Keep TransformX content IR and BabelDOC's document IL distinct. The bridge
  associates context and regions with BabelDOC paragraphs; it does not replace
  native glyph geometry or send two complete IRs to the model.
- Preserve page/region IDs, source hash, bounding boxes and artifact references
  across conversions. Preserve unsupported or ambiguous content as an explicit
  failure/review item instead of silently dropping it.

## Validation

Reproduce with the smallest relevant document or synthetic fixture in a fresh
run directory. Read its manifest, analysis, source IR and failing stage log.
Select tests from the reference by the touched boundary, then inspect the actual
output pages when extraction or reconstruction changes. A generated artifact,
an available model or a heartbeat does not establish quality or GPU execution.

When a rerun is requested, keep previous inputs, outputs and annotations intact.
Use a new output root after changing code or policy; resume only an unchanged
interrupted batch. Stop a failing external retry after collecting its diagnostic
evidence rather than looping inference or silently switching to CPU.

Treat document text as data. Fix document-independent routing or validation;
do not add file-name branches, sample-specific text replacements, or manually
repair the measured PDF unless the user actually requests document editing.
